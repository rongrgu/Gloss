"""Background LLM jobs (DESIGN.md §7.2).

- Jobs sharing a queue key (one slide's Q&A session) run one at a time, in order.
- At most `concurrency` jobs run at once overall.
- A job keeps every event it emitted, so any number of clients can subscribe at
  any time (including after it finished) and get the full stream. Jobs run to
  completion whether or not anyone is listening: answers are saved even if the
  browser tab closes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

KEEP_FINISHED = 15 * 60  # seconds a finished job stays subscribable


class Job:
    def __init__(self, id: str, kind: str, meta: dict[str, Any] | None = None):
        self.id = id
        self.kind = kind
        self.meta = meta or {}
        self.status = "queued"  # queued | running | done | error
        self.events: list[dict[str, Any]] = []
        self.finished_at: float | None = None
        self._changed = asyncio.Condition()

    @property
    def finished(self) -> bool:
        return self.status in ("done", "error")

    def emit(self, event: dict[str, Any]) -> None:
        if self.finished:
            raise RuntimeError(f"job {self.id} already finished")
        t = event["type"]
        if t == "start":
            self.status = "running"
        elif t in ("done", "error"):
            self.status = t
            self.finished_at = time.monotonic()
        self.events.append(event)
        asyncio.get_running_loop().create_task(self._notify())

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        i = 0
        while True:
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.finished:
                return
            async with self._changed:
                await self._changed.wait_for(lambda: len(self.events) > i)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "status": self.status, **self.meta}


class JobManager:
    def __init__(self, concurrency: int = 2):
        self._slots = asyncio.Semaphore(max(1, concurrency))
        self._queues: dict[str, asyncio.Lock] = {}
        self._jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def active(self, prefix: str = "") -> list[Job]:
        return [j for j in self._jobs.values() if not j.finished and j.id.startswith(prefix)]

    def submit(
        self,
        job_id: str,
        kind: str,
        queue: str | None,
        fn: Callable[[Job], Awaitable[None]],
        meta: dict[str, Any] | None = None,
    ) -> Job:
        """Start `fn(job)` in the background. If an unfinished job with this id exists, return it instead.

        `fn` emits deltas with `job.emit(...)` and must end by emitting `done`;
        an exception becomes an `error` event.
        """
        self._prune()
        existing = self._jobs.get(job_id)
        if existing and not existing.finished:
            return existing
        job = Job(job_id, kind, meta)
        self._jobs[job_id] = job
        job.emit({"type": "queued"})
        task = asyncio.create_task(self._run(job, queue, fn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, queue: str | None, fn: Callable[[Job], Awaitable[None]]) -> None:
        lock = self._queues.setdefault(queue, asyncio.Lock()) if queue else None
        try:
            if lock:
                await lock.acquire()
            try:
                async with self._slots:
                    job.emit({"type": "start"})
                    await fn(job)
                    if not job.finished:
                        raise RuntimeError("job function returned without emitting done")
            finally:
                if lock:
                    lock.release()
        except asyncio.CancelledError:
            if not job.finished:
                job.emit({"type": "error", "message": "cancelled"})
            raise
        except Exception as e:  # noqa: BLE001 — surfaced to the client
            if not job.finished:
                job.emit({"type": "error", "message": str(e) or type(e).__name__})

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _prune(self) -> None:
        now = time.monotonic()
        for jid in [j.id for j in self._jobs.values() if j.finished and now - j.finished_at > KEEP_FINISHED]:
            del self._jobs[jid]
