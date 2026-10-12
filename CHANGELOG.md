# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.3.1] - 2026-10-12

### Fixed

- A screen no longer fails when the data source has no exchange for one of the
  universe's countries (Yahoo has none for Luxembourg, for example). Those
  countries are skipped and reported in `BuildResult.unscreened`, in the JSON
  output and in the table. A screen still fails if none of its countries can
  be searched.
- A build no longer returns a wrong or empty index when Yahoo Finance limits
  requests. Refused requests used to come back as companies with no country
  and no size, which the rules then dropped, so the result depended on how
  many requests got through. Requests are now retried after a wait, and if
  Yahoo keeps refusing the build fails with `DataSourceError`.

### Added

- `DataConnector.unscreenable_countries`.
- `DataSourceError`, raised when a data source cannot answer.
- Yahoo screening for Colombia and the United Arab Emirates.

## [0.3.0] - 2026-10-11

### Added

- **Screened universes.** `universe.screen: true` finds the candidates by
  asking the data source which companies are listed in the universe's
  countries, so a spec can be rules only, with no ticker list. Also available
  as `Universe.builder().screened()` and `DataConnector.screen`.
- The Yahoo connector implements screening. The screen's result is recorded in
  the data snapshot, so a screened build replays exactly like any other.
- The table output summarises exclusions by reason when there are more than 25.

### Changed

- The Yahoo connector fetches company details concurrently and takes the price
  from the quote, which makes large builds several times faster.
- Requires `yfinance` 0.2.58 or newer, for its screener.

### Fixed

- The OpenBB connector labelled every market cap as US dollars.

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
