"""Tests for screened universes: candidates found by rule instead of listed."""

import json
import sys
import types

import pytest

from indexforge import SnapshotConnector, SpecError, build, cli
from indexforge.core.types import Currency
from indexforge.data.connectors.yahoo import YahooFinanceConnector
from indexforge.data.screening import yahoo_sector
from indexforge.spec import validate_spec
from tests.test_spec import FakeConnector, company

AS_OF = "2026-01-15"


class ScreeningConnector(FakeConnector):
    """A fake market whose screen returns every company it knows about."""

    def __init__(self, constituents, fx=None):
        super().__init__(constituents, fx)
        self.screens = []

    def screen(self, countries, sectors=None, min_market_cap=None, currency="USD"):
        self.screens.append((countries, sectors, min_market_cap, currency))
        return sorted(c.ticker for c in self._constituents)


@pytest.fixture
def market():
    return ScreeningConnector(
        [
            company("GER1", 300e9),
            company("GER2", 50e9),
            company("GER3", 20e9, sector="Industrials"),
            company("SMALL", 0.1e9),
            # Listed in Germany, based elsewhere: screens return these too.
            company("USXL", 4000e9, country="United States"),
            company("BANK", 80e9, sector="Financial Services"),
        ]
    )


@pytest.fixture
def spec():
    return {
        "name": "German Tech",
        "identifier": "DETECH",
        "currency": "EUR",
        "universe": {
            "screen": True,
            "countries": ["Germany"],
            "sectors": ["Technology", "Industrials"],
            "min_market_cap": 1_000_000_000,
        },
        "selection": {"rank_by": "MARKET_CAP", "top": 2},
        "weighting": {"method": "equal_weight"},
    }


class TestSpec:
    def test_screen_spec_needs_no_tickers(self, spec):
        clean = validate_spec(spec)
        assert clean["universe"]["screen"] is True
        assert "tickers" not in clean["universe"]

    def test_screen_and_tickers_cannot_be_combined(self, spec):
        spec["universe"]["tickers"] = ["SAP.DE"]
        with pytest.raises(SpecError, match="not both"):
            validate_spec(spec)

    @pytest.mark.parametrize("missing", ["countries", "min_market_cap"])
    def test_screen_must_be_bounded(self, spec, missing):
        del spec["universe"][missing]
        with pytest.raises(SpecError, match=missing):
            validate_spec(spec)

    def test_screen_must_be_a_boolean(self, spec):
        spec["universe"]["screen"] = "yes"
        with pytest.raises(SpecError, match="true or false"):
            validate_spec(spec)


class TestBuild:
    def test_rules_alone_decide_the_members(self, spec, market):
        result = build(spec, as_of=AS_OF, connector=market)

        assert [c.ticker for c in result.constituents] == ["GER1", "GER2"]
        assert market.screens == [(["Germany"], ["Technology", "Industrials"], 1e9, "EUR")]
        assert result.exclusions == {
            "BANK": "sector Financial Services is not in the universe",
            "GER3": "ranked outside the selection",
            "SMALL": "market cap is below the minimum",
            "USXL": "country United States is not in the universe",
        }

    def test_replay_reuses_the_recorded_candidates(self, spec, market, tmp_path):
        live = build(spec, as_of=AS_OF, connector=market)
        path = tmp_path / "data.json"
        path.write_text(json.dumps(live.snapshot))

        replayed = build(spec, as_of=AS_OF, connector=SnapshotConnector.load(path))

        assert replayed.to_dict()["constituents"] == live.to_dict()["constituents"]
        assert replayed.exclusions == live.exclusions

    def test_replay_refuses_a_snapshot_recorded_for_other_rules(self, spec, market):
        snapshot = build(spec, as_of=AS_OF, connector=market).snapshot
        spec["universe"]["countries"] = ["France"]
        with pytest.raises(ValueError, match="different universe rules"):
            build(spec, as_of=AS_OF, connector=SnapshotConnector(snapshot))

    def test_snapshot_of_listed_tickers_cannot_serve_a_screen(self, spec, market):
        listed = dict(spec, universe={"tickers": ["GER1", "GER2"]})
        snapshot = build(listed, as_of=AS_OF, connector=market).snapshot
        with pytest.raises(ValueError, match="holds no screen"):
            build(spec, as_of=AS_OF, connector=SnapshotConnector(snapshot))

    def test_source_that_cannot_screen_says_so(self, spec):
        with pytest.raises(NotImplementedError, match="cannot search for companies"):
            build(spec, as_of=AS_OF, connector=FakeConnector([]))

    def test_empty_screen_gives_an_empty_index(self, spec):
        result = build(spec, as_of=AS_OF, connector=ScreeningConnector([]))
        assert result.constituents == []
        assert result.exclusions == {}


