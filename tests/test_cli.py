"""Tests for the command line interface."""

import csv
import io
import json

import pytest

from indexforge import cli
from indexforge.core.constituent import Constituent
from indexforge.core.types import Currency
from indexforge.data.connectors.snapshot import RecordingConnector
from tests.test_spec import FakeConnector


@pytest.fixture
def spec_path(tmp_path):
    path = tmp_path / "index.yaml"
    assert cli.main(["init", str(path)]) == 0
    return path


@pytest.fixture
def live(monkeypatch):
    """Stand in for the live data source."""
    caps = {"AAPL": 3.5e12, "MSFT": 3.0e12, "NVDA": 4.0e12, "GOOGL": 2.0e12, "AMZN": 2.1e12}
    connector = FakeConnector(
        [
            Constituent(
                ticker=t,
                name=f"{t} Inc.",
                market_cap=cap,
                currency=Currency.USD,
                sector="Technology",
                country="United States",
            )
            for t, cap in caps.items()
        ]
    )
    monkeypatch.setattr(cli, "_live_connector", lambda source: connector)
    return connector


def test_init_writes_a_spec_that_validates(spec_path, capsys):
    assert cli.main(["validate", str(spec_path)]) == 0
    assert "is valid" in capsys.readouterr().out


def test_init_does_not_overwrite(spec_path, capsys):
    assert cli.main(["init", str(spec_path)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert cli.main(["init", str(spec_path), "--force"]) == 0


def test_invalid_spec_exits_2_with_the_reason(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("name: X\nidentifier: X\nuniverse: {tickers: [A], bogus: 1}\n")
    assert cli.main(["validate", str(path)]) == 2
    assert "bogus" in capsys.readouterr().err


def test_build_table_lists_members_and_exclusions(spec_path, live, capsys):
    assert cli.main(["build", str(spec_path), "--as-of", "2026-01-15"]) == 0
    out = capsys.readouterr().out
    assert "US Tech Leaders (USTECH)" in out
    assert "As of 2026-01-15 | 5 members | data: Fake" in out
    assert "META: no market data" in out


def test_build_csv(spec_path, live, capsys):
    assert cli.main(["build", str(spec_path), "--format", "csv"]) == 0
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    assert [r["ticker"] for r in rows] == ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL"]
    assert sum(float(r["weight"]) for r in rows) == pytest.approx(1.0)
    # The 30% cap from the starter spec is applied.
    assert max(float(r["weight"]) for r in rows) <= 0.30 + 1e-9


def test_build_json_to_a_file(spec_path, live, tmp_path):
    out = tmp_path / "out.json"
    assert cli.main(["build", str(spec_path), "--format", "json", "-o", str(out)]) == 0
    data = json.loads(out.read_text())
    assert data["identifier"] == "USTECH"
    assert len(data["constituents"]) == 5


def test_recorded_data_replays_identically_without_the_live_source(
    spec_path, live, tmp_path, capsys, monkeypatch
):
    data = tmp_path / "data.json"
    args = ["build", str(spec_path), "--as-of", "2026-01-15", "--format", "json"]
    assert cli.main([*args, "--save-data", str(data)]) == 0
    first = capsys.readouterr().out

    def no_live(source):
        raise AssertionError("a replay must not touch the live source")

    monkeypatch.setattr(cli, "_live_connector", no_live)
    assert cli.main(["build", str(spec_path), "--format", "json", "--data", str(data)]) == 0
    replay = json.loads(capsys.readouterr().out)

    expected = json.loads(first)
    assert replay["as_of"] == "2026-01-15"  # taken from the snapshot
    assert replay["constituents"] == expected["constituents"]


def test_data_and_save_data_are_mutually_exclusive(spec_path, capsys):
    assert cli.main(["build", str(spec_path), "--data", "a", "--save-data", "b"]) == 2


def test_build_with_no_members_exits_1(spec_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_live_connector", lambda source: FakeConnector([]))
    assert cli.main(["build", str(spec_path)]) == 1
    assert "no candidate met the rules" in capsys.readouterr().err


def test_missing_openbb_gives_install_hint(monkeypatch):
    import indexforge.data.connectors.openbb as openbb_module

    monkeypatch.setattr(openbb_module, "OpenBBConnector", None)
    with pytest.raises(ValueError, match=r"indexforge\[openbb\]"):
        cli._live_connector("openbb")


def test_recording_connector_reports_the_inner_name():
    assert RecordingConnector(FakeConnector([])).get_name() == "Fake"
