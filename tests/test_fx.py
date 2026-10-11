"""Tests for currency parsing and market cap conversion."""

import pytest

from indexforge.core.constituent import Constituent
from indexforge.core.types import Currency, Sector
from indexforge.data.fx import convert_market_caps, parse_currency


@pytest.mark.parametrize(
    "code, expected",
    [
        ("USD", Currency.USD),
        ("sek", Currency.SEK),
        ("GBp", Currency.GBP),  # London quotes prices in pence, market caps in pounds
        ("ZAc", Currency.ZAR),
        ("ILA", Currency.ILS),
        ("XXX", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_currency(code, expected):
    assert parse_currency(code) == expected


def test_conversion_is_idempotent():
    stock = Constituent(
        ticker="SWE", market_cap=100.0, free_float_market_cap=80.0, currency=Currency.SEK
    )
    rate = {("SEK", "USD"): 0.1}

    for _ in range(2):
        assert convert_market_caps([stock], Currency.USD, lambda b, q: rate[(b, q)]) == {}

    assert stock.market_cap == pytest.approx(10.0)
    assert stock.free_float_market_cap == pytest.approx(8.0)


def test_each_rate_is_looked_up_once():
    stocks = [Constituent(ticker=t, market_cap=1.0, currency=Currency.SEK) for t in "ABC"]
    asked = []

    def rate(base, quote):
        asked.append((base, quote))
        return 0.1

    convert_market_caps(stocks, Currency.USD, rate)
    assert asked == [("SEK", "USD")]


@pytest.mark.parametrize(
    "a, b",
    [
        ("Technology", "Information Technology"),
        ("Healthcare", "HEALTH_CARE"),
        ("Financial Services", "financials"),
        ("Consumer Cyclical", "Consumer Discretionary"),
    ],
)
def test_sector_names_are_canonicalised(a, b):
    assert Sector.canonical(a) == Sector.canonical(b)


def test_distinct_sectors_stay_distinct():
    assert Sector.canonical("Energy") != Sector.canonical("Utilities")
