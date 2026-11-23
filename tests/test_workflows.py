"""The workflow files themselves.

A duplicate YAML key is silently accepted by most parsers and then rejected by
GitHub at dispatch time ("failed to parse workflow: 'env' is already defined"),
which turns into a 422 nobody sees until Run workflow fails. These tests parse
with a strict loader so that mistake fails here instead.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


class StrictLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate mapping keys."""


def _strict_mapping(loader, node, deep=False):
    seen = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen[key] = loader.construct_object(value_node, deep=deep)
    return seen


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)


def load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)


def test_there_are_workflows_to_check():
    assert WORKFLOWS, "no workflow files found — wrong path?"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_has_no_duplicate_keys(path):
    load(path)   # raises on a duplicate key


def _snapshot_workflow() -> dict:
    return load(ROOT / ".github" / "workflows" / "snapshot.yml")


def test_snapshot_trigger_includes_a_dispatch_form():
    wf = _snapshot_workflow()
    triggers = wf[True] if True in wf else wf["on"]     # `on:` parses as True
    dispatch = triggers["workflow_dispatch"]
    assert "number" in dispatch["inputs"], "Run workflow needs a number box"
    assert dispatch["inputs"]["number"]["required"] is False
    assert "skip_watchlist" in dispatch["inputs"]


def test_the_form_value_reaches_the_cli():
    steps = _snapshot_workflow()["jobs"]["snapshot"]["steps"]
    probe = next(s for s in steps if s.get("name") == "Probe")
    assert probe["env"]["INPUT_NUMBER"] == "${{ inputs.number }}"
    assert "INPUT_SKIP_WATCHLIST" in probe["env"]
    assert "--number" in probe["run"]
    assert "--skip-watchlist" in probe["run"]


def test_snapshot_job_can_commit_and_redeploy():
    wf = _snapshot_workflow()
    assert wf["permissions"]["contents"] == "write"
    assert wf["permissions"]["actions"] == "write"
    # GITHUB_TOKEN commits don't chain, so Pages must be dispatched by hand.
    assert any("pages.yml" in s.get("run", "")
               for s in wf["jobs"]["snapshot"]["steps"])


def test_summary_step_survives_a_failed_probe():
    steps = _snapshot_workflow()["jobs"]["snapshot"]["steps"]
    summary = next(s for s in steps if s.get("name") == "Job summary")
    assert summary["if"] == "always()"
