"""Yahoo rate limiting: retried, and never mistaken for a company with no data."""

import sys
import types

import pandas as pd
import pytest

from indexforge import DataSourceError
from indexforge.cli import main
from indexforge.data.connectors import yahoo as yahoo_module
from indexforge.data.connectors.yahoo import YahooFinanceConnector


class YFRateLimitError(Exception):
    """Same class name as the error yfinance raises."""

    def __init__(self):
        super().__init__("Too Many Requests. Rate limited. Try after a while.")


@pytest.fixture
def yahoo(monkeypatch):
    """A connector on a stand-in yfinance that refuses the first `refusals` requests."""
    state = {"refusals": 0, "requests": 0, "waits": []}

    def answer(value):
        state["requests"] += 1
        if state["refusals"] > 0:
            state["refusals"] -= 1
            raise YFRateLimitError()
        return value

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            if self.symbol == "GONE":
                raise KeyError("no such ticker")
            return answer(
                {
                    "longName": f"{self.symbol} Corp",
                    "marketCap": 5e9,
                    "country": "Germany",
                    "currency": "EUR",
                    "currentPrice": 10.0,
                }
            )

        def history(self, **kwargs):
            return answer(pd.DataFrame({"Close": [1.25]}))

    fake = types.ModuleType("yfinance")
    fake.Ticker = Ticker
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setattr(yahoo_module.time, "sleep", state["waits"].append)

    return YahooFinanceConnector(max_workers=1), state


def test_a_brief_limit_is_waited_out(yahoo):
    connector, state = yahoo
    state["refusals"] = 2

    [company] = connector.get_constituent_data(["AAA.DE"])

    assert company.country == "Germany"
    assert state["waits"] == list(yahoo_module.RATE_LIMIT_WAITS[:2])


def test_a_lasting_limit_fails_instead_of_returning_empty_companies(yahoo):
    connector, state = yahoo
    state["refusals"] = 10_000

    with pytest.raises(DataSourceError, match="limiting requests"):
        connector.get_constituent_data(["AAA.DE", "BBB.DE", "CCC.DE"])

    # One ticker waits through every retry; the others fail at once.
    assert state["waits"] == list(yahoo_module.RATE_LIMIT_WAITS)
    assert state["requests"] == len(yahoo_module.RATE_LIMIT_WAITS) + 1


def test_the_next_build_tries_again(yahoo):
    connector, state = yahoo
    state["refusals"] = 10_000
    with pytest.raises(DataSourceError):
        connector.get_constituent_data(["AAA.DE"])

    state["refusals"] = 0
    assert connector.get_constituent_data(["AAA.DE"])[0].market_cap == 5e9


def test_an_unknown_ticker_is_still_a_bare_record(yahoo):
    connector, _ = yahoo

    [company] = connector.get_constituent_data(["GONE"])

    assert company.name == "GONE" and company.market_cap == 0


def test_a_limited_exchange_rate_fails_instead_of_reading_as_missing(yahoo):
    connector, state = yahoo
    state["refusals"] = 10_000

    with pytest.raises(DataSourceError):
        connector.get_fx_rate("SEK", "EUR")


def test_cli_reports_the_limit_and_exits_1(yahoo, tmp_path, capsys):
    _, state = yahoo
    state["refusals"] = 10_000
    spec = tmp_path / "index.yaml"
    assert main(["init", str(spec)]) == 0

    assert main(["build", str(spec)]) == 1
    assert "limiting requests" in capsys.readouterr().err


def test_many_blank_answers_fail_instead_of_reading_as_unknown_companies(yahoo, monkeypatch):
    connector, _ = yahoo
    monkeypatch.setattr(
        connector,
        "_get_stock_info",
        lambda stock, ticker: {} if ticker != "AAA.DE" else {"country": "Germany"},
    )

    with pytest.raises(DataSourceError, match="no data for 9 of 10"):
        connector.get_constituent_data(["AAA.DE", *[f"X{i}.DE" for i in range(9)]])


def test_a_few_unknown_tickers_are_not_a_failure(yahoo, monkeypatch):
    connector, _ = yahoo
    monkeypatch.setattr(connector, "_get_stock_info", lambda stock, ticker: {})

    assert len(connector.get_constituent_data(["TYPO1", "TYPO2", "TYPO3"])) == 3


def test_securities_without_a_size_or_country_are_not_blank_answers(yahoo, monkeypatch):
    # Listed bonds: Yahoo answers in full, with no market cap and no country.
    connector, _ = yahoo
    monkeypatch.setattr(
        connector,
        "_get_stock_info",
        lambda stock, ticker: {"quoteType": "EQUITY", "longName": "Bank 1.3% 2027"},
    )

    assert len(connector.get_constituent_data([f"BOND{i}.PA" for i in range(10)])) == 10
