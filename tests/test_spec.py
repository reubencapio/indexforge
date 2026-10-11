"""Tests for index specs: validation, building, exclusion reasons and replay."""

import json

import pytest

from indexforge import SnapshotConnector, SpecError, build, index_from_spec, load_spec
from indexforge.core.constituent import Constituent
from indexforge.core.types import Currency
from indexforge.data.connectors.base import DataConnector
from indexforge.spec import dump_spec, validate_spec

AS_OF = "2026-01-15"


class FakeConnector(DataConnector):
    """Fixed data and fixed exchange rates; counts how often it is asked."""

    def __init__(self, constituents, fx=None):
        self._constituents = constituents
        self._fx = fx or {}
        self.calls = 0

    def get_prices(self, tickers, start_date, end_date):  # pragma: no cover - unused
        raise NotImplementedError

    def get_constituent_data(self, tickers, as_of_date=None):
        self.calls += 1
        # Fresh copies: the index converts market caps in place.
        return [
            Constituent.from_dict(c.to_dict()) for c in self._constituents if c.ticker in tickers
        ]

    def get_market_cap(self, tickers, as_of_date=None):  # pragma: no cover - unused
        return {}

    def get_fx_rate(self, base, quote, as_of_date=None):
        if base == quote:
            return 1.0
        return self._fx.get(f"{base}/{quote}")

    def get_name(self):
        return "Fake"


def company(ticker, cap, currency=Currency.EUR, sector="Technology", country="Germany", **kw):
    return Constituent(
        ticker=ticker,
        name=f"{ticker} Corp",
        market_cap=cap,
        free_float_market_cap=cap,
        currency=currency,
        sector=sector,
        country=country,
        price=10.0,
        **kw,
    )


@pytest.fixture
def market():
    return FakeConnector(
        [
            company("AAA", 300e9),
            company("BBB", 200e9),
            # Quoted in kronor: 900bn SEK is 81bn EUR, the smallest of the large caps.
            company("SWE", 900e9, currency=Currency.SEK, country="Sweden"),
            company("TEL", 150e9, sector="Communication Services"),
            company("TINY", 0.2e9),
            company("USA", 500e9, currency=Currency.USD, country="United States"),
        ],
        fx={"SEK/EUR": 0.09, "USD/EUR": 0.9},
    )


@pytest.fixture
def spec():
    return {
        "spec_version": 1,
        "name": "Test Index",
        "identifier": "TEST",
        "currency": "EUR",
        "base_date": "2025-01-01",
        "base_value": 1000,
        "universe": {
            "tickers": ["AAA", "BBB", "SWE", "TEL", "TINY", "USA", "GONE"],
            "countries": ["Germany", "Sweden"],
            "sectors": ["Technology"],
            "min_market_cap": 500_000_000,
        },
        "selection": {"rank_by": "MARKET_CAP", "top": 2},
        "weighting": {"method": "equal_weight"},
        "rebalancing": {"frequency": "quarterly"},
    }


class TestValidation:
    def test_valid_spec_round_trips(self, spec):
        assert validate_spec(validate_spec(spec)) == validate_spec(spec)

    def test_tickers_are_required(self, spec):
        del spec["universe"]["tickers"]
        with pytest.raises(SpecError, match="universe.tickers"):
            validate_spec(spec)

    def test_unknown_key_is_an_error(self, spec):
        spec["universe"]["min_marketcap"] = 1
        with pytest.raises(SpecError, match="min_marketcap"):
            validate_spec(spec)

    def test_duplicate_tickers_are_rejected(self, spec):
        spec["universe"]["tickers"] = ["AAA", "aaa"]
        with pytest.raises(SpecError, match="more than once"):
            validate_spec(spec)

    @pytest.mark.parametrize(
        "path, value",
        [
            (("currency",), "XXX"),
            (("base_date",), "01/02/2025"),
            (("base_value",), 0),
            (("universe", "countries"), ["Atlantis"]),
            (("universe", "sectors"), ["Wizardry"]),
            (("selection", "top"), 0),
            (("selection", "rank_by"), "VIBES"),
            (("weighting", "method"), "random"),
            (("weighting", "max_weight"), 1.5),
            (("rebalancing", "frequency"), "hourly"),
        ],
    )
    def test_bad_values_are_rejected(self, spec, path, value):
        target = spec
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with pytest.raises(SpecError):
            validate_spec(spec)

    def test_enum_names_and_values_both_accepted(self, spec):
        spec["universe"]["countries"] = ["GERMANY", "united states"]
        spec["universe"]["sectors"] = ["HEALTH_CARE"]
        clean = validate_spec(spec)
        assert clean["universe"]["countries"] == ["Germany", "United States"]
        assert clean["universe"]["sectors"] == ["Health Care"]

    def test_yaml_and_json_files_load_the_same(self, spec, tmp_path):
        dump_spec(spec, tmp_path / "s.yaml")
        dump_spec(spec, tmp_path / "s.json")
        assert load_spec(tmp_path / "s.yaml") == load_spec(tmp_path / "s.json")

    def test_unreadable_file_is_a_spec_error(self, tmp_path):
        with pytest.raises(SpecError, match="Cannot read"):
            load_spec(tmp_path / "missing.yaml")

    def test_malformed_file_is_a_spec_error(self, tmp_path):
        (tmp_path / "bad.json").write_text("{not json")
        with pytest.raises(SpecError, match="not valid JSON"):
            load_spec(tmp_path / "bad.json")

    def test_index_from_spec_fetches_nothing(self, spec):
        index = index_from_spec(spec)
        assert index.identifier == "TEST"
        assert index.universe.tickers == spec["universe"]["tickers"]


