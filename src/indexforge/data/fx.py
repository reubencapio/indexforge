"""
Currency handling for market data.

Market caps arrive in each stock's trading currency. Comparing them raw ranks a
Swedish company quoted in kronor roughly ten times too high against one quoted
in dollars, so everything is converted to the index currency before any rule
that looks at size is applied.
"""

import logging
from collections.abc import Callable

from indexforge.core.constituent import Constituent
from indexforge.core.types import Currency

logger = logging.getLogger(__name__)

#: Minor-unit codes some exchanges quote share prices in, mapped to the currency
#: the company's market cap is actually stated in.
MINOR_UNITS = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ZAC": "ZAR", "ILA": "ILS"}

RateLookup = Callable[[str, str], float | None]


def parse_currency(code: str | None) -> Currency | None:
    """Currency for a provider's code, or None if the code is not supported."""
    if not code:
        return None
    code = MINOR_UNITS.get(code, code)
    try:
        return Currency(code.upper())
    except ValueError:
        return None


def convert_market_caps(
    constituents: list[Constituent], target: Currency, rate: RateLookup
) -> dict[str, str]:
    """
    Convert every constituent's market cap into `target`, in place.

    Share prices and `currency` are left as the exchange quotes them;
    `market_cap_currency` records what the market cap is now stated in, which
    also makes a second call a no-op.

    Args:
        constituents: Constituents to convert
        target: Currency to convert into
        rate: Returns units of the second currency per unit of the first, or None

    Returns:
        Ticker to source currency for every constituent that could not be
        converted because no rate was available. Their figures are unchanged.
    """
    rates: dict[str, float | None] = {}
    unconverted: dict[str, str] = {}

    for constituent in constituents:
        source = constituent.market_cap_currency or constituent.currency
        if source == target:
            continue

        if source.value not in rates:
            rates[source.value] = rate(source.value, target.value)
        fx = rates[source.value]

        if fx is None:
            unconverted[constituent.ticker] = source.value
            continue

        constituent.market_cap *= fx
        constituent.free_float_market_cap *= fx
        constituent.market_cap_currency = target

    if unconverted:
        logger.warning(
            "No %s exchange rate for %s; their market caps are not comparable",
            target.value,
            ", ".join(sorted(unconverted)),
        )
    return unconverted
