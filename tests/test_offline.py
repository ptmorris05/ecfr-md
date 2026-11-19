"""Reading cached text when the eCFR can't be reached."""

import copy

import httpx2
import pytest
from conftest import TITLES

from ecfr_md import ECFR, ECFRConnectionError, ECFRWarning, Settings
from ecfr_md.cache import Store
from ecfr_md.client import TITLES_FILE

VERSIONS = "/api/versioner/v1/versions/title-45.json"


def _settings(tmp_path) -> Settings:
    return Settings(
        cache_dir=tmp_path, backoff_initial=0, backoff_max=0, max_attempts=1
    )


def _offline(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("no network", request=request)


def _client(api, tmp_path) -> ECFR:
    return ECFR(_settings(tmp_path), transport=httpx2.MockTransport(api))


def test_offline_reading_uses_the_cache_with_a_warning(api, tmp_path):
    with _client(api, tmp_path) as ecfr:
        ecfr.get("45 CFR Part 164")
    offline = ECFR(_settings(tmp_path), transport=httpx2.MockTransport(_offline))
    warning = pytest.warns(ECFRWarning, match="list of titles.*may be out of date")
    with offline as ecfr, warning:
        part = ecfr.get("45 CFR Part 164")
    assert part.citation == "45 CFR Part 164"


def test_offline_freshness_check_serves_the_cached_copy(api, tmp_path):
    with _client(api, tmp_path) as ecfr:
        ecfr.get("45 CFR Part 164")
    newer = copy.deepcopy(TITLES)
    newer["titles"][0]["latest_issue_date"] = "2026-10-08"
    api.json("/api/versioner/v1/titles.json", newer)
    api.routes[VERSIONS] = _offline
    with _client(api, tmp_path) as ecfr, pytest.warns(ECFRWarning, match="cached on"):
        part = ecfr.get("45 CFR Part 164")
    assert part.citation == "45 CFR Part 164"


def test_offline_download_falls_back_to_a_cached_copy(api, tmp_path):
    with _client(api, tmp_path) as ecfr:
        ecfr.get("45 CFR Part 164")
    unknown = copy.deepcopy(TITLES)
    del unknown["titles"][0]["latest_issue_date"]  # Nothing in the cache is current.
    api.json("/api/versioner/v1/titles.json", unknown)
    full = "/api/versioner/v1/full/2026-10-07/title-45.xml"
    api.routes[full] = _offline
    api.routes[full.replace("2026-10-07", "2026-01-01")] = _offline
    with _client(api, tmp_path) as ecfr:
        with pytest.warns(ECFRWarning, match="text cached on"):
            assert ecfr.get("45 CFR Part 164").citation == "45 CFR Part 164"
        with pytest.raises(ECFRConnectionError):
            ecfr.get("45 CFR 164.104", date="2026-01-01")  # Never cached.


def test_offline_without_a_cache_fails(tmp_path):
    offline = ECFR(_settings(tmp_path), transport=httpx2.MockTransport(_offline))
    with offline as ecfr, pytest.raises(ECFRConnectionError):
        ecfr.titles()
    assert Store(None).kept(TITLES_FILE) is None