class TestBuild:
    def test_market_caps_are_compared_in_the_index_currency(self, spec, market):
        spec["selection"]["top"] = 3
        result = build(spec, as_of=AS_OF, connector=market)

        # Raw, SWE's 900bn would rank first. Converted, it is 81bn and ranks last.
        assert [c.ticker for c in result.constituents] == ["AAA", "BBB", "SWE"]
        swe = result.constituents[-1]
        assert swe.market_cap == pytest.approx(81e9)
        assert swe.market_cap_currency == Currency.EUR
        assert swe.currency == Currency.SEK  # the trading currency is untouched

    def test_every_left_out_candidate_has_a_reason(self, spec, market):
        result = build(spec, as_of=AS_OF, connector=market)

        assert [c.ticker for c in result.constituents] == ["AAA", "BBB"]
        assert result.exclusions == {
            "GONE": "no market data",
            "SWE": "ranked outside the selection",
            "TEL": "sector Communication Services is not in the universe",
            "TINY": "market cap is below the minimum",
            "USA": "country United States is not in the universe",
        }

    def test_weights_sum_to_one(self, spec, market):
        spec["weighting"] = {"method": "market_cap"}
        result = build(spec, as_of=AS_OF, connector=market)
        assert sum(c.weight for c in result.constituents) == pytest.approx(1.0)
        assert result.constituents[0].weight == pytest.approx(0.6)

    def test_missing_exchange_rate_is_reported_not_guessed(self, spec):
        market = FakeConnector([company("AAA", 300e9), company("SWE", 900e9, Currency.SEK)])
        spec["universe"] = {"tickers": ["AAA", "SWE"]}
        result = build(spec, as_of=AS_OF, connector=market)
        assert result.unconverted == {"SWE": "SEK"}
        assert result.to_dict()["constituents"][0]["market_cap_currency"] == "SEK"

    def test_sector_names_match_across_naming_schemes(self, spec, market):
        # The spec says GICS "Information Technology"; the data says "Technology".
        spec["universe"]["sectors"] = ["Information Technology"]
        result = build(spec, as_of=AS_OF, connector=market)
        assert [c.ticker for c in result.constituents] == ["AAA", "BBB"]

    def test_theme_keywords_filter_on_the_business_description(self, spec):
        market = FakeConnector(
            [
                company("QBT", 5e9, business_description="Builds superconducting qubit chips"),
                company("TEL", 90e9, business_description="Mobile and fixed-line telecoms"),
            ]
        )
        spec["universe"] = {"tickers": ["QBT", "TEL"]}
        spec["selection"] = {"theme_keywords": ["quantum", "qubit"]}
        result = build(spec, as_of=AS_OF, connector=market)
        assert [c.ticker for c in result.constituents] == ["QBT"]
        assert result.exclusions == {"TEL": "does not match the selection filters"}

    def test_same_inputs_give_the_same_output(self, spec, market):
        first = build(spec, as_of=AS_OF, connector=market).to_dict()
        second = build(spec, as_of=AS_OF, connector=market).to_dict()
        assert json.dumps(first) == json.dumps(second)

    def test_bad_as_of_date_is_rejected(self, spec, market):
        with pytest.raises(SpecError, match="as_of"):
            build(spec, as_of="yesterday", connector=market)


class TestReplay:
    def test_snapshot_replays_to_the_identical_result(self, spec, market, tmp_path):
        live = build(spec, as_of=AS_OF, connector=market)
        path = tmp_path / "data.json"
        path.write_text(json.dumps(live.snapshot))

        replayed = build(spec, as_of=AS_OF, connector=SnapshotConnector.load(path))

        assert replayed.to_dict()["constituents"] == live.to_dict()["constituents"]
        assert replayed.exclusions == live.exclusions

    def test_snapshot_holds_the_data_before_conversion(self, spec, market):
        snapshot = build(spec, as_of=AS_OF, connector=market).snapshot
        swe = next(row for row in snapshot["constituents"] if row["ticker"] == "SWE")
        assert swe["market_cap"] == 900e9
        assert swe["market_cap_currency"] == "SEK"
        assert snapshot["fx_rates"] == {"SEK/EUR": 0.09, "USD/EUR": 0.9}
        assert snapshot["as_of"] == AS_OF

    def test_unknown_snapshot_version_is_rejected(self):
        with pytest.raises(ValueError, match="snapshot version"):
            SnapshotConnector({"snapshot_version": 99})


class TestGetConstituents:
    def test_first_call_without_a_date_builds_the_index(self, spec, market):
        from indexforge import DataProvider

        index = index_from_spec(spec)
        index.set_data_provider(
            DataProvider.builder().add_source("fake", market).set_default("fake").build()
        )
        assert [c.ticker for c in index.get_constituents()] == ["AAA", "BBB"]

    def test_repeat_call_for_the_same_date_does_not_refetch(self, spec, market):
        result = build(spec, as_of=AS_OF, connector=market)
        result.index.get_constituents(AS_OF)
        result.index.get_constituents()
        assert market.calls == 1
