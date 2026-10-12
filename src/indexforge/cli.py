"""
Command line interface.

    indexforge init my_index.yaml          write a starter spec
    indexforge validate my_index.yaml      check a spec without fetching data
    indexforge build my_index.yaml         build the index and print its members

`build` can record the market data it used (--save-data) and replay it later
(--data), which is what makes a result exactly reproducible by someone else.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from indexforge import __version__
from indexforge.data.connectors.base import DataConnector, DataSourceError
from indexforge.data.connectors.snapshot import SnapshotConnector
from indexforge.spec import BuildResult, SpecError, build, load_spec

#: Above this many, the table summarises exclusions by reason.
MAX_LISTED_EXCLUSIONS = 25

EXAMPLE_SPEC = """\
# An index, described as data. Build it with:  indexforge build <this file>
spec_version: 1
name: US Tech Leaders
identifier: USTECH
currency: USD
base_date: "2025-01-01"
base_value: 1000

universe:
  # Every candidate is listed, so the result does not depend on who runs this.
  tickers: [AAPL, MSFT, NVDA, GOOGL, AMZN, META, AVGO, ORCL, CRM, ADBE]
  # Amounts are in the index currency.
  min_market_cap: 100000000000

selection:
  rank_by: MARKET_CAP
  top: 5

weighting:
  method: market_cap        # equal_weight | market_cap | free_float_market_cap
  max_weight: 0.30

rebalancing:
  frequency: quarterly      # monthly | quarterly | semi_annual | annual
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `indexforge` command. Returns the process exit code."""
    parser = _parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.ERROR,
        format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        result: int = args.handler(args)
        return result
    except SpecError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (OSError, ValueError, NotImplementedError, DataSourceError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="indexforge",
        description="Build rules-based financial indices from a spec file.",
    )
    parser.add_argument("--version", action="version", version=f"indexforge {__version__}")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show progress and data warnings"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")

    init = commands.add_parser("init", help="write a starter spec file")
    init.add_argument("path", nargs="?", default="index.yaml", help="file to create")
    init.add_argument("--force", action="store_true", help="overwrite an existing file")
    init.set_defaults(handler=_init)

    validate = commands.add_parser("validate", help="check a spec without fetching data")
    validate.add_argument("spec", help="path to a spec file (.yaml or .json)")
    validate.set_defaults(handler=_validate)

    build_cmd = commands.add_parser("build", help="build an index and print its members")
    build_cmd.add_argument("spec", help="path to a spec file (.yaml or .json)")
    build_cmd.add_argument(
        "--as-of", metavar="DATE", help="build date as YYYY-MM-DD (default: today)"
    )
    build_cmd.add_argument(
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="output format (default: table)",
    )
    build_cmd.add_argument("-o", "--output", metavar="FILE", help="write to a file, not stdout")
    build_cmd.add_argument(
        "--source",
        choices=("yahoo", "openbb"),
        default="yahoo",
        help="live data source (default: yahoo)",
    )
    build_cmd.add_argument(
        "--data", metavar="FILE", help="replay a recorded data snapshot instead of live data"
    )
    build_cmd.add_argument(
        "--save-data", metavar="FILE", help="record the market data used, for exact replay"
    )
    build_cmd.set_defaults(handler=_build)

    return parser


def _init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.write_text(EXAMPLE_SPEC)
    print(f"Wrote {path}. Build it with: indexforge build {path}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    spec = load_spec(args.spec)
    universe = spec["universe"]
    candidates = (
        f"screens {len(universe['countries'])} countries"
        if universe.get("screen")
        else f"{len(universe['tickers'])} candidates"
    )
    print(f"{args.spec} is valid: {spec['name']} ({candidates})")
    return 0


def _build(args: argparse.Namespace) -> int:
    if args.data and args.save_data:
        print("error: --data and --save-data cannot be used together", file=sys.stderr)
        return 2

    spec = load_spec(args.spec)
    as_of = args.as_of

    connector: DataConnector
    if args.data:
        snapshot = SnapshotConnector.load(args.data)
        connector = snapshot
        # A replay is only the same build if it is for the same date.
        as_of = as_of or snapshot.as_of
    else:
        connector = _live_connector(args.source)

    result = build(spec, as_of=as_of, connector=connector)

    if args.save_data:
        Path(args.save_data).write_text(
            json.dumps(result.snapshot, indent=2, sort_keys=True) + "\n"
        )

    text = _render(result, args.format)
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)

    if not result.constituents:
        print("error: no candidate met the rules", file=sys.stderr)
        return 1
    return 0


def _live_connector(source: str) -> DataConnector:
    if source == "openbb":
        try:
            from indexforge.data.connectors.openbb import OpenBBConnector
        except ImportError:
            OpenBBConnector = None  # type: ignore[assignment,misc]
        if OpenBBConnector is None:
            raise ValueError(
                'The OpenBB source is not installed. Run: pip install "indexforge[openbb]"'
            )
        return OpenBBConnector()

    from indexforge.data.connectors.yahoo import YahooFinanceConnector

    return YahooFinanceConnector()


def _render(result: BuildResult, fmt: str) -> str:
    data = result.to_dict()

    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"

    rows = data["constituents"]
    if fmt == "csv":
        buffer = io.StringIO()
        columns = ["ticker", "name", "sector", "country", "market_cap", "market_cap_currency"]
        writer = csv.DictWriter(buffer, fieldnames=[*columns, "weight"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue()

    lines = [
        f"{data['name']} ({data['identifier']})",
        f"As of {data['as_of']} | {len(rows)} members | data: {data['data_source']}",
        "",
    ]
    if rows:
        width = max(len("Ticker"), *(len(r["ticker"]) for r in rows))
        names = max(len("Name"), *(min(len(r["name"]), 40) for r in rows))
        cap = f"Market cap ({data['currency']})"
        lines.append(f"{'Ticker':<{width}}  {'Name':<{names}}  {cap:>20}  {'Weight':>8}")
        for r in rows:
            note = "" if r["market_cap_currency"] == data["currency"] else " *"
            lines.append(
                f"{r['ticker']:<{width}}  {r['name'][:40]:<{names}}  "
                f"{r['market_cap']:>20,.0f}  {r['weight']:>8.2%}{note}"
            )
    if data["unconverted"]:
        lines += ["", f"* Market cap not converted to {data['currency']} (no exchange rate):"]
        lines += [f"  {t} is in {c}" for t, c in data["unconverted"].items()]
    if data["unscreened"]:
        lines += [
            "",
            f"Not searched ({data['data_source']} covers no exchange there): "
            + ", ".join(data["unscreened"]),
        ]
    exclusions = data["exclusions"]
    if len(exclusions) > MAX_LISTED_EXCLUSIONS:
        # A screen considers hundreds of companies; list the reasons, not each one.
        counts: dict[str, int] = {}
        for reason in exclusions.values():
            counts[reason] = counts.get(reason, 0) + 1
        lines += ["", f"Left out: {len(exclusions)} candidates (use --format json for each one)"]
        lines += [
            f"  {count:>5}  {reason}"
            for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
    elif exclusions:
        lines += ["", "Left out:"]
        lines += [f"  {t}: {reason}" for t, reason in exclusions.items()]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
