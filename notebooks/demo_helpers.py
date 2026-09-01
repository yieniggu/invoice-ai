"""Small lifecycle utilities shared by the teaching notebooks.

This module deliberately contains no ML, registry, policy, or persistence logic.
"""

from collections.abc import Callable
from subprocess import TimeoutExpired
from time import monotonic
from time import sleep as default_sleep
from typing import Protocol


class ManagedProcess(Protocol):
    def terminate(self) -> None: ...


def run_mutable_action_once[T](name: str, completed: dict[str, T], action: Callable[[], T]) -> T:
    """Reuse a recorded result so a re-run does not repeat a state mutation."""
    if name not in completed:
        completed[name] = action()
    return completed[name]


def wait_for_health(
    url: str,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    request: Callable[[str], bool],
    sleep: Callable[[float], None] = default_sleep,
) -> None:
    """Poll a health endpoint until it is ready or the bounded timeout expires."""
    deadline = monotonic() + timeout_seconds
    while True:
        if request(url):
            return
        if monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for health endpoint: {url}")
        sleep(poll_interval_seconds)


def cleanup_created_process(process: ManagedProcess | None, *, created_by_helper: bool) -> None:
    """Terminate only a process the notebook started; never affect reused processes."""
    if process is None or not created_by_helper:
        return
    process.terminate()
    wait = getattr(process, "wait", None)
    if wait is None:
        return
    try:
        wait(timeout=5)
    except TimeoutExpired:
        kill = getattr(process, "kill", None)
        if kill is not None:
            kill()
            wait(timeout=5)
