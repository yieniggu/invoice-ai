"""Small idempotency utilities shared by the teaching notebooks."""

from collections.abc import Callable


def run_mutable_action_once[T](name: str, completed: dict[str, T], action: Callable[[], T]) -> T:
    """Reuse a recorded result so a re-run does not repeat a state mutation."""
    if name not in completed:
        completed[name] = action()
    return completed[name]
