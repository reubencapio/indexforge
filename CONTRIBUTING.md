# Contributing

Thanks for helping. Bug reports and pull requests are welcome.

## Setup

```bash
git clone https://github.com/reubencapio/indexforge.git
cd indexforge
poetry install
poetry run pre-commit install
```

## Before opening a pull request

```bash
poetry run pytest                      # tests
poetry run black src tests examples    # format
poetry run ruff check src tests examples
poetry run mypy src/indexforge
```

CI runs the same four checks on Python 3.10, 3.11 and 3.12.

## Guidelines

- **Tests do not touch the network.** Use a fake `DataConnector` (see
  `tests/test_spec.py`) so results do not depend on live market data.
- **Same input, same output.** Anything that affects which companies are
  selected or how they are weighted must be deterministic.
- **A spec setting that is accepted must be applied.** If a rule cannot be
  honoured, reject it with a `SpecError` that names the field.
- Add a line to `CHANGELOG.md` for anything a user would notice.

## Releasing

1. Update the version in `pyproject.toml` and `src/indexforge/__init__.py`.
2. Move the changelog entries under the new version.
3. Publish a GitHub release tagged `vX.Y.Z`. The publish workflow uploads it to PyPI.

## License

By contributing you agree that your contributions are licensed under AGPL-3.0-or-later.
