"""
Reference data for screening: how each data source names countries and sectors.

Screening means asking a data source "which companies exist in these countries?"
instead of being handed a ticker list. Providers screen by the exchange a stock
is listed on, not by where the company is based, so a screen is only the first
pass: every candidate's details are then fetched and the universe rules applied
to its actual country of domicile.
"""

from indexforge.core.types import Sector

#: Country name (as in `Country`) to the region code Yahoo uses for its exchanges.
YAHOO_REGIONS: dict[str, str] = {
    "United States": "us",
    "Canada": "ca",
    "Mexico": "mx",
    "Austria": "at",
    "Belgium": "be",
    "Denmark": "dk",
    "Finland": "fi",
    "France": "fr",
    "Germany": "de",
    "Greece": "gr",
    "Ireland": "ie",
    "Italy": "it",
    "Netherlands": "nl",
    "Norway": "no",
    "Portugal": "pt",
    "Spain": "es",
    "Sweden": "se",
    "Switzerland": "ch",
    "United Kingdom": "gb",
    "Czech Republic": "cz",
    "Hungary": "hu",
    "Poland": "pl",
    "Turkey": "tr",
    "Australia": "au",
    "Hong Kong": "hk",
    "Japan": "jp",
    "New Zealand": "nz",
    "Singapore": "sg",
    "China": "cn",
    "India": "in",
    "Indonesia": "id",
    "Malaysia": "my",
    "Philippines": "ph",
    "South Korea": "kr",
    "Taiwan": "tw",
    "Thailand": "th",
    "Argentina": "ar",
    "Brazil": "br",
    "Chile": "cl",
    "Israel": "il",
    "Kuwait": "kw",
    "Qatar": "qa",
    "Saudi Arabia": "sa",
    "South Africa": "za",
    "Egypt": "eg",
}

#: Currency market caps are quoted in on each region's exchanges.
REGION_CURRENCY: dict[str, str] = {
    "us": "USD",
    "ca": "CAD",
    "mx": "MXN",
    "at": "EUR",
    "be": "EUR",
    "fi": "EUR",
    "fr": "EUR",
    "de": "EUR",
    "gr": "EUR",
    "ie": "EUR",
    "it": "EUR",
    "nl": "EUR",
    "pt": "EUR",
    "es": "EUR",
    "dk": "DKK",
    "no": "NOK",
    "se": "SEK",
    "ch": "CHF",
    "gb": "GBP",
    "cz": "CZK",
    "hu": "HUF",
    "pl": "PLN",
    "tr": "TRY",
    "au": "AUD",
    "hk": "HKD",
    "jp": "JPY",
    "nz": "NZD",
    "sg": "SGD",
    "cn": "CNY",
    "in": "INR",
    "id": "IDR",
    "my": "MYR",
    "ph": "PHP",
    "kr": "KRW",
    "tw": "TWD",
    "th": "THB",
    "ar": "ARS",
    "br": "BRL",
    "cl": "CLP",
    "il": "ILS",
    "sa": "SAR",
    "za": "ZAR",
}

#: Regions where most listed lines are secondary quotes of foreign companies or
#: over-the-counter duplicates. Only the main exchanges are screened there.
PRIMARY_EXCHANGES: dict[str, list[str]] = {
    "de": ["GER"],
    "us": ["NMS", "NYQ", "NGM", "NCM", "ASE"],
}

#: Canonical (GICS, lower-case) sector name to the name Yahoo files it under.
YAHOO_SECTORS: dict[str, str] = {
    "information technology": "Technology",
    "health care": "Healthcare",
    "financials": "Financial Services",
    "consumer staples": "Consumer Defensive",
    "consumer discretionary": "Consumer Cyclical",
    "materials": "Basic Materials",
    "communication services": "Communication Services",
    "energy": "Energy",
    "industrials": "Industrials",
    "real estate": "Real Estate",
    "utilities": "Utilities",
}


def yahoo_sector(name: str) -> str | None:
    """Yahoo's name for a sector given in either naming scheme, or None."""
    return YAHOO_SECTORS.get(Sector.canonical(name))
