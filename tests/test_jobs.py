import asyncio

import pytest

from gloss.jobs import JobManager


def arun(coro):
    return asyncio.run(coro)


async def collect(job):
    return [e async for e in job.subscribe()]


def test_events_replay_to_late_subscribers():
    async def go():
        jm = JobManager()

        async def fn(job):
            job.emit({"type": "delta", "text": "a"})
            job.emit({"type": "done"})

        job = jm.submit("j", "k", None, fn)
        first = await collect(job)
        late = await collect(job)  # after it finished
        return first, late, job.status

    first, late, status = arun(go())
    assert [e["type"] for e in first] == ["queued", "start", "delta", "done"]
    assert first == late and status == "done"


def test_same_queue_runs_serially_in_order():
    async def go():
        jm = JobManager(concurrency=5)
        log, gate = [], asyncio.Event()

        def make(name):
            async def fn(job):
                log.append(f"{name}+")
                if name == "a":
                    await gate.wait()
                log.append(f"{name}-")
                job.emit({"type": "done"})
            return fn

        a = jm.submit("a", "k", "page1", make("a"))
        b = jm.submit("b", "k", "page1", make("b"))
        c = jm.submit("c", "k", "page2", make("c"))
        await collect(c)
        assert b.status == "queued"  # waiting behind a
        gate.set()
        await collect(a)
        await collect(b)
        return log

    log = arun(go())
    assert log.index("a-") < log.index("b+")  # b never overlaps a
    assert log.index("c-") < log.index("a-")  # other page not blocked


def test_global_concurrency_cap():
    async def go():
        jm = JobManager(concurrency=2)
        running = peak = 0

        async def fn(job):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            job.emit({"type": "done"})

        jobs = [jm.submit(f"j{i}", "k", f"page{i}", fn) for i in range(6)]
        for j in jobs:
            await collect(j)
        return peak

    assert arun(go()) == 2


def test_duplicate_submit_joins_running_job():
    async def go():
        jm = JobManager()
        gate, runs = asyncio.Event(), []

        async def fn(job):
            runs.append(1)
            await gate.wait()
            job.emit({"type": "done"})

        a = jm.submit("same", "k", None, fn)
        b = jm.submit("same", "k", None, fn)
        gate.set()
        await collect(a)
        c = jm.submit("same", "k", None, fn)  # finished -> new job
        await collect(c)
        return a is b, a is c, len(runs)

    assert arun(go()) == (True, False, 2)


@pytest.mark.parametrize("body, msg", [("raise", "kaput"), ("forget", "without emitting done")])
def test_failures_become_error_events(body, msg):
    async def go():
        jm = JobManager()

        async def fn(job):
            job.emit({"type": "delta", "text": "x"})
            if body == "raise":
                raise RuntimeError("kaput")

        job = jm.submit("j", "k", "q", fn)
        evs = await collect(job)
        # The queue is released after an error.
        ok = jm.submit("j2", "k", "q", lambda j: _done(j))
        await collect(ok)
        return evs, job.status, ok.status

    evs, status, after = arun(go())
    assert status == "error" and msg in evs[-1]["message"] and after == "done"


async def _done(job):
    job.emit({"type": "done"})
