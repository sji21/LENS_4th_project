"""검색 서비스를 중복 생성 없이 백그라운드에서 준비한다."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Callable, Generic, Literal, TypeVar


ServiceT = TypeVar("ServiceT")
ReadinessState = Literal["idle", "loading", "ready", "failed"]


@dataclass(frozen=True)
class ReadinessSnapshot:
    """UI가 검색 서비스 준비 상태를 안전하게 읽기 위한 불변 값."""

    state: ReadinessState
    elapsed_seconds: float = 0.0


class BackgroundServiceLoader(Generic[ServiceT]):
    """하나의 서비스 팩토리를 전용 스레드에서 최대 한 번 실행한다.

    실패한 작업만 명시적으로 재시도할 수 있다. 여러 Streamlit rerun이나
    여러 질문이 동시에 ``result()``를 호출해도 같은 Future를 공유한다.
    """

    def __init__(self, factory: Callable[[], ServiceT]) -> None:
        self._factory = factory
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="retrieval-readiness",
        )
        self._lock = Lock()
        self._future: Future[ServiceT] | None = None
        self._started_at: float | None = None

    def _submit_locked(self) -> Future[ServiceT]:
        self._started_at = perf_counter()
        self._future = self._executor.submit(self._factory)
        return self._future

    def start(self) -> "BackgroundServiceLoader[ServiceT]":
        """준비를 시작하고, 이미 시작했다면 기존 작업을 그대로 재사용한다."""

        with self._lock:
            if self._future is None:
                self._submit_locked()
        return self

    def result(self, timeout: float | None = None) -> ServiceT:
        """준비된 서비스를 반환하고, 준비 중이면 같은 작업이 끝날 때까지 기다린다."""

        self.start()
        with self._lock:
            future = self._future
        assert future is not None
        return future.result(timeout=timeout)

    def snapshot(self) -> ReadinessSnapshot:
        """현재 준비 상태와 경과 시간을 반환한다."""

        with self._lock:
            future = self._future
            started_at = self._started_at

        if future is None:
            return ReadinessSnapshot("idle")

        elapsed = max(0.0, perf_counter() - (started_at or perf_counter()))
        if not future.done():
            return ReadinessSnapshot("loading", elapsed)
        if future.exception() is not None:
            return ReadinessSnapshot("failed", elapsed)
        return ReadinessSnapshot("ready", elapsed)

    def retry(self) -> bool:
        """실패한 준비 작업만 새 Future로 교체한다."""

        with self._lock:
            if self._future is None:
                self._submit_locked()
                return True
            if not self._future.done() or self._future.exception() is None:
                return False
            self._submit_locked()
            return True
