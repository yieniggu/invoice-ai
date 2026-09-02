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
    evaluation_id: int | None
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
    """Select the first usable evaluation unless a usable ID was explicitly provided."""
    candidate_list = list(candidates)
    candidate_ids = [item.evaluation_id for item in candidate_list if item.usable]
    selected = next((item for item in candidate_list if item.evaluation_id == evaluation_id), None)
    if selected is not None and selected.usable:
        return EvaluationSelectionState(
            True,
            candidate_ids,
            evaluation_id,
            f"Evaluación seleccionada por override: ID {evaluation_id}.",
            "Continúa.",
        )

    if evaluation_id is None and candidate_ids:
        selected_evaluation_id = candidate_ids[0]
        return EvaluationSelectionState(
            True,
            candidate_ids,
            selected_evaluation_id,
            f"Evaluación seleccionada automáticamente: ID {selected_evaluation_id}.",
            "Continúa.",
        )

    if not candidate_ids:
        return EvaluationSelectionState(
            False,
            candidate_ids,
            None,
            "No hay evaluaciones USABLE.",
            "Ejecuta N03--N05 para registrar una evaluación con lineage completo y vuelve a ejecutar esta celda.",
        )

    recommended_ids = ", ".join(map(str, candidate_ids)) or "ninguno"
    return EvaluationSelectionState(
        False,
        candidate_ids,
        None,
        "El EVALUATION_ID indicado no es USABLE.",
        f"Indica un ID USABLE ({recommended_ids}) y vuelve a ejecutar esta celda.",
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
