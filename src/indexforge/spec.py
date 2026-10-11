"""
Index specifications: an index's rules as a plain data file.

A spec is the complete, portable definition of an index -- the candidate
tickers and every rule applied to them. It holds no code, so it can be stored,
diffed, emailed and rebuilt by anyone:

    indexforge build my_index.yaml

Because the candidates are listed explicitly, the outcome depends only on the
spec and on the market data for the build date. Pair the spec with a recorded
data snapshot and the result is identical for everyone, every time.

Example spec (YAML; JSON works the same way):

    spec_version: 1
    name: European Chip Makers
    identifier: EUCHIPS
    currency: EUR
    base_date: "2025-01-01"
    base_value: 1000
    universe:
      tickers: [ASML.AS, IFX.DE, STMPA.PA, BESI.AS, SOI.PA, AIXA.DE]
      min_market_cap: 2000000000
    selection:
      rank_by: MARKET_CAP
      top: 5
    weighting:
      method: market_cap
      max_weight: 0.3
    rebalancing:
      frequency: quarterly
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from indexforge.core.constituent import Constituent
from indexforge.core.index import Index
from indexforge.core.types import Country, Currency, Factor, Sector
from indexforge.core.universe import Universe
from indexforge.data.connectors.base import DataConnector
from indexforge.data.connectors.snapshot import RecordingConnector
from indexforge.data.provider import DataProvider
from indexforge.rebalancing.schedule import RebalancingSchedule
from indexforge.selection.criteria import SelectionCriteria
from indexforge.weighting.methods import WeightingMethod

SPEC_VERSION = 1

#: Stands in for "keep everything" where the selection needs a count.
_NO_LIMIT = 1_000_000

_TOP_LEVEL = {
    "spec_version",
    "name",
    "identifier",
    "description",
    "currency",
    "base_date",
    "base_value",
    "universe",
    "selection",
    "weighting",
    "rebalancing",
}
_UNIVERSE = {
    "tickers",
    "screen",
    "countries",
    "exclude_countries",
    "sectors",
    "exclude_sectors",
    "min_market_cap",
    "max_market_cap",
    "min_average_daily_volume",
    "min_free_float",
}
_SELECTION = {"rank_by", "top", "theme_keywords", "theme_match", "min_dividend_yield"}
_WEIGHTING = {"method", "max_weight"}
_REBALANCING = {"frequency"}

_WEIGHTING_METHODS = ("equal_weight", "market_cap", "free_float_market_cap")
_FREQUENCIES = ("monthly", "quarterly", "semi_annual", "annual")


class SpecError(ValueError):
    """The spec is malformed. The message says which field and why."""


@dataclass
class BuildResult:
    """Outcome of building an index from a spec."""

    index: Index
    constituents: list[Constituent]
    as_of: str
    data_source: str
    #: Candidates that were left out, with the reason for each.
    exclusions: dict[str, str] = field(default_factory=dict)
    #: Tickers whose market cap could not be converted to the index currency.
    unconverted: dict[str, str] = field(default_factory=dict)
    #: Universe countries the data source has no exchange for, so did not search.
    unscreened: list[str] = field(default_factory=list)
    #: The exact market data used, replayable with SnapshotConnector.
    snapshot: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Plain-data form of the result, with a stable key and row order."""
        currency = self.index.currency.value
        return {
            "identifier": self.index.identifier,
            "name": self.index.name,
            "currency": currency,
            "as_of": self.as_of,
            "data_source": self.data_source,
            "constituents": [
                {
                    "ticker": c.ticker,
                    "name": c.name,
                    "sector": c.sector,
                    "country": c.country,
                    "market_cap": round(c.market_cap, 2),
                    "market_cap_currency": (c.market_cap_currency or c.currency).value,
                    "weight": round(c.weight, 8),
                }
                for c in self.constituents
            ],
            "exclusions": dict(sorted(self.exclusions.items())),
            "unconverted": dict(sorted(self.unconverted.items())),
            "unscreened": sorted(self.unscreened),
        }


