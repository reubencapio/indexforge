"""
Financial Modeling Prep data connector.

Needs an API key; a free one is available from financialmodelingprep.com. What
the free plan includes (which exchanges, how many requests a day, whether the
company screener is open) is decided by FMP and changes; a request outside the
plan fails with a message saying so.
"""

import logging
import os

import pandas as pd

from indexforge.core.constituent import Constituent
from indexforge.data.connectors._http import get_json
from indexforge.data.connectors.base import DataConnector, DataSourceError
from indexforge.data.countries import ISO_CODES, country_name
from indexforge.data.fx import parse_currency
from indexforge.data.screening import REGION_CURRENCY, YAHOO_REGIONS, yahoo_sector

logger = logging.getLogger(__name__)

BASE_URL = "https://financialmodelingprep.com/stable"
SCREEN_LIMIT = 5000


class FMPConnector(DataConnector):
    """
    Data connector for Financial Modeling Prep.

    Example:
        >>> connector = FMPConnector(api_key="...")   # or set FMP_API_KEY
        >>> result = build("index.yaml", connector=connector)
    """

    PROVIDES = frozenset(
        {
            "market_cap",
            "average_daily_volume",
            "business_description",
            "sector",
            "industry",
            "country",
        }
    )

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("FMP_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Financial Modeling Prep needs an API key. Pass one, or set FMP_API_KEY."
            )
        self._quotes: dict[str, dict[str, float]] = {}

    def get_name(self) -> str:
        return "Financial Modeling Prep"

    def _get(self, path: str, **params: object) -> list[dict]:
        answer = get_json(
            self.get_name(), f"{BASE_URL}/{path}", {**params, "apikey": self._api_key}
        )
        if isinstance(answer, dict):
            # FMP reports some refusals as a normal answer carrying a message.
            message = answer.get("Error Message") or answer.get("message")
            if message:
                raise DataSourceError(f"{self.get_name()}: {message}")
            return [answer]
        return answer if isinstance(answer, list) else []

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        return [self._profile(ticker) for ticker in tickers]

    def _profile(self, ticker: str) -> Constituent:
        rows = self._get("profile", symbol=ticker)
        if not rows:
            logger.warning("%s has no profile for %s", self.get_name(), ticker)
            return Constituent(ticker=ticker, name=ticker, country="Unknown")
        row = rows[0]
        market_cap = float(row.get("marketCap") or row.get("mktCap") or 0)
        currency = parse_currency(row.get("currency"))
        company = Constituent(
            ticker=ticker,
            name=row.get("companyName") or ticker,
            market_cap=market_cap,
            sector=row.get("sector") or "Unknown",
            industry=row.get("industry") or "Unknown",
            country=country_name(row.get("country")),
            exchange=row.get("exchange") or row.get("exchangeShortName") or "",
            average_daily_volume=float(row.get("averageVolume") or row.get("volAvg") or 0),
            business_description=row.get("description") or "",
        )
        if currency is not None:
            company.currency = currency
        company.free_float_market_cap = market_cap
        if row.get("price"):
            company.price = float(row["price"])
        return company

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        return {c.ticker: c.market_cap for c in self.get_constituent_data(tickers)}

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        closes = {}
        for ticker in tickers:
            rows = self._get(
                "historical-price-eod/full", symbol=ticker, **{"from": start_date, "to": end_date}
            )
            closes[ticker] = pd.Series(
                {pd.Timestamp(r["date"]): float(r["close"]) for r in rows if r.get("close")}
            )
        return pd.DataFrame(closes).sort_index()

    def get_fx_rate(self, base: str, quote: str, as_of_date: str | None = None) -> float | None:
        if base == quote:
            return 1.0
        try:
            if as_of_date:
                end = pd.Timestamp(as_of_date)
                rows = self._get(
                    "historical-price-eod/light",
                    symbol=f"{base}{quote}",
                    **{"from": str((end - pd.Timedelta(days=10)).date()), "to": str(end.date())},
                )
                rows = sorted(rows, key=lambda r: r.get("date", ""))[-1:]
            else:
                rows = self._get("quote", symbol=f"{base}{quote}")
        except DataSourceError as e:
            logger.warning("No %s/%s rate from %s: %s", base, quote, self.get_name(), e)
            return None
        rate = float(rows[0].get("price") or 0) if rows else 0
        return rate if rate > 0 else None

    def unscreenable_countries(self, countries: list[str]) -> list[str]:
        return sorted(c for c in countries if c not in ISO_CODES)

    def screen(
        self,
        countries: list[str],
        sectors: list[str] | None = None,
        min_market_cap: float | None = None,
        currency: str = "USD",
    ) -> list[str]:
        """
        Find companies based in the given countries with FMP's company screener.

        As with the Yahoo screen, the size floor sent is half the minimum,
        converted to the local currency, and the exact minimum is applied
        afterwards; one line is kept per company, the most traded.
        """
        if not countries:
            raise ValueError("Screening needs at least one country")
        skipped = self.unscreenable_countries(countries)
        countries = [c for c in countries if c not in skipped]
        if not countries:
            raise ValueError(
                f"{self.get_name()} covers no exchange in: {', '.join(skipped)}. "
                "Choose other countries or list the tickers."
            )

        sector_names: list[str | None] = []
        for sector in sectors or []:
            name = yahoo_sector(sector)  # FMP files sectors under the same names
            if name is None:
                raise ValueError(f"{self.get_name()} has no sector named {sector!r}")
            if name not in sector_names:
                sector_names.append(name)

        self._quotes = {}
        best: dict[str, tuple[float, str]] = {}  # company name -> (volume, ticker)
        for country in countries:
            floor = None
            if min_market_cap:
                local = REGION_CURRENCY.get(YAHOO_REGIONS.get(country, ""))
                rate = self.get_fx_rate(currency, local) if local else None
                if rate:
                    floor = int(min_market_cap * rate * 0.5)
            for sector_name in sector_names or [None]:
                rows = self._get(
                    "company-screener",
                    country=ISO_CODES[country],
                    sector=sector_name,
                    marketCapMoreThan=floor,
                    isEtf="false",
                    isFund="false",
                    isActivelyTrading="true",
                    limit=SCREEN_LIMIT,
                )
                for row in rows:
                    ticker = row.get("symbol")
                    if not ticker or row.get("isEtf") or row.get("isFund"):
                        continue
                    name = row.get("companyName") or ticker
                    volume = float(row.get("volume") or 0)
                    if name not in best or (-volume, ticker) < (-best[name][0], best[name][1]):
                        best[name] = (volume, ticker)
                    figures = {"market_cap": row.get("marketCap"), "price": row.get("price")}
                    self._quotes[ticker] = {k: float(v) for k, v in figures.items() if v}

        tickers = sorted(ticker for _, ticker in best.values())
        self._quotes = {ticker: self._quotes[ticker] for ticker in tickers}
        return tickers

    def screened_quotes(self) -> dict[str, dict[str, float]]:
        return dict(self._quotes)
