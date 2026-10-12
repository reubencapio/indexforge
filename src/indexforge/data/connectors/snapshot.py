"""
Recording and replaying market data.

Live market data changes by the minute, so two people running the same index
rules an hour apart can get different members. A snapshot removes that: one run
records exactly the data it used, and anyone holding the file can replay it and
get the same result, offline, on any day.
"""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from indexforge.core.constituent import Constituent
from indexforge.data.connectors.base import DataConnector

SNAPSHOT_VERSION = 1


def _screen_request(
    countries: list[str], sectors: list[str] | None, min_market_cap: float | None, currency: str
) -> dict[str, Any]:
    """The inputs of a screen in a canonical form, for comparing two requests."""
    return {
        "countries": sorted(countries),
        "sectors": sorted(sectors or []),
        "min_market_cap": float(min_market_cap) if min_market_cap is not None else None,
        "currency": currency,
    }


class RecordingConnector(DataConnector):
    """
    Wraps another connector and keeps a copy of everything it returned.

    Example:
        >>> recorder = RecordingConnector(YahooFinanceConnector())
        >>> # ... build an index with it ...
        >>> recorder.save("data.json")
    """

    def __init__(self, inner: DataConnector) -> None:
        self._inner = inner
        self._constituents: dict[str, dict[str, Any]] = {}
        self._fx: dict[str, float] = {}
        self._as_of: str | None = None
        self._screen: dict[str, Any] | None = None
        self.PROVIDES = inner.PROVIDES  # type: ignore[misc]

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        return self._inner.get_prices(tickers, start_date, end_date)

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        constituents = self._inner.get_constituent_data(tickers, as_of_date)
        self._as_of = as_of_date
        for constituent in constituents:
            # Copied to a dict now: the caller converts currencies in place.
            self._constituents[constituent.ticker] = constituent.to_dict()
        return constituents

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        return self._inner.get_market_cap(tickers, as_of_date)

    def get_fx_rate(self, base: str, quote: str, as_of_date: str | None = None) -> float | None:
        rate = self._inner.get_fx_rate(base, quote, as_of_date)
        if rate is not None and base != quote:
            self._fx[f"{base}/{quote}"] = rate
        return rate

    def screen(
        self,
        countries: list[str],
        sectors: list[str] | None = None,
        min_market_cap: float | None = None,
        currency: str = "USD",
    ) -> list[str]:
        tickers = self._inner.screen(countries, sectors, min_market_cap, currency)
        self._screen = {
            "request": _screen_request(countries, sectors, min_market_cap, currency),
            "tickers": list(tickers),
            "unscreened": self._inner.unscreenable_countries(countries),
        }
        return tickers

    def unscreenable_countries(self, countries: list[str]) -> list[str]:
        return self._inner.unscreenable_countries(countries)

    def get_name(self) -> str:
        return self._inner.get_name()

    def snapshot(self) -> dict[str, Any]:
        """Everything recorded so far, in the format SnapshotConnector reads."""
        snapshot: dict[str, Any] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "source": self._inner.get_name(),
            "as_of": self._as_of,
            "fx_rates": dict(sorted(self._fx.items())),
            "constituents": [self._constituents[t] for t in sorted(self._constituents)],
        }
        if self._screen is not None:
            snapshot["screen"] = self._screen
        return snapshot

    def save(self, path: str | Path) -> None:
        """Write the snapshot to a JSON file."""
        Path(path).write_text(json.dumps(self.snapshot(), indent=2, sort_keys=True) + "\n")


class SnapshotConnector(DataConnector):
    """
    Serves data from a recorded snapshot instead of a live source.

    Tickers missing from the snapshot are simply not returned, and are reported
    by the index as having no market data.

    Example:
        >>> connector = SnapshotConnector.load("data.json")
    """

    def __init__(self, snapshot: dict[str, Any]) -> None:
        version = snapshot.get("snapshot_version")
        if version != SNAPSHOT_VERSION:
            raise ValueError(
                f"Unsupported snapshot version {version!r}; expected {SNAPSHOT_VERSION}"
            )
        self._snapshot = snapshot
        self._rows: dict[str, dict[str, Any]] = {
            row["ticker"]: row for row in snapshot.get("constituents", [])
        }
        self._fx: dict[str, float] = dict(snapshot.get("fx_rates", {}))

    @classmethod
    def load(cls, path: str | Path) -> "SnapshotConnector":
        """Read a snapshot written by RecordingConnector.save."""
        return cls(json.loads(Path(path).read_text()))

    @property
    def as_of(self) -> str | None:
        """Date the snapshot was recorded for."""
        return self._snapshot.get("as_of")

    def get_prices(self, tickers: list[str], start_date: str, end_date: str) -> pd.DataFrame:
        # A snapshot holds one point in time, not a price history.
        return pd.DataFrame()

    def get_constituent_data(
        self, tickers: list[str], as_of_date: str | None = None
    ) -> list[Constituent]:
        # A fresh object per call: the index converts currencies in place.
        return [Constituent.from_dict(self._rows[t]) for t in tickers if t in self._rows]

    def get_market_cap(self, tickers: list[str], as_of_date: str | None = None) -> dict[str, float]:
        return {t: self._rows[t].get("market_cap", 0.0) for t in tickers if t in self._rows}

    def get_fx_rate(self, base: str, quote: str, as_of_date: str | None = None) -> float | None:
        if base == quote:
            return 1.0
        return self._fx.get(f"{base}/{quote}")

    def screen(
        self,
        countries: list[str],
        sectors: list[str] | None = None,
        min_market_cap: float | None = None,
        currency: str = "USD",
    ) -> list[str]:
        recorded = self._snapshot.get("screen")
        if recorded is None:
            raise ValueError(
                "This snapshot holds no screen: it was recorded for a universe with listed tickers"
            )
        # The candidate list is only valid for the rules it was produced by.
        if recorded.get("request") != _screen_request(countries, sectors, min_market_cap, currency):
            raise ValueError(
                "This snapshot was recorded for different universe rules "
                "(countries, sectors, minimum market cap or currency). Build without --data."
            )
        return list(recorded["tickers"])

    def unscreenable_countries(self, countries: list[str]) -> list[str]:
        return list((self._snapshot.get("screen") or {}).get("unscreened", []))

    def get_name(self) -> str:
        return f"Snapshot ({self._snapshot.get('source', 'unknown source')})"
