"""Unit tests for the RocketReach async-polling loop
(``RocketReachProvider._poll_person_lookup_until_complete``).

Scope: the polling state machine introduced by the Universal migration
(requirements §5.2, LLD §3.3 T3). Exercises the loop's four documented
outcomes:

    1. Every id reports ``status: "complete"`` → return the matched profile.
    2. ``status: "failed"`` → return the no-match sentinel.
    3. Wall-time cap hit → return partial profile stamped with
       ``lookup_status: "in_progress"`` and a ``retry_hint``.
    4. 429 during polling is honoured within the wall-time budget and does
       not abort the loop.
    5. 401 during polling surfaces as a ProviderError.

HTTP is swapped via ``httpx.MockTransport`` — we keep the real
``httpx.AsyncClient`` contract, only the wire is faked. Polling timings are
shrunk to ms and ``asyncio.sleep`` is neutralised so the test suite stays
fast.
"""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from server.core.exceptions import ProviderError
from server.execution.providers import rocketreach as rr_module
from server.execution.providers.rocketreach import RocketReachProvider


# ---------------------------------------------------------------------------
# Helpers — inject a MockTransport-backed AsyncClient into the provider
# module, and neutralise asyncio.sleep so the loop runs instantly.
# ---------------------------------------------------------------------------


