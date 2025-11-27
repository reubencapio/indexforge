"""
Index Maker - A domain-driven Python module for creating financial indices.

This module provides an intuitive, type-safe API for creating, managing,
and analyzing financial indices. Designed for index professionals.

Example:
    >>> from index_maker import Index, Universe, WeightingMethod, Currency
    >>>
    >>> index = Index.create(
    ...     name="Tech Leaders Index",
    ...     identifier="TECHLDRS",
    ...     currency=Currency.USD,
    ...     base_date="2025-01-01",
    ...     base_value=1000.0
    ... )
    >>>
    >>> universe = Universe.from_tickers(["AAPL", "MSFT", "GOOGL"])
    >>> index.set_universe(universe)
    >>> index.set_weighting_method(WeightingMethod.equal_weight())
    >>>
    >>> value = index.calculate(date="2025-11-15")
"""

from index_maker.core.constituent import Constituent
from index_maker.core.index import Index
from index_maker.core.types import (
    AssetClass,
    Currency,
    Factor,
    IndexType,
    Industry,
    Region,
    Sector,
)
from index_maker.core.universe import Universe, UniverseBuilder
from index_maker.data.connectors.base import DataConnector
from index_maker.data.connectors.yahoo import YahooFinanceConnector
from index_maker.data.provider import DataProvider, DataProviderBuilder
from index_maker.rebalancing.schedule import RebalancingSchedule, RebalancingScheduleBuilder
from index_maker.selection.composite import CompositeScore, CompositeScoreBuilder
from index_maker.selection.criteria import SelectionCriteria, SelectionCriteriaBuilder
from index_maker.validation.report import ValidationReport
from index_maker.validation.rules import ValidationRules, ValidationRulesBuilder
from index_maker.weighting.methods import WeightingMethod, WeightingMethodBuilder

__version__ = "0.1.0"

__all__ = [
    # Core
    "Index",
    "Universe",
    "UniverseBuilder",
    "Constituent",
    # Types
    "Currency",
    "IndexType",
    "AssetClass",
    "Region",
    "Factor",
    "Sector",
    "Industry",
    # Selection
    "SelectionCriteria",
    "SelectionCriteriaBuilder",
    "CompositeScore",
    "CompositeScoreBuilder",
    # Weighting
    "WeightingMethod",
    "WeightingMethodBuilder",
    # Rebalancing
    "RebalancingSchedule",
    "RebalancingScheduleBuilder",
    # Data
    "DataProvider",
    "DataProviderBuilder",
    "DataConnector",
    "YahooFinanceConnector",
    # Validation
    "ValidationRules",
    "ValidationRulesBuilder",
    "ValidationReport",
]
