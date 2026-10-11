"""
Yahoo Finance data connector.

A free data connector using the yfinance library.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pandas as pd

from indexforge.core.constituent import Constituent
from indexforge.core.types import Currency
from indexforge.data.connectors.base import DataConnector
from indexforge.data.fx import parse_currency
from indexforge.data.screening import (
    PRIMARY_EXCHANGES,
    REGION_CURRENCY,
    YAHOO_REGIONS,
    yahoo_sector,
)

logger = logging.getLogger(__name__)

#: Yahoo's largest page, and a ceiling on one screen so a loose rule cannot
#: turn into tens of thousands of follow-up requests.
SCREEN_PAGE_SIZE = 250
SCREEN_MAX_RESULTS = 5000


def _any_of(query_cls: Any, field: str, values: list[str]) -> Any:
    """A query matching any of `values`; Yahoo rejects an "or" with one operand."""
    terms = [query_cls("eq", [field, value]) for value in values]
    return terms[0] if len(terms) == 1 else query_cls("or", terms)


class YahooFinanceConnector(DataConnector):
    """
    Data connector for Yahoo Finance (free data source).

    Uses the yfinance library to fetch market data, fundamentals,
    and corporate actions data.

    Features:
    - Historical prices (OHLCV)
    - Market capitalization
    - Basic fundamentals (sector, industry, country)
    - Dividend and split data
    - Free, no API key required

    Limitations:
    - Data is delayed 15-20 minutes
    - No intraday data on free tier
    - Rate limits exist (but generous)
    - Historical data may have gaps

    Example:
        >>> connector = YahooFinanceConnector()
        >>> prices = connector.get_prices(["AAPL", "MSFT"], "2024-01-01", "2024-12-31")
        >>> constituents = connector.get_constituent_data(["AAPL", "MSFT"])
    """

    #: Mirrors the fields set in get_constituent_data below.
    PROVIDES = frozenset(
        {
            "market_cap",
            "free_float_market_cap",
            "free_float_factor",
            "average_daily_volume",
            "dividend_yield",
            "pe_ratio",
            "pb_ratio",
            "business_description",
            "sector",
            "industry",
            "country",
        }
    )

    def __init__(self, cache_enabled: bool = True, max_workers: int = 8) -> None:
        """
        Initialize Yahoo Finance connector.

        Args:
            cache_enabled: Whether to cache data locally (reduces API calls)
            max_workers: How many tickers to fetch at once
        """
        self._cache_enabled = cache_enabled
        self._max_workers = max(1, max_workers)
        self._price_cache: dict[str, pd.DataFrame] = {}
        self._info_cache: dict[str, dict] = {}
        self._fx_cache: dict[str, float] = {}
        self._yf = None

    def _get_yfinance(self):
        """Lazy load yfinance to avoid import errors if not installed."""
        if self._yf is None:
            try:
                import yfinance as yf

                self._yf = yf
            except ImportError:
                raise ImportError(
                    "yfinance is required for YahooFinanceConnector. "
                    "Install it with: pip install yfinance"
                )
        return self._yf

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch historical prices from Yahoo Finance.

        Args:
            tickers: List of ticker symbols
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with OHLCV data for each ticker
        """
        yf = self._get_yfinance()

        try:
            # Download all tickers at once (more efficient)
            data = yf.download(
                tickers,
                start=start_date,
                end=end_date,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )

            if data.empty:
                logger.warning(f"No data returned for {tickers}")
                return pd.DataFrame()

            # If single ticker, yfinance doesn't use MultiIndex
            if len(tickers) == 1:
                data.columns = pd.MultiIndex.from_product([[tickers[0]], data.columns])

            return data

        except Exception as e:
            logger.error(f"Error fetching prices: {e}")
            return pd.DataFrame()

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        """
        Fetch constituent data from Yahoo Finance.

        Args:
            tickers: List of ticker symbols
            as_of_date: Date for data (ignored, always uses latest)

        Returns:
            List of Constituent objects
        """
        self._get_yfinance()
        if not tickers:
            return []

        # One request per ticker, so they are made concurrently. `map` returns
        # results in input order, which keeps the output order deterministic.
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(tickers))) as pool:
            return list(pool.map(self._fetch_constituent, tickers))

    def _fetch_constituent(self, ticker: str) -> Constituent:
        """Fetch one constituent. Never raises: a failure yields a bare record."""
        yf = self._get_yfinance()
        try:
            stock = yf.Ticker(ticker)
            info = self._get_stock_info(stock, ticker)

            constituent = Constituent(
                ticker=ticker,
                name=info.get("longName", info.get("shortName", ticker)),
                market_cap=info.get("marketCap", 0) or 0,
                sector=info.get("sector", "Unknown"),
                industry=info.get("industry", "Unknown"),
                country=info.get("country", "Unknown"),
                currency=self._currency(info.get("currency"), ticker),
                exchange=info.get("exchange", ""),
                dividend_yield=info.get("dividendYield", 0) or 0,
                pe_ratio=info.get("trailingPE"),
                pb_ratio=info.get("priceToBook"),
                average_daily_volume=info.get("averageVolume", 0) or 0,
                free_float_factor=self._calculate_free_float(info),
                business_description=info.get("longBusinessSummary", "") or "",
            )

            # Calculate free float market cap
            constituent.free_float_market_cap = (
                constituent.market_cap * constituent.free_float_factor
            )

            # The quote already carries the price; only fall back to a second
            # request when it does not.
            price = info.get("currentPrice") or info.get("regularMarketPrice")
            if not price:
                hist = stock.history(period="1d")
                if not hist.empty:
                    price = hist["Close"].iloc[-1]
            if price:
                constituent.price = float(price)

            return constituent

        except Exception as e:
            logger.warning(f"Error fetching data for {ticker}: {e}")
            # Create minimal constituent
            return Constituent(ticker=ticker, name=ticker)

    def screen(
        self,
        countries: list[str],
        sectors: list[str] | None = None,
        min_market_cap: float | None = None,
        currency: str = "USD",
    ) -> list[str]:
        """
        Find companies listed on the given countries' exchanges.

        Yahoo screens by listing venue, so foreign companies with a local quote
        come back too; they are removed later by the universe's country rule.
        Where a company has several lines (share classes, or a quote on more
        than one venue) only the most traded line is kept.

        The size floor sent to Yahoo is half the requested minimum, converted to
        the local currency. The exact minimum is applied afterwards on converted
        figures; the margin keeps a company near the line from being lost to a
        difference between Yahoo's rate and ours.
        """
        yf = self._get_yfinance()
        from yfinance import EquityQuery

        unknown = sorted(c for c in countries if c not in YAHOO_REGIONS)
        if unknown:
            raise ValueError(f"Yahoo Finance cannot screen these countries: {', '.join(unknown)}")
        if not countries:
            raise ValueError("Screening needs at least one country")

        yahoo_sectors: list[str] = []
        for sector in sectors or []:
            name = yahoo_sector(sector)
            if name is None:
                raise ValueError(f"Yahoo Finance has no sector named {sector!r}")
            if name not in yahoo_sectors:
                yahoo_sectors.append(name)

        best: dict[str, tuple[float, str]] = {}  # company name -> (volume, ticker)
        for country in countries:
            region = YAHOO_REGIONS[country]
            clauses = [EquityQuery("eq", ["region", region])]

            exchanges = PRIMARY_EXCHANGES.get(region)
            if exchanges:
                clauses.append(_any_of(EquityQuery, "exchange", exchanges))
            if yahoo_sectors:
                clauses.append(_any_of(EquityQuery, "sector", yahoo_sectors))
            if min_market_cap:
                local = REGION_CURRENCY.get(region)
                rate = self.get_fx_rate(currency, local) if local else None
                if rate:
                    clauses.append(
                        EquityQuery("gt", ["intradaymarketcap", min_market_cap * rate * 0.5])
                    )

            query = clauses[0] if len(clauses) == 1 else EquityQuery("and", clauses)
            for quote in self._screen_all(yf, query):
                if quote.get("quoteType") != "EQUITY" or not quote.get("symbol"):
                    continue
                ticker = quote["symbol"]
                name = quote.get("longName") or quote.get("shortName") or ticker
                volume = float(quote.get("averageDailyVolume3Month") or 0)
                # Highest volume wins; the ticker breaks ties so the choice is stable.
                if name not in best or (-volume, ticker) < (-best[name][0], best[name][1]):
                    best[name] = (volume, ticker)

        return sorted(ticker for _, ticker in best.values())

    def _screen_all(self, yf: Any, query: Any) -> list[dict]:
        """Every result of a screen, following Yahoo's pagination."""
        quotes: list[dict] = []
        while len(quotes) < SCREEN_MAX_RESULTS:
            page = yf.screen(
                query,
                size=SCREEN_PAGE_SIZE,
                offset=len(quotes),
                sortField="intradaymarketcap",
                sortAsc=False,
            )
            batch = page.get("quotes") or []
            quotes.extend(batch)
            if len(batch) < SCREEN_PAGE_SIZE or len(quotes) >= (page.get("total") or 0):
                break
        return quotes

    def _currency(self, code: str | None, ticker: str) -> Currency:
        """
        Currency a ticker's market cap is quoted in.

        Yahoo reports some exchanges in minor units ("GBp" for London, "ZAc" for
        Johannesburg, "ILA" for Tel Aviv). That applies to the share price; the
        market cap is in whole units, so the minor code maps to its parent.
        """
        iso = parse_currency(code)
        if iso is None:
            if code:
                logger.warning(
                    "Unsupported currency %r for %s; its market cap is treated as USD", code, ticker
                )
            return Currency.USD
        return iso

    def get_fx_rate(self, base: str, quote: str, as_of_date: str | None = None) -> float | None:
        """
        Exchange rate from Yahoo's currency pairs.

        With a date, the last close on or before that date is used, so a build
        for a past date does not move when rerun later.
        """
        if base == quote:
            return 1.0

        key = f"{base}{quote}@{as_of_date or 'latest'}"
        if key in self._fx_cache:
            return self._fx_cache[key]

        yf = self._get_yfinance()
        rate: float | None = None
        try:
            pair = yf.Ticker(f"{base}{quote}=X")
            if as_of_date:
                end = pd.Timestamp(as_of_date) + pd.Timedelta(days=1)
                hist = pair.history(start=end - pd.Timedelta(days=10), end=end)
            else:
                hist = pair.history(period="5d")
            if not hist.empty:
                rate = float(hist["Close"].dropna().iloc[-1])
        except Exception as e:
            logger.warning(f"Error fetching FX rate {base}/{quote}: {e}")

        if rate is not None and rate > 0:
            self._fx_cache[key] = rate
            return rate
        return None

    def _get_stock_info(self, stock, ticker: str) -> dict:
        """Get stock info with caching."""
        if self._cache_enabled and ticker in self._info_cache:
            return self._info_cache[ticker]

        try:
            info: dict = stock.info
            if self._cache_enabled:
                self._info_cache[ticker] = info
            return info
        except Exception:
            return {}

    def _calculate_free_float(self, info: dict) -> float:
        """Calculate free float factor from Yahoo Finance data."""
        shares_outstanding: float = info.get("sharesOutstanding", 0) or 0
        float_shares: float = info.get("floatShares", 0) or 0

        if shares_outstanding and float_shares:
            return float(min(float_shares / shares_outstanding, 1.0))
        return 1.0  # Default to 100% free float

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        """
        Fetch market capitalization for tickers.

        Args:
            tickers: List of ticker symbols
            as_of_date: Date for data (ignored, uses latest)

        Returns:
            Dictionary mapping ticker to market cap
        """
        yf = self._get_yfinance()
        market_caps = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = self._get_stock_info(stock, ticker)
                market_caps[ticker] = info.get("marketCap", 0) or 0
            except Exception as e:
                logger.warning(f"Error fetching market cap for {ticker}: {e}")
                market_caps[ticker] = 0

        return market_caps

    def get_dividends(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch dividend data.

        Args:
            tickers: List of ticker symbols
            start_date: Start date
            end_date: End date

        Returns:
            DataFrame with dividend data
        """
        yf = self._get_yfinance()
        all_dividends = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                dividends = stock.dividends

                # Filter by date range
                if not dividends.empty:
                    dividends = dividends[
                        (dividends.index >= start_date) & (dividends.index <= end_date)
                    ]
                    all_dividends[ticker] = dividends
            except Exception as e:
                logger.warning(f"Error fetching dividends for {ticker}: {e}")

        if not all_dividends:
            return pd.DataFrame()

        return pd.DataFrame(all_dividends)

    def get_splits(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        """
        Fetch stock split data.

        Args:
            tickers: List of ticker symbols
            start_date: Start date
            end_date: End date

        Returns:
            DataFrame with split data
        """
        yf = self._get_yfinance()
        all_splits = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                splits = stock.splits

                # Filter by date range
                if not splits.empty:
                    splits = splits[(splits.index >= start_date) & (splits.index <= end_date)]
                    all_splits[ticker] = splits
            except Exception as e:
                logger.warning(f"Error fetching splits for {ticker}: {e}")

        if not all_splits:
            return pd.DataFrame()

        return pd.DataFrame(all_splits)

    def get_free_float(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        """
        Fetch free float ratio for tickers.

        Args:
            tickers: List of ticker symbols
            as_of_date: Date for data

        Returns:
            Dictionary mapping ticker to free float ratio
        """
        yf = self._get_yfinance()
        free_floats = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = self._get_stock_info(stock, ticker)
                free_floats[ticker] = self._calculate_free_float(info)
            except Exception as e:
                logger.warning(f"Error fetching free float for {ticker}: {e}")
                free_floats[ticker] = 1.0

        return free_floats

    def get_sector(self, tickers: list[str]) -> dict[str, str]:
        """
        Fetch sector classification for tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dictionary mapping ticker to sector
        """
        yf = self._get_yfinance()
        sectors = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = self._get_stock_info(stock, ticker)
                sectors[ticker] = info.get("sector", "Unknown")
            except Exception as e:
                logger.warning(f"Error fetching sector for {ticker}: {e}")
                sectors[ticker] = "Unknown"

        return sectors

    def get_country(self, tickers: list[str]) -> dict[str, str]:
        """
        Fetch country of incorporation for tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dictionary mapping ticker to country
        """
        yf = self._get_yfinance()
        countries = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = self._get_stock_info(stock, ticker)
                countries[ticker] = info.get("country", "Unknown")
            except Exception as e:
                logger.warning(f"Error fetching country for {ticker}: {e}")
                countries[ticker] = "Unknown"

        return countries

    def get_business_descriptions(self, tickers: list[str]) -> dict[str, str]:
        """
        Fetch business descriptions for tickers.

        Args:
            tickers: List of ticker symbols

        Returns:
            Dictionary mapping ticker to business description
        """
        yf = self._get_yfinance()
        descriptions = {}

        for ticker in tickers:
            try:
                stock = yf.Ticker(ticker)
                info = self._get_stock_info(stock, ticker)
                descriptions[ticker] = info.get("longBusinessSummary", "") or ""
            except Exception as e:
                logger.warning(f"Error fetching description for {ticker}: {e}")
                descriptions[ticker] = ""

        return descriptions

    def is_available(self) -> bool:
        """Check if Yahoo Finance is available."""
        try:
            self._get_yfinance()
            return True
        except ImportError:
            return False

    def get_name(self) -> str:
        """Get connector name."""
        return "Yahoo Finance"

    def clear_cache(self) -> None:
        """Clear the data cache."""
        self._price_cache.clear()
        self._info_cache.clear()
        self._fx_cache.clear()
