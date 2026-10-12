"""
A connector that keeps company details in a store instead of refetching them.
"""

import logging
from datetime import timedelta

import pandas as pd

from indexforge.core.constituent import Constituent
from indexforge.data.connectors.base import DataConnector, DataSourceError
from indexforge.data.store import CompanyStore

logger = logging.getLogger(__name__)

#: How long stored details are used before they are fetched again.
DEFAULT_MAX_AGE = timedelta(days=7)

#: Companies fetched and stored per step, so a source that stops answering
#: part-way leaves the earlier steps saved.
FETCH_BATCH = 50


class CachedConnector(DataConnector):
    """
    Wraps a connector so that company details come from a store when possible.

    Only what changes slowly is reused: name, country, sector, industry,
    description, currency. Size, price and volume are never taken from the
    store. So stored details are used for a company only when the source's
    screen has just supplied its current figures; a company without them is
    fetched in full, and stored for next time.

    Example:
        >>> connector = CachedConnector(YahooFinanceConnector(), SqliteCompanyStore("c.sqlite"))
        >>> result = build("index.yaml", connector=connector)
    """

    def __init__(
        self,
        inner: DataConnector,
        store: CompanyStore,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self._inner = inner
        self._store = store
        self._max_age = max_age
        self.PROVIDES = inner.PROVIDES  # type: ignore[misc]
        #: Companies read from the store, and fetched, by the last request.
        self.reused = 0
        self.fetched = 0

    def get_name(self) -> str:
        return self._inner.get_name()

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        return self._inner.get_prices(tickers, start_date, end_date)

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        return self._inner.get_market_cap(tickers, as_of_date)

    def get_fx_rate(self, base: str, quote: str, as_of_date: str | None = None) -> float | None:
        return self._inner.get_fx_rate(base, quote, as_of_date)

    def screen(
        self,
        countries: list[str],
        sectors: list[str] | None = None,
        min_market_cap: float | None = None,
        currency: str = "USD",
    ) -> list[str]:
        return self._inner.screen(countries, sectors, min_market_cap, currency)

    def screened_quotes(self) -> dict[str, dict[str, float]]:
        return self._inner.screened_quotes()

    def unscreenable_countries(self, countries: list[str]) -> list[str]:
        return self._inner.unscreenable_countries(countries)

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        source = self._inner.get_name()
        quotes = self._inner.screened_quotes()

        # Stored details are only usable alongside a current size.
        reusable = [t for t in tickers if quotes.get(t, {}).get("market_cap")]
        companies = self._store.get(source, reusable, self._max_age) if reusable else {}
        for ticker, company in companies.items():
            _apply_quote(company, quotes[ticker])
        self.reused = len(companies)

        missing = [t for t in tickers if t not in companies]
        self.fetched = 0
        for start in range(0, len(missing), FETCH_BATCH):
            batch = missing[start : start + FETCH_BATCH]
            try:
                fetched = self._inner.get_constituent_data(batch, as_of_date)
            except DataSourceError as e:
                self._store.want(source, missing[start:])
                if not self.fetched:
                    raise
                raise DataSourceError(
                    f"{e} Details for {self.reused + self.fetched} of {len(tickers)} "
                    "companies are saved, so trying again continues from there."
                ) from e
            # A company the source knew nothing about is not kept: next time it
            # may answer, and a stored blank would hide that for a week.
            self._store.put(source, [c for c in fetched if c.market_cap or c.business_description])
            self.fetched += len(fetched)
            companies.update({c.ticker: c for c in fetched})

        if self.reused:
            logger.info("Company details: %d from the store, %d fetched", self.reused, self.fetched)
        return [companies[t] for t in tickers if t in companies]


def _apply_quote(company: Constituent, quote: dict[str, float]) -> None:
    """Replace a stored company's size, price and volume with current figures."""
    company.market_cap = quote["market_cap"]
    company.free_float_market_cap = company.market_cap * company.free_float_factor
    if quote.get("price"):
        company.price = quote["price"]
    if quote.get("average_daily_volume"):
        company.average_daily_volume = quote["average_daily_volume"]
