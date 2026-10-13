"""Data connectors for various data sources."""

from indexforge.data.connectors.base import DataConnector, DataSourceError
from indexforge.data.connectors.cached import CachedConnector
from indexforge.data.connectors.finnhub import FinnhubConnector
from indexforge.data.connectors.fmp import FMPConnector
from indexforge.data.connectors.snapshot import RecordingConnector, SnapshotConnector
from indexforge.data.connectors.yahoo import YahooFinanceConnector

# Optional connectors (may require additional dependencies)
try:
    from indexforge.data.connectors.openbb import OpenBBConnector
except ImportError:
    OpenBBConnector = None  # type: ignore

__all__ = [
    "DataConnector",
    "DataSourceError",
    "CachedConnector",
    "FMPConnector",
    "FinnhubConnector",
    "YahooFinanceConnector",
    "RecordingConnector",
    "SnapshotConnector",
    "OpenBBConnector",
]
