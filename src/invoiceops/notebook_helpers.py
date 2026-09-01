"""Selection states used by the Class 3 teaching notebook."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


class EvaluationCandidate(Protocol):
    evaluation_id: int
    usable: bool


@dataclass(frozen=True)
class EvaluationSelectionState:
    ready: bool
    candidate_ids: list[int]
    state: str
    next_action: str


@dataclass(frozen=True)
class BatchSelectionState:
    ready: bool
    state: str
    next_action: str


def evaluation_selection_state(
    candidates: Iterable[EvaluationCandidate], evaluation_id: int | None
) -> EvaluationSelectionState:
    """Require a visible usable ID without choosing one on the student's behalf."""
    candidate_list = list(candidates)
    candidate_ids = [item.evaluation_id for item in candidate_list if item.usable]
    selected = next((item for item in candidate_list if item.evaluation_id == evaluation_id), None)
    if selected is not None and selected.usable:
        return EvaluationSelectionState(True, candidate_ids, "Selección válida.", "Continúa.")

    recommended_ids = ", ".join(map(str, candidate_ids)) or "ninguno"
    return EvaluationSelectionState(
        False,
        candidate_ids,
        "Falta una evaluación explícita y utilizable.",
        f"Escribe uno de estos IDs USABLE en EVALUATION_ID: {recommended_ids}; luego vuelve a ejecutar esta celda.",
    )


def batch_selection_state(batch_id: object) -> BatchSelectionState:
    """Require a positive explicit batch ID before querying persisted state."""
    if isinstance(batch_id, int) and not isinstance(batch_id, bool) and batch_id > 0:
        return BatchSelectionState(True, "Selección válida.", "Continúa.")
    return BatchSelectionState(
        False,
        "Falta un batch explícito y válido.",
        "En Portal elige el batch inicial o su sucesor y escribe un entero positivo en BATCH_ID.",
    )
