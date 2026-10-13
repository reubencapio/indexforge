"""
A store of company details, so they are fetched once and not on every build.

A company's country, sector and business description change rarely, but most
data sources hand them out one company per request and limit how many requests
they accept. Keeping the answers means a screen of a few thousand companies
costs a handful of requests after the first time, and every build reads the
same details.
"""

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path

from indexforge.core.constituent import Constituent


class CompanyStore(ABC):
    """
    Where company details are kept between builds.

    Details are kept per data source, since sources disagree on sector names
    and on what a company's description says.
    """

    @abstractmethod
    def get(self, source: str, tickers: list[str], max_age: timedelta) -> dict[str, Constituent]:
        """The stored companies among `tickers` that are no older than `max_age`."""

    @abstractmethod
    def put(self, source: str, companies: list[Constituent]) -> None:
        """Store companies, replacing what was stored for the same tickers."""

    def want(self, source: str, tickers: list[str]) -> None:  # noqa: B027
        """
        Note tickers whose details could not be fetched this time.

        A store with something to fetch them later (a scheduled job, say) can
        use this to know what to fetch. The default does nothing.
        """


class MemoryCompanyStore(CompanyStore):
    """A store that lasts as long as the process. Useful in tests."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[datetime, dict]] = {}
        self.wanted: list[tuple[str, str]] = []

    def get(self, source: str, tickers: list[str], max_age: timedelta) -> dict[str, Constituent]:
        oldest = datetime.now(timezone.utc) - max_age
        found = {}
        for ticker in tickers:
            row = self._rows.get((source, ticker))
            if row and row[0] >= oldest:
                found[ticker] = Constituent.from_dict(row[1])
        return found

    def put(self, source: str, companies: list[Constituent]) -> None:
        now = datetime.now(timezone.utc)
        for company in companies:
            self._rows[(source, company.ticker)] = (now, company.to_dict())

    def want(self, source: str, tickers: list[str]) -> None:
        self.wanted.extend((source, ticker) for ticker in tickers)


class SqliteCompanyStore(CompanyStore):
    """
    A store in one SQLite file.

    Example:
        >>> store = SqliteCompanyStore("companies.sqlite")
        >>> connector = CachedConnector(YahooFinanceConnector(), store)
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS companies ("
                " source TEXT NOT NULL, ticker TEXT NOT NULL,"
                " fetched_at TEXT NOT NULL, data TEXT NOT NULL,"
                " PRIMARY KEY (source, ticker))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def get(self, source: str, tickers: list[str], max_age: timedelta) -> dict[str, Constituent]:
        oldest = (datetime.now(timezone.utc) - max_age).isoformat()
        found = {}
        with self._lock, self._connect() as db:
            # In batches: SQLite limits how many values one query may bind.
            for start in range(0, len(tickers), 500):
                batch = tickers[start : start + 500]
                marks = ",".join("?" * len(batch))
                rows = db.execute(
                    f"SELECT ticker, data FROM companies"  # noqa: S608 - only "?" marks are interpolated
                    f" WHERE source = ? AND fetched_at >= ? AND ticker IN ({marks})",
                    [source, oldest, *batch],
                )
                for ticker, data in rows:
                    found[ticker] = Constituent.from_dict(json.loads(data))
        return found

    def put(self, source: str, companies: list[Constituent]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as db:
            db.executemany(
                "INSERT OR REPLACE INTO companies (source, ticker, fetched_at, data)"
                " VALUES (?, ?, ?, ?)",
                [(source, c.ticker, now, json.dumps(c.to_dict())) for c in companies],
            )
