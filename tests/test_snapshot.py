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


# ---------------------------------------------------------------- Run workflow
# Clicking "Run workflow" in the Actions tab passes a number into --number; the
# run must probe that number, keep the watchlist intact, and not lose the
# on-demand numbers from previous runs.


@pytest.mark.parametrize("raw, expected", [
    ("+60123456789", ["+60123456789"]),
    ("+60 12-345 6789", ["+60123456789"]),
    ("+60123456789, +601112345678", ["+60123456789", "+601112345678"]),
    ("+60123456789;+15550100", ["+60123456789", "+15550100"]),
    ("60123456789", ["60123456789"]),
    ("", []),
    ("  ,  ", []),
])
def test_dispatch_input_is_scraped_into_numbers(raw, expected):
    assert snap.parse_numbers(raw) == expected


def test_merge_keeps_previous_on_demand_numbers(fake_engine, tmp_path):
    watch = ["+60123456789"]
    first = asyncio.run(snap.build_snapshot(watch + ["+60199998888"],
                                            source="watchlist"))
    # mark the second one as on-demand, the way the CLI does
    first["entries"]["+60199998888"]["source"] = "dispatch"
    first = snap.merge_snapshot(first, None, watchlist=watch)

    second = asyncio.run(snap.build_snapshot(watch, source="watchlist"))
    merged = snap.merge_snapshot(second, first, watchlist=watch)

    assert set(merged["entries"]) == {"+60123456789", "+60199998888"}
    assert merged["entries"]["+60199998888"]["source"] == "dispatch"
    assert merged["watchlist_size"] == 1
    assert merged["dispatch_size"] == 1


def test_merge_drops_numbers_removed_from_the_watchlist(fake_engine):
    old = asyncio.run(snap.build_snapshot(["+60123456789", "+15550100"],
                                          source="watchlist"))
    old_entry = old["entries"].pop("+60123456789")
    old["entries"]["+15550100"]["source"] = "watchlist"

    fresh = asyncio.run(snap.build_snapshot(["+60123456789"], source="watchlist"))
    merged = snap.merge_snapshot(fresh, old, watchlist=["+60123456789"])
    assert "+15550100" not in merged["entries"]
    assert old_entry["source"] == "watchlist"


def test_merge_caps_carried_over_numbers_newest_first(fake_engine):
    stale = {"generated_at": "2026-01-01T00:00:00Z", "entries": {
        f"+1555010{i}": {"iso": "US", "probed_at": f"2026-01-0{i + 1}T00:00:00Z",
                         "source": "dispatch", "conclusive": 0, "found": [],
                         "results": []}
        for i in range(5)
    }}
    fresh = asyncio.run(snap.build_snapshot(["+60123456789"], source="watchlist"))
    merged = snap.merge_snapshot(fresh, stale, watchlist=["+60123456789"],
                                 keep_dispatch=2)
    carried = [n for n, e in merged["entries"].items() if e.get("source") == "dispatch"]
    assert len(carried) == 2
    # newest probed_at wins
    assert set(carried) == {"+15550103", "+15550104"}


def test_main_probes_the_number_from_the_run_workflow_form(fake_engine, tmp_path,
                                                           monkeypatch):
    watch = tmp_path / "watchlist.txt"
    watch.write_text("+60123456789\n", encoding="utf-8")
    out = tmp_path / "snapshots.json"
    monkeypatch.setenv("INPUT_NUMBER", "")

    code = snap.main(["--watchlist", str(watch), "--out", str(out),
                      "--number", "+60199998888"])
    assert code == 0
    data = json.loads(out.read_text())
    assert data["entries"]["+60199998888"]["source"] == "dispatch"
    assert data["entries"]["+60123456789"]["source"] == "watchlist"
    assert fake_engine == ["+60123456789", "+60199998888"]


def test_main_skip_watchlist_probes_only_the_requested_number(fake_engine, tmp_path):
    watch = tmp_path / "watchlist.txt"
    watch.write_text("+60123456789\n", encoding="utf-8")
    out = tmp_path / "snapshots.json"

    code = snap.main(["--watchlist", str(watch), "--out", str(out),
                      "--skip-watchlist", "--number", "+60199998888"])
    assert code == 0
    data = json.loads(out.read_text())
    assert list(data["entries"]) == ["+60199998888"]
    assert fake_engine == ["+60199998888"]


def test_main_without_watchlist_or_number_is_an_error(tmp_path):
    assert snap.main(["--watchlist", str(tmp_path / "nope.txt"),
                      "--out", str(tmp_path / "s.json")]) == 2
    assert snap.main(["--watchlist", str(tmp_path / "nope.txt"),
                      "--skip-watchlist", "--out", str(tmp_path / "s.json")]) == 2


def test_reconcile_keeps_both_runs_numbers(tmp_path, fake_engine):
    """Two runs probed at once; the slower push must not drop either number."""
    fresh_path = tmp_path / "fresh.json"
    remote_path = tmp_path / "remote.json"
    watch = tmp_path / "watchlist.txt"
    watch.write_text("+60123456789\n", encoding="utf-8")

    fresh = asyncio.run(snap.build_snapshot(["+60123456789", "+60177001122"],
                                            source="watchlist"))
    fresh["entries"]["+60177001122"]["source"] = "dispatch"
    fresh_path.write_text(json.dumps(fresh), encoding="utf-8")

    other = asyncio.run(snap.build_snapshot(["+60123456789", "+60199990000"],
                                           source="watchlist"))
    other["entries"]["+60199990000"]["source"] = "dispatch"
    remote_path.write_text(json.dumps(other), encoding="utf-8")

    merged = snap.reconcile(fresh_path, remote_path, watchlist_path=watch)
    assert set(merged["entries"]) == {"+60123456789", "+60177001122", "+60199990000"}
    assert merged["watchlist_size"] == 1 and merged["dispatch_size"] == 2


def test_reconcile_survives_a_missing_remote_file(tmp_path, fake_engine):
    fresh_path = tmp_path / "fresh.json"
    fresh_path.write_text(json.dumps(asyncio.run(snap.build_snapshot(["+60123456789"]))),
                          encoding="utf-8")
    merged = snap.reconcile(fresh_path, tmp_path / "gone.json")
    assert list(merged["entries"]) == ["+60123456789"]


def test_cli_merge_mode_rewrites_the_file(tmp_path, fake_engine):
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps(asyncio.run(snap.build_snapshot(["+60123456789"]))),
                     encoding="utf-8")
    remote = tmp_path / "remote.json"
    other = asyncio.run(snap.build_snapshot(["+15550100"]))
    other["entries"]["+15550100"]["source"] = "dispatch"
    remote.write_text(json.dumps(other), encoding="utf-8")
    out = tmp_path / "out.json"

    assert snap.main(["--merge", "--fresh", str(fresh), "--remote", str(remote),
                      "--watchlist", str(tmp_path / "none.txt"), "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert set(data["entries"]) == {"+60123456789", "+15550100"}
    assert snap.main(["--merge", "--out", str(out)]) == 2      # --fresh is required


def test_summarize_tolerates_a_hand_edited_file():
    text = snap.summarize({"generated_at": "T", "entries": {
        "+60": {"probed_at": "T", "conclusive": 1, "results": [{"status": "found"}]},
        "+61": 7,
    }})
    assert "no snapshot" not in text
    assert "+60" in text
