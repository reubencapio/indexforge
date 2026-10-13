"""Country names as the library uses them, by ISO 3166-1 alpha-2 code."""

ISO_COUNTRIES: dict[str, str] = {
    "US": "United States",
    "CA": "Canada",
    "MX": "Mexico",
    "AT": "Austria",
    "BE": "Belgium",
    "DK": "Denmark",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "IE": "Ireland",
    "IT": "Italy",
    "LU": "Luxembourg",
    "NL": "Netherlands",
    "NO": "Norway",
    "PT": "Portugal",
    "ES": "Spain",
    "SE": "Sweden",
    "CH": "Switzerland",
    "GB": "United Kingdom",
    "CZ": "Czech Republic",
    "HU": "Hungary",
    "PL": "Poland",
    "TR": "Turkey",
    "AU": "Australia",
    "HK": "Hong Kong",
    "JP": "Japan",
    "NZ": "New Zealand",
    "SG": "Singapore",
    "CN": "China",
    "IN": "India",
    "ID": "Indonesia",
    "MY": "Malaysia",
    "PH": "Philippines",
    "KR": "South Korea",
    "TW": "Taiwan",
    "TH": "Thailand",
    "AR": "Argentina",
    "BR": "Brazil",
    "CL": "Chile",
    "CO": "Colombia",
    "EG": "Egypt",
    "IL": "Israel",
    "KW": "Kuwait",
    "QA": "Qatar",
    "SA": "Saudi Arabia",
    "ZA": "South Africa",
    "AE": "United Arab Emirates",
}

ISO_CODES: dict[str, str] = {name: code for code, name in ISO_COUNTRIES.items()}


def country_name(code: str | None) -> str:
    """The library's name for an ISO code; the code itself if it is not one we know."""
    if not code:
        return "Unknown"
    return ISO_COUNTRIES.get(code.strip().upper(), code.strip())