def load_spec(path: str | Path) -> dict[str, Any]:
    """
    Read and validate a spec file.

    The format follows the extension: .json is parsed as JSON, anything else as
    YAML (of which JSON is a subset).

    Raises:
        SpecError: If the file cannot be parsed or the spec is invalid
    """
    path = Path(path)
    try:
        text = path.read_text()
    except OSError as e:
        raise SpecError(f"Cannot read {path}: {e.strerror or e}") from e

    try:
        data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise SpecError(f"{path} is not valid {path.suffix.lstrip('.').upper() or 'YAML'}: {e}")

    return validate_spec(data)


def dump_spec(spec: dict[str, Any], path: str | Path) -> None:
    """Write a spec to a file, as JSON or YAML according to the extension."""
    path = Path(path)
    spec = validate_spec(spec)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(spec, indent=2) + "\n")
    else:
        path.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True))


def validate_spec(data: Any) -> dict[str, Any]:
    """
    Check a spec and return it normalised.

    Unknown keys are errors, not warnings: a misspelt rule that is silently
    ignored produces an index that looks right and is not the one described.

    Raises:
        SpecError: Describing the first problem found
    """
    if not isinstance(data, dict):
        raise SpecError("A spec must be a mapping of settings")

    _reject_unknown(data, _TOP_LEVEL, "the spec")

    version = data.get("spec_version", SPEC_VERSION)
    if version != SPEC_VERSION:
        raise SpecError(f"spec_version {version!r} is not supported; this version reads 1")

    for key in ("name", "identifier"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise SpecError(f"'{key}' is required and must be text")

    currency = _enum(Currency, data.get("currency", "USD"), "currency")
    base_date = str(data.get("base_date") or date.today().isoformat())
    _parse_date(base_date, "base_date")
    base_value = _number(data.get("base_value", 1000), "base_value", minimum=0, exclusive=True)

    universe = _section(data, "universe", _UNIVERSE)
    tickers = universe.get("tickers")
    screen = universe.get("screen", False)
    if not isinstance(screen, bool):
        raise SpecError("'universe.screen' must be true or false")
    if screen and tickers:
        raise SpecError(
            "Use either 'universe.tickers' or 'universe.screen: true', not both: "
            "a screen finds the candidates itself"
        )

    clean_universe: dict[str, Any]
    if screen:
        if not universe.get("countries"):
            raise SpecError("'universe.countries' is required when 'universe.screen' is true")
        if universe.get("min_market_cap") is None:
            raise SpecError(
                "'universe.min_market_cap' is required when 'universe.screen' is true, "
                "to bound the search"
            )
        clean_universe = {"screen": True}
    else:
        if not isinstance(tickers, list) or not tickers:
            raise SpecError(
                "'universe.tickers' is required: list every candidate ticker, or set "
                "'universe.screen: true' with countries and a minimum market cap"
            )
        if not all(isinstance(t, str) and t.strip() for t in tickers):
            raise SpecError("'universe.tickers' must be a list of ticker symbols")
        tickers = [t.strip().upper() for t in tickers]
        duplicates = sorted({t for t in tickers if tickers.count(t) > 1})
        if duplicates:
            raise SpecError(f"'universe.tickers' lists {', '.join(duplicates)} more than once")
        clean_universe = {"tickers": tickers}

    for key in ("countries", "exclude_countries"):
        if universe.get(key):
            clean_universe[key] = [
                _enum(Country, v, f"universe.{key}").value for v in universe[key]
            ]
    for key in ("sectors", "exclude_sectors"):
        if universe.get(key):
            clean_universe[key] = [_enum(Sector, v, f"universe.{key}").value for v in universe[key]]
    for key in ("min_market_cap", "max_market_cap", "min_average_daily_volume"):
        if universe.get(key) is not None:
            clean_universe[key] = _number(universe[key], f"universe.{key}", minimum=0)
    if universe.get("min_free_float") is not None:
        clean_universe["min_free_float"] = _number(
            universe["min_free_float"], "universe.min_free_float", minimum=0, maximum=1
        )

    selection = _section(data, "selection", _SELECTION)
    clean_selection: dict[str, Any] = {}
    if selection.get("rank_by") is not None:
        clean_selection["rank_by"] = _enum(Factor, selection["rank_by"], "selection.rank_by").value
    if selection.get("top") is not None:
        top = selection["top"]
        if not isinstance(top, int) or isinstance(top, bool) or top < 1:
            raise SpecError("'selection.top' must be a whole number of at least 1")
        clean_selection["top"] = top
    if selection.get("theme_keywords"):
        keywords = selection["theme_keywords"]
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise SpecError("'selection.theme_keywords' must be a list of words or phrases")
        clean_selection["theme_keywords"] = keywords
        match = selection.get("theme_match", "any")
        if match not in ("any", "all"):
            raise SpecError("'selection.theme_match' must be 'any' or 'all'")
        clean_selection["theme_match"] = match
    if selection.get("min_dividend_yield") is not None:
        clean_selection["min_dividend_yield"] = _number(
            selection["min_dividend_yield"], "selection.min_dividend_yield", minimum=0
        )

    weighting = _section(data, "weighting", _WEIGHTING)
    method = str(weighting.get("method", "equal_weight")).lower()
    if method not in _WEIGHTING_METHODS:
        raise SpecError(f"'weighting.method' must be one of: {', '.join(_WEIGHTING_METHODS)}")
    clean_weighting: dict[str, Any] = {"method": method}
    if weighting.get("max_weight") is not None:
        clean_weighting["max_weight"] = _number(
            weighting["max_weight"], "weighting.max_weight", minimum=0, maximum=1, exclusive=True
        )

    rebalancing = _section(data, "rebalancing", _REBALANCING)
    frequency = str(rebalancing.get("frequency", "quarterly")).lower().replace("-", "_")
    if frequency not in _FREQUENCIES:
        raise SpecError(f"'rebalancing.frequency' must be one of: {', '.join(_FREQUENCIES)}")

    spec: dict[str, Any] = {
        "spec_version": SPEC_VERSION,
        "name": data["name"].strip(),
        "identifier": data["identifier"].strip(),
    }
    if data.get("description"):
        spec["description"] = str(data["description"])
    spec.update(
        {
            "currency": currency.value,
            "base_date": base_date,
            "base_value": base_value,
            "universe": clean_universe,
        }
    )
    if clean_selection:
        spec["selection"] = clean_selection
    spec["weighting"] = clean_weighting
    spec["rebalancing"] = {"frequency": frequency}
    return spec


def index_from_spec(spec: dict[str, Any]) -> Index:
    """Build a configured Index from a spec. No market data is fetched."""
    spec = validate_spec(spec)

    index = Index.create(
        name=spec["name"],
        identifier=spec["identifier"],
        currency=spec["currency"],
        base_date=spec["base_date"],
        base_value=spec["base_value"],
    )

    rules = spec["universe"]
    universe = Universe.builder().currency(Currency(spec["currency"]))
    if rules.get("screen"):
        universe.screened()
    else:
        universe.tickers(rules["tickers"])
    if rules.get("countries"):
        universe.countries(rules["countries"])
    if rules.get("exclude_countries"):
        universe.exclude_countries(rules["exclude_countries"])
    if rules.get("sectors"):
        universe.sectors(rules["sectors"])
    if rules.get("exclude_sectors"):
        universe.exclude_sectors(rules["exclude_sectors"])
    if rules.get("min_market_cap") is not None:
        universe.min_market_cap(rules["min_market_cap"])
    if rules.get("max_market_cap") is not None:
        universe.max_market_cap(rules["max_market_cap"])
    if rules.get("min_average_daily_volume") is not None:
        universe.min_average_daily_volume(rules["min_average_daily_volume"])
    if rules.get("min_free_float") is not None:
        universe.min_free_float(rules["min_free_float"])
    index.set_universe(universe.build())

    selection = spec.get("selection")
    if selection:
        criteria = SelectionCriteria.builder()
        criteria.ranking_by(Factor(selection.get("rank_by", Factor.MARKET_CAP.value)))
        # With no `top`, every candidate that passes the filters is kept.
        criteria.select_top(selection.get("top", _NO_LIMIT))
        if selection.get("theme_keywords"):
            criteria.theme_filter(selection["theme_keywords"], selection.get("theme_match", "any"))
        if selection.get("min_dividend_yield") is not None:
            criteria.custom_filter(_min_dividend_yield(selection["min_dividend_yield"]))
        index.set_selection_criteria(criteria.build())

    weighting = spec["weighting"]
    max_weight = weighting.get("max_weight")
    if weighting["method"] == "equal_weight":
        index.set_weighting_method(WeightingMethod.equal_weight())
    else:
        builder = (
            WeightingMethod.market_cap()
            if weighting["method"] == "market_cap"
            else WeightingMethod.free_float_market_cap()
        )
        if max_weight is not None:
            builder.with_cap(max_weight=max_weight)
        index.set_weighting_method(builder.build())

    index.set_rebalancing_schedule(
        RebalancingSchedule.builder().frequency(spec["rebalancing"]["frequency"]).build()
    )
    return index


def build(
    spec: dict[str, Any] | str | Path,
    as_of: str | None = None,
    connector: DataConnector | None = None,
) -> BuildResult:
    """
    Build an index from a spec: fetch data, apply the rules, weight the members.

    Args:
        spec: A spec mapping, or the path to a spec file
        as_of: Build date (YYYY-MM-DD); today if omitted
        connector: Data source; Yahoo Finance if omitted

    Returns:
        The members with their weights, the candidates left out and why, and a
        snapshot of the data used.

    Example:
        >>> result = build("my_index.yaml")
        >>> for c in result.constituents:
        ...     print(c.ticker, f"{c.weight:.2%}")
    """
    if isinstance(spec, (str, Path)):
        spec = load_spec(spec)
    index = index_from_spec(spec)

    as_of = as_of or date.today().isoformat()
    _parse_date(as_of, "as_of")

    if connector is None:
        from indexforge.data.connectors.yahoo import YahooFinanceConnector

        connector = YahooFinanceConnector()

    recorder = RecordingConnector(connector)
    index.set_data_provider(
        DataProvider.builder().add_source("spec", recorder).set_default("spec").build()
    )

    constituents = index.get_constituents(as_of)
    return BuildResult(
        index=index,
        constituents=constituents,
        as_of=as_of,
        data_source=connector.get_name(),
        exclusions=index.exclusions,
        unconverted=index.unconverted,
        unscreened=index.unscreened,
        snapshot=recorder.snapshot(),
    )


def _min_dividend_yield(minimum: float) -> Callable[[Constituent], bool]:
    def passes(constituent: Constituent) -> bool:
        return (constituent.dividend_yield or 0) >= minimum

    return passes


# ==================== Validation helpers ====================


def _reject_unknown(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SpecError(
            f"Unknown setting in {where}: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def _section(data: dict[str, Any], name: str, allowed: set[str]) -> dict[str, Any]:
    section = data.get(name) or {}
    if not isinstance(section, dict):
        raise SpecError(f"'{name}' must be a mapping of settings")
    _reject_unknown(section, allowed, f"'{name}'")
    return section


def _enum(enum_cls: Any, value: Any, where: str) -> Any:
    """Match an enum by value ("Germany") or by member name ("GERMANY")."""
    if isinstance(value, str):
        wanted = value.strip()
        for member in enum_cls:
            if wanted.lower() == member.value.lower():
                return member
        name = wanted.upper().replace(" ", "_").replace("-", "_")
        if name in enum_cls.__members__:
            return enum_cls[name]
    raise SpecError(f"'{where}' has an unrecognised value: {value!r}")


def _number(
    value: Any,
    where: str,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(f"'{where}' must be a number, not {value!r}")
    if minimum is not None and (value < minimum or (exclusive and value == minimum)):
        raise SpecError(f"'{where}' must be {'above' if exclusive else 'at least'} {minimum}")
    if maximum is not None and value > maximum:
        raise SpecError(f"'{where}' must be at most {maximum}")
    return float(value)


def _parse_date(value: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise SpecError(f"'{where}' must be a date written as YYYY-MM-DD, not {value!r}") from None
