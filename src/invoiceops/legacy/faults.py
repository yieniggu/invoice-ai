from dataclasses import dataclass
from time import sleep

PORTAL_LATENCY_OPTIONS_MS = frozenset({0, 1000, 3000, 5000})


@dataclass
class FaultState:
    change_process_button_label: bool = False
    portal_latency_ms: int = 0
    decision_api_unavailable: bool = False


state = FaultState()


def reset_faults() -> None:
    state.change_process_button_label = False
    state.portal_latency_ms = 0
    state.decision_api_unavailable = False


def apply_portal_latency() -> None:
    if state.portal_latency_ms:
        sleep(state.portal_latency_ms / 1000)
