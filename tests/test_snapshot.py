"""The GitHub Actions snapshot backend."""

import asyncio
import json

import pytest

from numtrail import Result, Status
from numtrail import snapshot as snap


@pytest.fixture()
def fake_engine(monkeypatch):
    """Replace the network with canned results so the test stays offline."""
    seen = []

    async def fake_run_checks(phone, *, only=None, settings=None, **kwargs):
        seen.append(phone.e164)
        return [
            Result(module="my_number", domain="mcmc.gov.my", status=Status.FOUND,
                   detail="mobile · 012 block · allocated to Maxis", tier="offline"),
            Result(module="instagram", domain="instagram.com", status=Status.RATE_LIMITED,
                   detail="429", tier="legacy"),
            Result(module="numverify", domain="numverify.com", status=Status.NEEDS_CREDENTIALS,
                   detail="set NUMVERIFY_KEY", tier="api"),
        ]

    monkeypatch.setattr(snap, "run_checks", fake_run_checks)
    return seen


def test_watchlist_ignores_comments_and_blanks(tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_text(
        "# a comment\n\n+60123456789  # trailing note\n+15550100\n   \n#+601112345678\n",
        encoding="utf-8",
    )
    assert snap.read_watchlist(path) == ["+60123456789", "+15550100"]


def test_build_snapshot_shape(fake_engine):
    result = asyncio.run(snap.build_snapshot(["+60123456789", "+15550100"]))
    assert fake_engine == ["+60123456789", "+15550100"]
    assert result["generator"] == "numtrail.snapshot"
    assert result["watchlist_size"] == 2
    assert set(result["entries"]) == {"+60123456789", "+15550100"}

    entry = result["entries"]["+60123456789"]
    assert entry["iso"] == "MY"
    assert entry["conclusive"] == 1                      # only the offline probe
    assert entry["found"] == ["mcmc.gov.my"]
    assert "T" in entry["probed_at"] and entry["probed_at"].endswith("Z")
    assert {r["status"] for r in entry["results"]} == {"found", "rate_limited", "needs_credentials"}


def test_build_snapshot_skips_unparseable_lines(fake_engine):
    result = asyncio.run(snap.build_snapshot(["+60123456789", "not-a-number"]))
    assert result["watchlist_size"] == 1
    assert result["skipped_lines"] and "not-a-number" in result["skipped_lines"][0]


def test_build_snapshot_reports_credential_presence(fake_engine, monkeypatch):
    monkeypatch.setenv("NUMVERIFY_KEY", "k")
    result = asyncio.run(snap.build_snapshot(["+60123456789"]))
    assert result["credentials_present"] == ["NUMVERIFY_KEY"]
    assert result["proxy"] is False


def test_write_and_reload(fake_engine, tmp_path):
    result = asyncio.run(snap.build_snapshot(["+60123456789"]))
    path = snap.write_snapshot(result, tmp_path / "snapshots.json")
    reloaded = json.loads(path.read_text())
    assert reloaded["entries"]["+60123456789"]["found"] == ["mcmc.gov.my"]


def test_summary_mentions_numbers_and_tallies(fake_engine):
    result = asyncio.run(snap.build_snapshot(["+60123456789"]))
    text = snap.summarize(result)
    assert "+60123456789" in text
    assert "found=1" in text and "needs_credentials=1" in text


def test_cli_refuses_without_watchlist(tmp_path, capsys):
    code = snap.main(["--watchlist", str(tmp_path / "missing.txt"),
                      "--out", str(tmp_path / "out.json")])
    assert code == 2
    assert "no watchlist" in capsys.readouterr().out


def test_committed_snapshot_is_valid_json():
    """The file the Pages site fetches must parse and look right."""
    from pathlib import Path

    path = Path("numtrail/web/static/snapshots.json")
    assert path.is_file(), "run: python -m numtrail.snapshot"
    data = json.loads(path.read_text())
    assert data["generator"] == "numtrail.snapshot"
    assert data["entries"], "snapshot has no entries"
    for number, entry in data["entries"].items():
        assert number.startswith("+")
        assert isinstance(entry["results"], list)
