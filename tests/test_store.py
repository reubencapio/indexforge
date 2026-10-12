"""Stored company details: fetched once, with sizes always current."""

from datetime import timedelta

import pandas as pd
import pytest

from indexforge import (
    CachedConnector,
    Constituent,
    DataConnector,
    DataSourceError,
    MemoryCompanyStore,
    SqliteCompanyStore,
    build,
)
from indexforge.cli import main


class Market(DataConnector):
    """A source that counts detail requests and can stop answering."""

    def __init__(self, companies, with_quotes=True):
        self.companies = {c.ticker: c for c in companies}
        self.with_quotes = with_quotes
        self.requested: list[str] = []
        self.answers_left = 10_000

    def get_name(self):
        return "Test Market"

    def get_prices(self, tickers, start_date, end_date):  # pragma: no cover - unused
        return pd.DataFrame()

    def get_market_cap(self, tickers, as_of_date=None):  # pragma: no cover - unused
        return {}

    def get_fx_rate(self, base, quote, as_of_date=None):
        return 1.0

    def get_constituent_data(self, tickers, as_of_date=None):
        if self.answers_left < len(tickers):
            raise DataSourceError("Test Market is limiting requests.")
        self.answers_left -= len(tickers)
        self.requested.extend(tickers)
        return [
            (
                Constituent.from_dict(self.companies[t].to_dict())
                if t in self.companies
                else Constituent(ticker=t, name=t)
            )
            for t in tickers
        ]

    def screen(self, countries, sectors=None, min_market_cap=None, currency="USD"):
        return sorted(self.companies)

    def screened_quotes(self):
        if not self.with_quotes:
            return {}
        return {
            t: {"market_cap": c.market_cap, "price": c.price} for t, c in self.companies.items()
        }


def company(ticker, market_cap, description="Makes things."):
    return Constituent(
        ticker=ticker,
        name=f"{ticker} Corp",
        market_cap=market_cap,
        price=10.0,
        country="Germany",
        sector="Technology",
        business_description=description,
    )


SPEC = {
    "spec_version": 1,
    "name": "Stored",
    "identifier": "STORED",
    "currency": "USD",
    "base_date": "2025-01-01",
    "base_value": 1000,
    "universe": {"screen": True, "countries": ["Germany"], "min_market_cap": 1e9},
    "selection": {"rank_by": "MARKET_CAP", "top": 2},
    "weighting": {"method": "market_cap"},
    "rebalancing": {"frequency": "quarterly"},
}


@pytest.fixture
def market():
    return Market([company("AAA", 9e9), company("BBB", 5e9), company("CCC", 2e9)])


def weights(result):
    return {c.ticker: round(c.weight, 6) for c in result.constituents}


def test_details_are_fetched_once(market):
    store = MemoryCompanyStore()

    first = build(SPEC, connector=CachedConnector(market, store))
    second = build(SPEC, connector=CachedConnector(market, store))

    assert market.requested == ["AAA", "BBB", "CCC"]
    assert weights(first) == weights(second)
    assert first.data_source == "Test Market"


def test_sizes_come_from_the_screen_not_the_store(market):
    store = MemoryCompanyStore()
    build(SPEC, connector=CachedConnector(market, store))

    market.companies["CCC"].market_cap = 50e9  # the company grew since it was stored
    result = build(SPEC, connector=CachedConnector(market, store))

    assert [c.ticker for c in result.constituents] == ["CCC", "AAA"]
    assert market.requested == ["AAA", "BBB", "CCC"]


def test_without_current_sizes_the_store_is_not_used(market):
    market.with_quotes = False
    store = MemoryCompanyStore()

    build(SPEC, connector=CachedConnector(market, store))
    build(SPEC, connector=CachedConnector(market, store))

    assert market.requested == ["AAA", "BBB", "CCC"] * 2


def test_old_details_are_fetched_again(market):
    store = MemoryCompanyStore()
    build(SPEC, connector=CachedConnector(market, store))

    build(SPEC, connector=CachedConnector(market, store, max_age=timedelta(seconds=-1)))

    assert len(market.requested) == 6


def test_a_source_that_stops_answering_keeps_what_it_gave(market, monkeypatch):
    from indexforge.data.connectors import cached

    monkeypatch.setattr(cached, "FETCH_BATCH", 2)
    market.answers_left = 2
    store = MemoryCompanyStore()

    with pytest.raises(DataSourceError, match="2 of 3 companies are saved"):
        build(SPEC, connector=CachedConnector(market, store))
    assert store.wanted == [("Test Market", "CCC")]

    market.answers_left = 10
    result = build(SPEC, connector=CachedConnector(market, store))

    assert len(result.constituents) == 2
    assert market.requested == ["AAA", "BBB", "CCC"]


def test_a_company_the_source_knows_nothing_about_is_not_stored(market):
    market.screen = lambda *args, **kwargs: ["AAA", "GONE"]
    store = MemoryCompanyStore()

    build(SPEC, connector=CachedConnector(market, store))

    assert set(store.get("Test Market", ["AAA", "GONE"], timedelta(days=1))) == {"AAA"}


def test_sqlite_store_keeps_details_between_processes(tmp_path):
    path = tmp_path / "nested" / "companies.sqlite"
    SqliteCompanyStore(path).put("Yahoo Finance", [company("AAA", 9e9, "Builds qubits.")])

    stored = SqliteCompanyStore(path).get("Yahoo Finance", ["AAA", "BBB"], timedelta(days=1))

    assert stored["AAA"].business_description == "Builds qubits."
    assert stored["AAA"].country == "Germany"
    assert SqliteCompanyStore(path).get("Other", ["AAA"], timedelta(days=1)) == {}
    assert SqliteCompanyStore(path).get("Yahoo Finance", ["AAA"], timedelta(seconds=-1)) == {}


def test_cli_cache_option_stores_details(market, tmp_path, monkeypatch, capsys):
    import yaml

    from indexforge import cli

    monkeypatch.setattr(cli, "_live_connector", lambda source: market)
    spec = tmp_path / "index.yaml"
    spec.write_text(yaml.safe_dump(SPEC))
    cache = tmp_path / "companies.sqlite"

    assert main(["build", str(spec), "--cache", str(cache)]) == 0
    assert main(["build", str(spec), "--cache", str(cache)]) == 0

    assert market.requested == ["AAA", "BBB", "CCC"]
    assert "AAA" in capsys.readouterr().out
