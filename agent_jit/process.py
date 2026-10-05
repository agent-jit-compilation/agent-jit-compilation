"""Bound the lifetime of an adapter and its browser child processes."""

import asyncio
from collections.abc import Awaitable
import contextlib
import os
import signal
import subprocess
from typing import TypeVar


_T = TypeVar("_T")


async def await_with_deadline(operation: Awaitable[_T], seconds: float) -> _T:
    """Await an operation while preserving deadline expiry through cancellation.

    Some original adapters catch cancellation and return or raise another error.
    Check the timeout scope as well as the exception so those outcomes cannot
    turn an expired execution into success or an ordinary agent error. The outer
    process deadline still handles operations that refuse to terminate.

    Args:
        operation: Operation to execute in the current task.
        seconds: Maximum duration before requesting cancellation.

    Returns:
        The operation's result if it completes before the deadline.

    Raises:
        TimeoutError: The deadline expired, even if cancellation was suppressed.
        BaseException: An operation failure unrelated to deadline expiry.
    """
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            result = await operation
    except BaseException as exc:
        if deadline.expired():
            raise TimeoutError("Operation exceeded its execution deadline") from exc
        raise
    if deadline.expired():
        raise TimeoutError("Operation exceeded its execution deadline")
    return result


def run_isolated(command, *, cwd, env, log, timeout):
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=log, stderr=log, start_new_session=True
    )
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
