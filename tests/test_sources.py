"""Choosing between the eCFR API and GovInfo's bulk files."""

import json

import pytest

from ecfr_md import Settings
from ecfr_md.sources import (
    COSTS_FILE,
    Costs,
    ECFRApi,
    GovInfoBulk,
    choose,
    explain,
)


def test_sources_know_their_hosts():
    assert ECFRApi("https://www.ecfr.gov").host == "www.ecfr.gov"
    bulk = GovInfoBulk("https://mirror.example/ecfr/")
    assert bulk.host == "mirror.example"
    assert bulk.title_url(7) == "https://mirror.example/ecfr/title-7/ECFR-title7.xml"


def pick(settings=None, *, past=False, cached=False, single=False, estimate=(1, 0, 0),
         current=True):  # fmt: skip
    settings = settings or Settings()
    calls = []

    def estimated():
        calls.append("estimate")
        return estimate

    def is_current():
        calls.append("current")
        return current

    choice = choose(
        settings,
        Costs(settings, None),
        past=past,
        cached=cached,
        single=single,
        estimate=estimated,
        current=is_current,
    )
    return choice.source, choice.reason, calls


def test_the_api_when_asked_for_or_for_past_dates():
    assert pick(Settings(source="api"), cached=True)[:2] == (
        "api",
        "source is set to api",
    )
    assert pick(past=True, cached=True) == (
        "api",
        "bulk has only the current text",
        [],
    )


def test_a_current_bulk_copy_in_the_cache_is_used():
    assert pick(cached=True) == (
        "bulk",
        "a current bulk copy of the title is in the cache",
        [],
    )


def test_one_request_goes_to_the_api_without_estimating():
    assert pick(single=True) == ("api", "one API request", [])


def test_text_in_the_cache_goes_to_the_api():
    assert pick(estimate=(0, 10, 10)) == (
        "api",
        "the text is in the cache",
        ["estimate"],
    )


def test_the_faster_source_wins():
    # 379 requests at 4 s, 2 at a time, against 48 MB at 2.5 MB/s.
    source, reason, calls = pick(estimate=(379, 160_000_000, 160_000_000))
    assert source == "bulk" and calls == ["estimate", "current"]
    assert reason == "downloading the title (~19 s) beats 379 API requests (~758 s)"
    assert pick(estimate=(1, 1000, 100_000_000))[:2] == (
        "api",
        "1 API request (~4 s) beats downloading the title (~12 s)",
    )


def test_ties_go_to_the_api():
    settings = Settings(api_latency=1, bulk_bandwidth=0.3, concurrency=1)
    assert pick(settings, estimate=(2, 1, 2))[:2] == (
        "api",
        "2 API requests (~2 s) beat downloading the title (~2 s)",
    )
    assert pick(settings, estimate=(5, 1, 3))[:2] == (
        "bulk",
        "downloading the title (~3 s) beats 5 API requests (~5 s)",
    )


def test_a_stale_bulk_copy_goes_to_the_api():
    assert pick(estimate=(500, 1, 1), current=False)[:2] == (
        "api",
        "the bulk copy is older than the eCFR's latest changes",
    )
    assert pick(Settings(source="bulk"), single=True, current=False)[0] == "api"


def test_bulk_when_asked_for():
    assert pick(Settings(source="bulk"), single=True) == (
        "bulk",
        "source is set to bulk",
        ["current"],
    )


def test_a_size_threshold_replaces_the_estimate():
    settings = Settings(bulk_min_bytes=1000)
    assert pick(settings, single=True, estimate=(1, 999, 10**9))[:2] == (
        "api",
        "999 bytes is under bulk_min_bytes",
    )
    assert pick(settings, single=True, estimate=(1, 1000, 10**9))[:2] == (
        "bulk",
        "the text is over bulk_min_bytes",
    )


def test_costs_are_learned_and_kept(tmp_path):
    settings = Settings(api_latency=2, bulk_bandwidth=1000)
    costs = Costs(settings, tmp_path)
    costs.save()
    assert not (tmp_path / COSTS_FILE).exists()  # Nothing learned yet.
    costs.api_request(12)
    costs.bulk_download(6000, 2)
    costs.bulk_download(0, 0)  # Nothing to learn from.
    assert costs.api_latency == pytest.approx(4)
    assert costs.bulk_bandwidth == pytest.approx(1400)
    costs.save()
    saved = json.loads((tmp_path / COSTS_FILE).read_text())
    assert saved == {"api": {"www.ecfr.gov": 4.0}, "bulk": {"www.govinfo.gov": 1400}}
    again = Costs(settings, tmp_path)
    assert (again.api_latency, again.bulk_bandwidth) == (4.0, 1400)
    other = Costs(Settings(base_url="https://mirror.example"), tmp_path)
    assert other.api_latency == 4.0 and other.bulk_bandwidth == 1400  # Defaults.


def test_costs_need_not_be_learned(tmp_path):
    costs = Costs(Settings(learn_costs=False), tmp_path)
    costs.api_request(100)
    costs.bulk_download(1, 1)
    costs.save()
    assert costs.api_latency == 4.0 and not (tmp_path / COSTS_FILE).exists()


def test_unreadable_or_unwritable_costs_are_ignored(tmp_path):
    (tmp_path / COSTS_FILE).write_text("[1, 2]")
    costs = Costs(Settings(), tmp_path)
    assert costs.api_latency == 4.0
    (tmp_path / COSTS_FILE).write_text("{not json")
    assert Costs(Settings(), tmp_path).api_latency == 4.0
    blocked = tmp_path / "file"
    blocked.write_text("")
    costs = Costs(Settings(), blocked)  # A file where the directory would be.
    costs.api_request(1)
    costs.save()


def test_explaining_the_choice():
    costs = Costs(Settings(), None)
    assert explain(Settings(source="api"), costs) == "All text comes from the eCFR API."
    assert explain(Settings(source="bulk"), costs).startswith("Current text comes")
    auto = explain(Settings(), costs)
    assert "4.0 s per API request or 2.5 MB/s from bulk" in auto
    assert "1,000 bytes or more" in explain(Settings(bulk_min_bytes=1000), costs)