class TestCli:
    def test_many_exclusions_are_summarised_by_reason(self, spec, tmp_path, monkeypatch, capsys):
        crowd = [company(f"US{i:03d}", 1e9 + i, country="United States") for i in range(40)]
        market = ScreeningConnector([company("GER1", 300e9), *crowd])
        monkeypatch.setattr(cli, "_live_connector", lambda source: market)
        path = tmp_path / "s.json"
        path.write_text(json.dumps(spec))

        assert cli.main(["build", str(path)]) == 0
        out = capsys.readouterr().out
        assert "Left out: 40 candidates" in out
        assert "40  country United States is not in the universe" in out
        assert "US001:" not in out

    def test_validate_describes_a_screen(self, spec, tmp_path, capsys):
        path = tmp_path / "s.json"
        path.write_text(json.dumps(spec))
        assert cli.main(["validate", str(path)]) == 0
        assert "screens 1 countries" in capsys.readouterr().out

    def test_unscreenable_source_is_a_clean_error(self, spec, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(cli, "_live_connector", lambda source: FakeConnector([]))
        path = tmp_path / "s.json"
        path.write_text(json.dumps(spec))
        assert cli.main(["build", str(path)]) == 1
        assert "cannot search for companies" in capsys.readouterr().err


class TestYahooScreen:
    """The Yahoo connector's screen, against a stand-in for the yfinance module."""

    @pytest.fixture
    def yahoo(self, monkeypatch):
        calls = []

        class Query:
            def __init__(self, operator, operands):
                self.operator, self.operands = operator, operands

            def flat(self):
                if self.operator in ("and", "or"):
                    return [self.operator, *[q.flat() for q in self.operands]]
                return [self.operator, *self.operands]

        quotes = [
            {"symbol": "AAA.DE", "longName": "Alpha AG", "averageDailyVolume3Month": 10},
            {"symbol": "AAA2.DE", "longName": "Alpha AG", "averageDailyVolume3Month": 500},
            {"symbol": "BBB.DE", "longName": "Beta SE", "averageDailyVolume3Month": 5},
            {"symbol": "FUND.DE", "longName": "A Fund", "quoteType": "ETF"},
        ]
        for quote in quotes:
            quote.setdefault("quoteType", "EQUITY")

        def screen(query, size, offset, sortField, sortAsc):
            calls.append({"query": query.flat(), "size": size, "offset": offset})
            return {"quotes": quotes[offset : offset + size], "total": len(quotes)}

        fake = types.ModuleType("yfinance")
        fake.EquityQuery = Query
        fake.screen = screen
        monkeypatch.setitem(sys.modules, "yfinance", fake)

        connector = YahooFinanceConnector()
        monkeypatch.setattr(connector, "get_fx_rate", lambda base, quote, as_of=None: 1.0)
        return connector, calls

    def test_one_ticker_per_company_most_traded_line_wins(self, yahoo):
        connector, _ = yahoo
        assert connector.screen(["Germany"]) == ["AAA2.DE", "BBB.DE"]

    def test_query_carries_region_exchange_sector_and_a_loose_size_floor(self, yahoo):
        connector, calls = yahoo
        connector.screen(["Germany"], ["Information Technology"], 1_000_000_000, "EUR")
        assert calls[0]["query"] == [
            "and",
            ["eq", "region", "de"],
            ["eq", "exchange", "GER"],
            ["eq", "sector", "Technology"],
            ["gt", "intradaymarketcap", 500_000_000.0],
        ]

    def test_unsupported_country_is_rejected(self, yahoo):
        connector, _ = yahoo
        with pytest.raises(ValueError, match="Atlantis"):
            connector.screen(["Atlantis"])

    def test_unknown_sector_is_rejected(self, yahoo):
        connector, _ = yahoo
        with pytest.raises(ValueError, match="Wizardry"):
            connector.screen(["Germany"], ["Wizardry"])


def test_yahoo_sector_accepts_either_naming_scheme():
    assert yahoo_sector("Health Care") == yahoo_sector("Healthcare") == "Healthcare"
    assert yahoo_sector("FINANCIALS") == "Financial Services"
    assert yahoo_sector("Wizardry") is None


def test_company_helper_defaults():
    assert company("X", 1.0).currency == Currency.EUR
