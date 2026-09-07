"""검색 서비스 백그라운드 준비 상태 테스트."""

from __future__ import annotations

from threading import Event, Lock

import pytest

from src.retrieval.readiness import BackgroundServiceLoader


def test_loader_starts_without_blocking_and_reuses_one_factory_call() -> None:
    release = Event()
    calls = 0
    calls_lock = Lock()
    service = object()

    def factory():
        nonlocal calls
        with calls_lock:
            calls += 1
        release.wait(timeout=2)
        return service

    loader = BackgroundServiceLoader(factory).start()

    assert loader.snapshot().state == "loading"
    loader.start()
    release.set()
    assert loader.result(timeout=2) is service
    assert loader.result(timeout=2) is service
    assert loader.snapshot().state == "ready"
    assert calls == 1


def test_loader_retries_only_after_failure() -> None:
    calls = 0
    service = object()

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return service

    loader = BackgroundServiceLoader(factory).start()
    with pytest.raises(RuntimeError, match="temporary failure"):
        loader.result(timeout=2)

    assert loader.snapshot().state == "failed"
    assert loader.retry() is True
    assert loader.result(timeout=2) is service
    assert loader.retry() is False
    assert calls == 2
