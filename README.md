# indexforge

Build rules-based stock indices from a spec file, in Python or from the command line.

[![CI](https://github.com/reubencapio/indexforge/actions/workflows/ci.yml/badge.svg)](https://github.com/reubencapio/indexforge/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/indexforge.svg)](https://pypi.org/project/indexforge/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

An index is a list of candidate companies plus rules: which ones qualify, how
many to keep, how to weight them. indexforge takes that description as a small
YAML file and gives you the members and their weights. Give someone else the
file and they get the same index.

It is the engine behind [IndexMaker](https://www.indexmaker.ai).

## Install

```bash
pip install indexforge
```

Python 3.10 to 3.12. Market data comes from Yahoo Finance by default, with no
API key needed.

## Quick start

```bash
indexforge init my_index.yaml     # write a starter spec
indexforge build my_index.yaml    # fetch data, apply the rules, print the index
```

```text
US Tech Leaders (USTECH)
As of 2026-10-11 | 5 members | data: Yahoo Finance

Ticker  Name                       Market cap (USD)    Weight
NVDA    NVIDIA Corporation        4,000,000,000,000    30.00%
AAPL    Apple Inc.                3,500,000,000,000    28.11%
...

Left out:
  ADBE: ranked outside the selection
  ...
```

Every candidate that did not make it is listed with the reason. (The figures
above are illustrative.)

## The spec

```yaml
spec_version: 1
name: EU Quantum Enablers
identifier: EUQENABL
currency: EUR
base_date: "2025-01-01"
base_value: 1000

universe:
  # Every candidate is listed, so the result does not depend on who runs this.
  tickers: [ASML.AS, IFX.DE, STMPA.PA, BESI.AS, SOI.PA, AIXA.DE, JEN.DE, HO.PA]
  countries: [Germany, France, Netherlands]
  sectors: [Technology, Industrials]
  min_market_cap: 500000000        # in the index currency

selection:
  rank_by: MARKET_CAP
  top: 5

weighting:
  method: equal_weight             # equal_weight | market_cap | free_float_market_cap

rebalancing:
  frequency: quarterly             # monthly | quarterly | semi_annual | annual
```

| Section | Setting | Meaning |
|---|---|---|
| `universe` | `tickers` | The candidates, as Yahoo Finance symbols. Required unless `screen` is true. |
| | `screen` | `true` to find candidates by rule instead. Needs `countries` and `min_market_cap`. |
| | `countries`, `exclude_countries` | Keep or drop by country of domicile. |
| | `sectors`, `exclude_sectors` | Keep or drop by sector. GICS and Yahoo names both work. |
| | `min_market_cap`, `max_market_cap` | Size limits, in the index currency. |
| | `min_average_daily_volume` | Minimum average shares traded per day. |
| | `min_free_float` | Minimum free float, from 0 to 1. |
| `selection` | `rank_by` | Factor to rank by, such as `MARKET_CAP` or `DIVIDEND_YIELD`. |
| | `top` | How many to keep. Omit to keep every candidate that qualifies. |
| | `theme_keywords`, `theme_match` | Keep companies whose description mentions `any` or `all` of the keywords. |
| | `min_dividend_yield` | Minimum dividend yield, as a fraction. |
| `weighting` | `method`, `max_weight` | Weighting scheme and an optional cap per member. |
| `rebalancing` | `frequency` | How often the index is reviewed. |

### Letting the rules find the companies

Instead of listing tickers, a universe can be screened: the data source is
asked which companies are listed in the given countries, and the rules decide
the rest.

```yaml
universe:
  screen: true
  countries: [Germany, France, Netherlands]   # required for a screen
  sectors: [Technology, Industrials]
  min_market_cap: 500000000                   # required: it bounds the search
selection:
  rank_by: MARKET_CAP
  top: 20
```

A screen considers every company listed on those countries' exchanges, fetches
its details, and keeps the ones actually based there. Expect a few hundred
candidates and a run time of tens of seconds. Two things to know:

- Companies are found through their home exchanges. One based in the
  Netherlands but listed only in New York is not found by a Netherlands screen.
- A theme is whatever the keywords match. `theme_keywords: [quantum]` keeps
  every company whose description contains the word, which is repeatable but
  blunt. Read the result before relying on it.

Screening needs a data source that supports it. Yahoo Finance does.

A setting the tool does not recognise is an error. A misspelt rule that is
quietly ignored would give you an index that is not the one you described.

More specs are in [`examples/specs`](examples/specs).

## Getting the same result as someone else

Market data moves, so the same spec built on different days can give different
members. To make a result exactly repeatable, record the data along with it:

```bash
# Build, and save the market data that was used
indexforge build my_index.yaml --save-data my_index.data.json

# Anyone with both files gets the identical index: offline, on any day
indexforge build my_index.yaml --data my_index.data.json
```

The data file holds the figures as the source reported them, the exchange
rates used and, for a screened universe, the list of candidates the screen
returned, so the result can be checked as well as repeated.

Without a data file, a build is repeatable to the extent the data source is:
Yahoo Finance reports current market caps only, so `--as-of` fixes the exchange
rates and the date on the output, not the market caps.

## Command line reference

```text
indexforge init [PATH] [--force]        write a starter spec (default: index.yaml)
indexforge validate SPEC                check a spec without fetching data
indexforge build SPEC                   build the index
    --as-of DATE                        build date, YYYY-MM-DD (default: today)
    --format table|csv|json             output format (default: table)
    -o, --output FILE                   write to a file
    --source yahoo|openbb               live data source (default: yahoo)
    --save-data FILE                    record the market data used
    --data FILE                         replay recorded market data
indexforge -v ...                       show data warnings
```

Exit codes: `0` success, `1` the build failed or no candidate met the rules,
`2` the spec or the arguments are invalid.

## Using it from Python

The same build, as a function:

```python
from indexforge import build

result = build("my_index.yaml")

for c in result.constituents:
    print(f"{c.ticker}: {c.weight:.2%}")

print(result.exclusions)   # {"ADBE": "ranked outside the selection", ...}
```

Or construct an index directly, which also allows custom ranking functions and
filters that a spec file cannot express:

```python
from indexforge import (
    Currency, Factor, Index, RebalancingSchedule, SelectionCriteria,
    Universe, WeightingMethod,
)

index = Index.create(
    name="Tech Leaders",
    identifier="TECHLDRS",
    currency=Currency.USD,
    base_date="2025-01-01",
    base_value=1000,
)

universe = (Universe.builder()
    .tickers(["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META"])
    .min_market_cap(1_000_000_000_000)
    .build()
)

selection = (SelectionCriteria.builder()
    .ranking_by(Factor.MARKET_CAP)
    .select_top(5)
    .build()
)

weighting = WeightingMethod.market_cap().with_cap(max_weight=0.30).build()

(index
    .set_universe(universe)
    .set_selection_criteria(selection)
    .set_weighting_method(weighting)
    .set_rebalancing_schedule(RebalancingSchedule.quarterly())
)

for c in index.get_constituents():
    print(f"{c.ticker}: {c.name} - {c.weight:.2%}")
```

### Your own data source

Implement `DataConnector` to use a database, a vendor feed or a file:

```python
import pandas as pd
from indexforge import Constituent, DataConnector, build

class MyData(DataConnector):
    def get_prices(self, tickers, start_date, end_date):
        return pd.DataFrame(...)

    def get_constituent_data(self, tickers, as_of_date=None):
        return [Constituent(ticker=t, ...) for t in tickers]

    def get_market_cap(self, tickers, as_of_date=None):
        return {t: ... for t in tickers}

    def get_fx_rate(self, base, quote, as_of_date=None):
        return ...   # units of `quote` per unit of `base`, or None

result = build("my_index.yaml", connector=MyData())
```

OpenBB is available as an optional source: `pip install "indexforge[openbb]"`.

## What it does not do

- **It does not judge what a company does.** A screen applies the rules you
  give it to the data it gets; it has no opinion on whether a company "really"
  belongs to a theme.
- **It does not give historical market caps** with the default data source, so
  it cannot rebuild what an index would have held on a past date.
- **It is not investment advice**, and Yahoo Finance data is unofficial and can
  be wrong or missing. Check figures that matter against a primary source.

## Development

```bash
git clone https://github.com/reubencapio/indexforge.git
cd indexforge
poetry install
poetry run pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks CI runs, and
[CHANGELOG.md](CHANGELOG.md) for what changed between versions.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE). If you run a modified version as a
network service, the AGPL requires you to offer its source to your users.
