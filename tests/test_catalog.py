"""The catalogue is the web UI's data source, so it gets tested like code."""

import json

import pytest

from numtrail.catalog import CATEGORIES, SITES, STATIC_DIR, as_dicts, coverage, write_json
from numtrail.registry import discover


def test_catalogue_clears_the_hundred_site_bar():
    assert len(SITES) >= 100


def test_ids_are_unique_and_url_safe():
    ids = [s.id for s in SITES]
    assert len(ids) == len(set(ids))
    assert all(i.replace("-", "").replace("_", "").isalnum() and i.islower() for i in ids)


def test_every_category_is_known():
    assert {s.category for s in SITES} <= set(CATEGORIES)


def test_every_domain_looks_like_a_hostname():
    for site in SITES:
        assert "." in site.domain and " " not in site.domain and "/" not in site.domain


def test_no_lookup_path_starts_with_a_slash():
    for site in SITES:
        assert not site.lookup.startswith("/"), site.id


@pytest.mark.parametrize("site", SITES, ids=lambda s: s.id)
def test_every_automated_site_points_at_a_real_module(site):
    if not site.probe:
        return
    assert site.probe in discover(), f"{site.id} claims probe {site.probe} which is not registered"


def test_automated_sites_are_a_small_honest_minority():
    stats = coverage()
    assert stats["automated"] == len([s for s in SITES if s.probe])
    assert stats["automated"] + stats["manual"] == stats["total"]


def test_verified_flag_only_on_automated_sites():
    for site in SITES:
        if site.verified:
            assert site.probe, f"{site.id} is marked verified but has no probe"


def test_catalog_json_matches_source():
    """The committed catalog.json must equal what the module produces."""
    on_disk = json.loads((STATIC_DIR / "catalog.json").read_text())
    assert on_disk["sites"] == as_dicts()
    assert on_disk["coverage"]["total"] == len(SITES)


def test_write_json_round_trips(tmp_path):
    target = write_json(tmp_path / "catalog.json")
    payload = json.loads(target.read_text())
    assert len(payload["sites"]) == len(SITES)
    assert payload["categories"] == list(CATEGORIES)


def test_static_dir_has_the_ui():
    for name in ("index.html", "app.js", "styles.css", "config.js", "catalog.json"):
        assert (STATIC_DIR / name).is_file(), name
