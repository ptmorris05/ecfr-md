import threading
import time

import httpx2
import pytest

from ecfr_md import ECFR, Settings
from ecfr_md.network import (
    MIN_RATE,
    PROBE_AFTER,
    RECOVER_TO,
    RECOVERY_STEP,
    Limiter,
    Throttle,
)
from ecfr_md.progress import Progress
from ecfr_md.ui import describe

FULL = "/api/versioner/v1/full/2026-10-07/title-45.xml"


def test_throttle_halves_on_429_and_recovers():
    throttle = Throttle(4.0)
    assert throttle.rejected(retry_after=0) == 2.0
    assert throttle.rejected(retry_after=0) == 1.0
    assert throttle.succeeded() == pytest.approx(1.0 + RECOVERY_STEP)
    for _ in range(100):
        throttle.succeeded()
    # It settles below the rate that got a 429, not back at the setting.
    assert throttle.rate == pytest.approx(2.0 * RECOVER_TO)
    for _ in range(50):
        throttle.rejected(retry_after=0)
    assert throttle.rate == MIN_RATE


def test_unlimited_throttle_starts_limiting_after_a_429():
    throttle = Throttle(None)
    assert throttle.succeeded() is None
    assert throttle.rejected(retry_after=0) > 0


def test_429_pauses_every_thread():
    throttle = Throttle(None)
    throttle.rejected(retry_after=0.3)
    start = time.monotonic()
    threads = [threading.Thread(target=throttle.wait) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert time.monotonic() - start >= 0.3


class Recorder(Progress):
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if name in vars(Progress) and not name.startswith("_"):

            def record(*args, **kwargs):
                with object.__getattribute__(self, "lock"):
                    object.__getattribute__(self, "events").append(name)
                return attr(*args, **kwargs)

            return record
        return attr


def test_progress_events(api, settings, tmp_path, monkeypatch):
    monkeypatch.setattr("ecfr_md.client.SPLIT_ABOVE", 0)
    recorder = Recorder()
    api.sequence(
        FULL,
        httpx2.Response(429, text=""),
        httpx2.Response(200, text="<DIV5 N='164' TYPE='PART'/>"),
    )
    with ECFR(settings, transport=httpx2.MockTransport(api), progress=recorder) as e:
        e.save(45, tmp_path / "t", tree=True)
    for event in (
        "fetching",
        "started",
        "finished",
        "retrying",
        "rate_changed",
        "parts_started",
        "part_finished",
        "file_written",
    ):
        assert event in recorder.events, event


@pytest.mark.parametrize(
    ("url", "name"),
    [
        (
            "https://x/api/versioner/v1/full/2026-10-07/title-45.xml?part=164",
            "part 164",
        ),
        (
            "https://x/api/versioner/v1/full/2026-10-07/title-45.xml?part=164&section=164.502",
            "section 164.502",
        ),
        ("https://x/api/versioner/v1/full/2026-10-07/title-40.xml", "title 40"),
        (
            "https://x/api/versioner/v1/structure/2026-10-07/title-40.json",
            "outline of title 40",
        ),
        ("https://x/api/versioner/v1/titles.json", "list of titles"),
        ("https://x/api/search/v1/results?query=x", "search results"),
    ],
)
def test_request_names(url, name):
    assert describe(url) == name


def test_parts_are_fetched_concurrently_and_written_in_order(
    api, tmp_path, monkeypatch
):
    monkeypatch.setattr("ecfr_md.client.SPLIT_ABOVE", 0)
    structure = {
        "identifier": "45", "label": "Title 45", "type": "title", "size": 9,
        "children": [
            {"identifier": str(n), "label": f"Part {n}", "type": "part"}
            for n in range(1, 13)
        ],
    }  # fmt: skip
    api.json("/api/versioner/v1/structure/2026-10-07/title-45.json", structure)
    active, peak = 0, 0
    lock = threading.Lock()

    def full(request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        part = request.url.params["part"]
        time.sleep(0.05 * (13 - int(part)) / 12)  # Early parts finish last.
        with lock:
            active -= 1
        return httpx2.Response(
            200, text=f"<DIV5 N='{part}' TYPE='PART'><HEAD>Part {part}</HEAD></DIV5>"
        )

    api.routes[FULL] = full
    settings = Settings(concurrency=4, backoff_initial=0)
    with ECFR(settings, transport=httpx2.MockTransport(api)) as ecfr:
        title = ecfr.get(45)
    assert [p.identifier for p in title] == [str(n) for n in range(1, 13)]
    assert 1 < peak <= 4


def test_limiter_bounds_requests_in_flight_across_threads():
    limiter = Limiter(2)
    running, most = 0, 0
    lock = threading.Lock()

    def request():
        nonlocal running, most
        with limiter:
            with lock:
                running += 1
                most = max(most, running)
            time.sleep(0.01)
            with lock:
                running -= 1

    threads = [threading.Thread(target=request) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most == 2


def test_limiter_backs_off_and_never_returns_to_the_level_that_failed():
    limiter = Limiter(4)
    for _ in range(3):
        limiter.__enter__()
    assert limiter.rejected() == 1  # Three were running when the 429 came.
    for _ in range(3):
        limiter.__exit__(None, None, None)
    for _ in range(10 * PROBE_AFTER):
        limiter.succeeded()
    assert limiter.limit == 2  # Probed up, but not to 3.


def test_429s_do_not_use_up_attempts(ecfr, api):
    full = "/api/versioner/v1/full/2026-10-07/title-45.xml"
    slow_down = httpx2.Response(429, headers={"Retry-After": "0"})
    ok = httpx2.Response(200, text="<DIV5 N='164' TYPE='PART'><HEAD>P</HEAD></DIV5>")
    # max_attempts is 3 in these tests; four 429s are still waited out.
    api.sequence(full, slow_down, slow_down, slow_down, slow_down, ok)
    assert ecfr.get("45 CFR 164").identifier == "164"
    assert ecfr._limiter.limit == 1
