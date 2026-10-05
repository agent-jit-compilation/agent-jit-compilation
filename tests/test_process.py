import asyncio
import os
import subprocess
import sys
import pytest
from agent_jit.process import await_with_deadline, run_isolated


@pytest.mark.parametrize("cancellation_response", ["raise", "return", "propagate"])
def test_deadline_survives_adapter_cancellation_handling(cancellation_response):
    async def adapter():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            if cancellation_response == "raise":
                raise RuntimeError("Task A failed to complete")
            if cancellation_response == "return":
                return "apparent success"
            raise

    with pytest.raises(TimeoutError, match="execution deadline"):
        asyncio.run(await_with_deadline(adapter(), 0.01))


def test_deadline_preserves_ordinary_outcomes_and_external_cancellation():
    async def exercise():
        async def success():
            return 42

        async def failure():
            raise ValueError("invalid plan")

        assert await await_with_deadline(success(), 1) == 42
        with pytest.raises(ValueError, match="invalid plan"):
            await await_with_deadline(failure(), 1)

        started = asyncio.Event()

        async def pending():
            started.set()
            await asyncio.sleep(60)

        task = asyncio.create_task(await_with_deadline(pending(), 60))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_hard_deadline_kills_worker(tmp_path):
    marker = tmp_path / "pid"
    program = 'import os,pathlib,time;pathlib.Path("pid").write_text(str(os.getpid()));time.sleep(60)'
    with (tmp_path / "log").open("w") as log:
        with pytest.raises(subprocess.TimeoutExpired):
            run_isolated(
                [sys.executable, "-c", program],
                cwd=tmp_path,
                env=os.environ.copy(),
                log=log,
                timeout=0.3,
            )
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
