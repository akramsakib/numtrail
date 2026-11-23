"""Renderers and CLI wiring."""

import csv
import io
import json

import pytest

from numtrail import Result, Status
from numtrail.cli import _resolve_numbers, build_parser, main
from numtrail.models import Status as S
from numtrail.output import found_domains, render_table, to_csv, to_json
from numtrail.phone import parse

PHONE = parse("+60123456789")

RESULTS = [
    Result(module="instagram", domain="instagram.com", status=S.FOUND, tier="legacy",
           detail="account exists", elapsed=0.4),
    Result(module="snapchat", domain="snapchat.com", status=S.BLOCKED, tier="legacy",
           detail="endpoint needs a session", elapsed=0.2),
    Result(module="twilio", domain="twilio.com", status=S.NOT_FOUND, method="api",
           semantic="number_valid", tier="api", elapsed=0.1),
    Result(module="numverify", domain="numverify.com", status=S.NEEDS_CREDENTIALS,
           method="api", semantic="number_valid", tier="api", detail="set NUMVERIFY_KEY"),
]


def phone_header_present(text):
    return "+60 123456789" in text and "(MY)" in text


def test_json_payload_shape():
    payload = json.loads(to_json(RESULTS, PHONE, 1.234))
    assert payload["phone"] == "+60123456789"
    assert payload["iso"] == "MY"
    assert [r["module"] for r in payload["results"]] == ["instagram", "snapchat",
                                                          "twilio", "numverify"]
    assert payload["results"][0]["found"] is True
    assert payload["results"][1]["status"] == "blocked"


def test_csv_has_a_row_per_result():
    rows = list(csv.DictReader(io.StringIO(to_csv(RESULTS))))
    assert len(rows) == 4
    assert rows[0]["module"] == "instagram"
    assert rows[2]["semantic"] == "number_valid"


def test_table_mentions_counts_and_legend():
    text = render_table(RESULTS, PHONE, 0.75, no_color=True)
    assert phone_header_present(text)
    assert "1/4 yes" in text
    assert "finished in 0.75s" in text
    assert "[+] found" in text


def test_only_found_hides_the_rest():
    text = render_table(RESULTS, PHONE, 0.1, no_color=True, only_found=True)
    assert "instagram" in text and "snapchat" not in text


def test_found_domains():
    assert found_domains(RESULTS) == ["instagram.com"]


def test_resolve_numbers_supports_both_forms():
    assert _resolve_numbers(["+60123456789"]) == ["+60123456789"]
    assert _resolve_numbers(["60", "123456789"]) == ["60123456789"]
    assert _resolve_numbers(["60123456789", "15550100"]) == ["60123456789", "15550100"]


def test_parser_defaults():
    args = build_parser().parse_args(["+60123456789"])
    assert args.concurrency == 8 and args.timeout == 15.0 and args.retries == 1


def test_cli_list_modules_exits_cleanly(capsys):
    assert main(["--list-modules"]) == 0
    out = capsys.readouterr().out
    assert "instagram" in out and "twilio" in out


def test_cli_rejects_bad_number(capsys):
    assert main(["abc"]) == 2
    assert "error:" in capsys.readouterr().err


def test_cli_rejects_unknown_module(capsys):
    assert main(["+60123456789", "-m", "myspace"]) == 2
    assert "unknown module" in capsys.readouterr().err


def test_cli_end_to_end_with_stubbed_engine(monkeypatch, capsys):
    async def fake_run_checks(phone, **kwargs):
        return RESULTS

    monkeypatch.setattr("numtrail.cli.run_checks", fake_run_checks)
    assert main(["+60123456789", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["phone"] == "+60123456789"


def test_cli_quiet_prints_domains_only(monkeypatch, capsys):
    async def fake_run_checks(phone, **kwargs):
        return RESULTS

    monkeypatch.setattr("numtrail.cli.run_checks", fake_run_checks)
    assert main(["+60123456789", "-q"]) == 0
    assert capsys.readouterr().out.strip() == "instagram.com"


def test_cli_writes_csv(monkeypatch, tmp_path, capsys):
    async def fake_run_checks(phone, **kwargs):
        return RESULTS

    monkeypatch.setattr("numtrail.cli.run_checks", fake_run_checks)
    target = tmp_path / "out.csv"
    main(["+60123456789", "--csv", str(target), "-q"])
    capsys.readouterr()
    assert target.read_text().startswith("module,domain,status,found")
