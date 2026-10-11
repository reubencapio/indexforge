# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-11

First release from this repository. The library previously lived inside the
`indexmaker` web application's repository.

### Added

- **Index specs.** An index can be described as a YAML or JSON file and built
  with `indexforge.build()`. Unknown settings are errors, not warnings.
- **Command line tool.** `indexforge init`, `indexforge validate` and
  `indexforge build`, with table, CSV and JSON output.
- **Reproducible builds.** `indexforge build --save-data` records the market
  data a build used; `--data` replays it, offline, to the identical result.
  Also available as `RecordingConnector` and `SnapshotConnector`.
- **Exclusion reasons.** `Index.exclusions` says why each candidate was left out.
- **Currency conversion.** Market caps are converted to the index currency
  before they are compared. `DataConnector.get_fx_rate` is the new hook; the
  Yahoo connector implements it. 19 more currencies are recognised.
- `Sector.canonical`, so "Technology" and "Information Technology" (and the
  other Yahoo and GICS spellings) match each other in universe rules.
- `py.typed` marker, so type checkers use the package's annotations.

### Changed

- `Index.get_constituents()` with no date now builds the index as of today on
  first use. It used to return an empty list until `calculate()` had been called.
- `Universe.min_market_cap` and `max_market_cap` are in the index currency.
- Installing the package no longer pulls in a web framework, task queue or
  database drivers. OpenBB moved to the optional `openbb` extra.
- `Constituent.to_dict()` and `from_dict()` now round-trip every data field.

### Fixed

- Stocks quoted in a currency outside the old 14-currency list (Swedish kronor,
  for example) were treated as US dollars, overstating their size roughly tenfold.

## [0.1.5] and earlier

Released from the `indexmaker` repository; see its history.
