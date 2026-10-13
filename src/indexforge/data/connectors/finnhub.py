"""
Finnhub data connector.

Needs an API key; a free one is available from finnhub.io. The free plan gives
a company's name, country, industry and size, at up to 60 requests a minute. It
has no business descriptions, so a theme filter matches nothing; no sector in
the usual sense, so a sector rule excludes everything; and no way to search for
companies by rule. It suits an index that names its companies.
"""

import logging
import os
import time

import pandas as pd

from indexforge.core.constituent import Constituent
from indexforge.data.connectors._http import get_json
from indexforge.data.connectors.base import DataConnector, DataSourceError
from indexforge.data.countries import country_name
from indexforge.data.fx import parse_currency

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"

#: Seconds between requests, to stay inside 60 a minute.
REQUEST_INTERVAL = 1.05


class FinnhubConnector(DataConnector):
    """
    Data connector for Finnhub.

    Example:
        >>> connector = FinnhubConnector(api_key="...")   # or set FINNHUB_API_KEY
    """

    PROVIDES = frozenset({"market_cap", "industry", "country"})

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("FINNHUB_API_KEY")
        if not self._api_key:
            raise ValueError("Finnhub needs an API key. Pass one, or set FINNHUB_API_KEY.")
        self._last_request = 0.0

    def get_name(self) -> str:
        return "Finnhub"

    def _get(self, path: str, **params: object) -> dict:
        wait = self._last_request + REQUEST_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        answer = get_json(self.get_name(), f"{BASE_URL}/{path}", {**params, "token": self._api_key})
        if isinstance(answer, dict) and answer.get("error"):
            raise DataSourceError(f"{self.get_name()}: {answer['error']}")
        return answer if isinstance(answer, dict) else {}

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        return [self._profile(ticker) for ticker in tickers]

    def _profile(self, ticker: str) -> Constituent:
        row = self._get("stock/profile2", symbol=ticker)
        if not row:
            logger.warning("%s has no profile for %s", self.get_name(), ticker)
            return Constituent(ticker=ticker, name=ticker, country="Unknown")
        # Finnhub reports market capitalisation in millions.
        market_cap = float(row.get("marketCapitalization") or 0) * 1_000_000
        company = Constituent(
            ticker=ticker,
            name=row.get("name") or ticker,
            market_cap=market_cap,
            sector="Unknown",
            industry=row.get("finnhubIndustry") or "Unknown",
            country=country_name(row.get("country")),
            exchange=row.get("exchange") or "",
        )
        currency = parse_currency(row.get("currency"))
        if currency is not None:
            company.currency = currency
        company.free_float_market_cap = market_cap
        return company

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        return {c.ticker: c.market_cap for c in self.get_constituent_data(tickers)}

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        raise DataSourceError("Finnhub's free plan does not include price history.")