def _install_fake_httpx(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace ``httpx.AsyncClient`` (as imported by the provider module)
    with a wrapper that always routes through a MockTransport running
    *handler*. Real AsyncClient, real methods — just no network."""

    class _FakeAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(rr_module.httpx, "AsyncClient", _FakeAsyncClient)


class _FakeClock:
    """Deterministic monotonic clock that advances exactly when the code
    under test chooses to sleep. Running in virtual time lets us keep
    production-default polling intervals (3s tick / 30s cap) in the
    assertions while the test itself finishes in microseconds."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, by: float) -> None:
        self.t += by


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> _FakeClock:
    """Replace ``time.monotonic`` + ``asyncio.sleep`` so the polling loop
    runs in virtual time — every ``sleep(x)`` advances the clock by ``x``
    and returns immediately. Production defaults for cap/interval stay in
    place so assertions on ``retry_after_seconds`` reflect real values."""
    clock = _FakeClock()
    monkeypatch.setattr(rr_module.time, "monotonic", clock)

    async def _fake_sleep(delay: float) -> None:
        clock.advance(delay)

    monkeypatch.setattr(rr_module.asyncio, "sleep", _fake_sleep)
    return clock


def _json_response(status: int, payload) -> httpx.Response:
    return httpx.Response(status_code=status, json=payload)


# ---------------------------------------------------------------------------
# Completes on first poll
# ---------------------------------------------------------------------------


class TestPollingCompletesImmediately:
    async def test_returns_matched_profile_when_first_poll_is_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/universal/person/checkStatus")
            # Vendor returns a list of entries — one per polled id.
            return _json_response(200, [{
                "id": 42, "status": "complete",
                "name": "Jane Doe", "current_title": "CTO",
            }])

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result["status"] == "complete"
        assert result["name"] == "Jane Doe"
        # Success path carries the raw profile; no in_progress marker.
        assert "lookup_status" not in result
        assert "retry_hint" not in result

    async def test_accepts_wrapped_profiles_shape(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Vendor occasionally wraps results as ``{profiles: [...]}``
        instead of a bare list. The loop must read either shape."""
        def handler(_: httpx.Request) -> httpx.Response:
            return _json_response(200, {"profiles": [{
                "id": 42, "status": "complete", "name": "Jane Doe",
            }]})

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result["status"] == "complete"


# ---------------------------------------------------------------------------
# Status=failed — returns the no-match sentinel
# ---------------------------------------------------------------------------


class TestPollingFailedStatus:
    async def test_returns_no_match_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return _json_response(200, [{"id": 42, "status": "failed"}])

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        # Sentinel contract matches normalizer's no-match branch
        # (see normalizer.py rocketreach dispatch).
        assert result == {"match_found": False, "profiles": []}


# ---------------------------------------------------------------------------
# Cap hit — returns in_progress + retry_hint
# ---------------------------------------------------------------------------


class TestPollingCapHit:
    """When every tick returns a non-terminal status and the wall-time cap
    is exhausted, the loop must return the last-seen profile with an
    ``in_progress`` marker and a ``retry_hint`` that tells the caller what
    id to re-issue."""

    async def test_returns_in_progress_marker_and_retry_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            # Every tick: still searching. Never terminal.
            return _json_response(200, [{
                "id": 42, "status": "searching",
                "name": "Jane (partial)",
            }])

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result.get("lookup_status") == "in_progress"
        assert isinstance(result.get("retry_hint"), dict)
        assert result["retry_hint"].get("vendor_id") == 42
        # Default cap is 30s — the hint reflects the whole budget so the
        # agent waits long enough for the vendor to finish.
        assert result["retry_hint"].get("retry_after_seconds") == 30

    async def test_preserves_last_seen_partial_profile_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The normalizer will still map the partial row — don't strip
        already-resolved fields just because the lookup hasn't completed."""
        def handler(_: httpx.Request) -> httpx.Response:
            return _json_response(200, [{
                "id": 42, "status": "progress",
                "name": "Jane Doe", "current_title": "CTO",
            }])

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result["name"] == "Jane Doe"
        assert result["current_title"] == "CTO"

    async def test_empty_polling_payload_yields_minimal_in_progress(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the vendor never shipped the requested id (edge case — empty
        list), we still return an ``in_progress`` envelope so the caller
        gets a well-formed retry hint instead of None."""
        def handler(_: httpx.Request) -> httpx.Response:
            return _json_response(200, [])

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result.get("lookup_status") == "in_progress"
        assert result["retry_hint"]["vendor_id"] == 42


# ---------------------------------------------------------------------------
# 429 during polling — honoured but does not abort
# ---------------------------------------------------------------------------


class TestPollingHonoursRateLimit:
    async def test_single_429_does_not_abort_and_subsequent_complete_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One 429 tick must consume some of the wall-time budget and let
        the next tick succeed — not bubble up as an error."""
        responses = iter([
            httpx.Response(429, headers={"Retry-After": "1"}),
            _json_response(200, [{
                "id": 42, "status": "complete", "name": "Jane Doe",
            }]),
        ])

        def handler(_: httpx.Request) -> httpx.Response:
            return next(responses)

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result["status"] == "complete"
        assert result["name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# Hard auth failure during polling — raises
# ---------------------------------------------------------------------------


class TestPollingSurfacesAuthErrors:
    async def test_401_during_poll_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"detail": "invalid key"})

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider()._poll_person_lookup_until_complete(
                vendor_id=42, api_key="fake-key",
            )

        assert exc_info.value.status_code == 401
        assert exc_info.value.provider == "rocketreach"

    async def test_403_during_poll_raises_provider_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mid-flight 403 (credit state changed, etc.) is a hard error —
        don't silently degrade to an in_progress envelope."""
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"detail": "forbidden"})

        _install_fake_httpx(monkeypatch, handler)

        with pytest.raises(ProviderError) as exc_info:
            await RocketReachProvider()._poll_person_lookup_until_complete(
                vendor_id=42, api_key="fake-key",
            )

        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Unexpected transient non-200 — keeps polling
# ---------------------------------------------------------------------------


class TestPollingSkipsTransientNon200s:
    async def test_503_does_not_abort_and_subsequent_complete_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        responses = iter([
            httpx.Response(503, text="service unavailable"),
            _json_response(200, [{"id": 42, "status": "complete"}]),
        ])

        def handler(_: httpx.Request) -> httpx.Response:
            return next(responses)

        _install_fake_httpx(monkeypatch, handler)

        result = await RocketReachProvider()._poll_person_lookup_until_complete(
            vendor_id=42, api_key="fake-key",
        )

        assert result["status"] == "complete"
