import json
from code import InteractiveInterpreter
from html import escape
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoiceops import demo_paths
from invoiceops.demo_reset import reset_local_demo
from invoiceops.domain.policy import recommend_from_probability
from invoiceops.evidence import (
    EvidenceProvenance,
    EvidenceRecord,
    create_evidence_batch,
    persist_evidence_records,
)
from invoiceops.legacy.db import insert_model_evaluation
from invoiceops.ml import bootstrap
from invoiceops.ml.train import ensure_canonical_dataset

NOTEBOOK_PATH = Path(__file__).parents[2] / "notebooks/06_class_03_continuity_and_demo_state.ipynb"
NOTEBOOK_01_PATH = Path(__file__).parents[2] / "notebooks/01_data_and_baseline.ipynb"
CI_WORKFLOW_PATH = Path(__file__).parents[2] / ".github/workflows/ci.yml"
PROJECT_ROOT = NOTEBOOK_PATH.parents[1]


def _notebook_cell(cell_id: str) -> str:
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if cell["id"] == cell_id:
            return "".join(cell["source"])
    raise AssertionError(f"Notebook cell not found: {cell_id}")


def _record(evaluation_id: int) -> EvidenceRecord:
    return EvidenceRecord(
        evaluation_id=evaluation_id,
        invoice_id="INV-10023",
        correlation_id="corr-notebook-state",
        model_name="invoice-review",
        model_version="7",
        run_id="run-notebook-state",
        manual_review_probability="0.8",
        policy_version="ml-policy-v1",
        policy_threshold="0.8",
        recommendation="MANUAL_REVIEW",
        source="model",
        reason="probability_at_or_above_threshold",
        evaluation_created_at="2026-01-01T00:00:00Z",
        provenance=EvidenceProvenance(
            dataset_version="invoice-risk-v1",
            feature_schema_version="invoice-features-v1",
            git_commit="a" * 40,
        ),
    )


def _run_evaluation_selection(db_path: Path, evaluation_id: int | None) -> dict[str, object]:
    states: list[tuple[object, ...]] = []
    namespace = {
            "db_path": db_path,
            "escape": escape,
            "show_table": lambda *args: None,
            "precondition": lambda *args: states.append(args),
    }
    source = _notebook_cell("select-evaluation").replace(
        "EVALUATION_ID = None", f"EVALUATION_ID = {evaluation_id!r}"
    )
    InteractiveInterpreter(namespace).runcode(compile(source, str(NOTEBOOK_PATH), "exec"))
    return {"selection": namespace["selection"], "states": states}


def _run_batch_selection(db_path: Path, evaluation_id: int, batch_id: int | None) -> dict[str, object]:
    states: list[tuple[object, ...]] = []
    namespace = {
            "db_path": db_path,
            "EVALUATION_ID": evaluation_id,
            "selection": SimpleNamespace(ready=True, next_action="Continúa."),
            "escape": escape,
            "full_value": str,
            "show_table": lambda *args: None,
            "precondition": lambda *args: states.append(args),
    }
    source = _notebook_cell("select-batch").replace("BATCH_ID = None", f"BATCH_ID = {batch_id!r}")
    InteractiveInterpreter(namespace).runcode(compile(source, str(NOTEBOOK_PATH), "exec"))
    return {"batch": namespace["persisted_batch"], "states": states}


def test_browser_smoke_uses_one_confirmed_isolated_database() -> None:
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "--confirm-reset-local-demo" in workflow
    assert workflow.count("var/local-demo/invoiceops.db") == 2
    assert 'INVOICEOPS_DB_PATH="$INVOICEOPS_DB_PATH"' in workflow
    assert "scripts/bootstrap_local_demo.py --initialize-demo-root" in workflow
    assert "--demo-root var/local-demo" in workflow


def test_student_materials_describe_one_canonical_reset_and_no_rendered_fallback() -> None:
    sources = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "notebooks/README.md",
        PROJECT_ROOT / "docs/class-02-mlops-runbook.md",
        PROJECT_ROOT / "docs/class-03-continuity-runbook.md",
        PROJECT_ROOT / "docs/manual-ejecucion-macos.md",
        PROJECT_ROOT / "docs/manual-ejecucion-windows.md",
    )
    material = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    assert "--demo-root distinto" not in material
    assert "var/local-demo" in material
    assert "dataset canónico" in material
    assert "notebooks/rendered/" not in material
    assert not tuple((PROJECT_ROOT / "notebooks/rendered").glob("*.html"))


def test_notebook_01_uses_the_isolated_dataset_and_handles_uninitialized_reset() -> None:
    notebook = json.loads(NOTEBOOK_01_PATH.read_text(encoding="utf-8"))
    clean_start = "".join(next(cell["source"] for cell in notebook["cells"] if cell["id"] == "clean-start"))
    load_data = "".join(next(cell["source"] for cell in notebook["cells"] if cell["id"] == "load-data"))

    assert "canonical_demo_root" in clean_start
    assert "INITIALIZE_DEMO_ROOT" in clean_start
    assert "except ValueError" in clean_start
    assert "reset_local_demo(DEMO_ROOT, confirmed=True)" in clean_start
    assert 'DATASET_ROOT = canonical_demo_root() / "data"' in load_data
    assert 'Path("data")' not in load_data
    assert "ensure_canonical_dataset" in load_data
    assert "ensure_canonical_dataset(output_root=DATASET_ROOT)" in load_data
    assert "generate_synthetic_dataset" not in load_data


def test_notebook_01_dataset_root_rejects_altered_canonical_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    dataset_root = demo_paths.canonical_demo_root() / "data"
    dataset_dir, status = ensure_canonical_dataset(output_root=dataset_root)

    assert dataset_dir == dataset_root / "invoice-risk-v1"
    assert status == "created"
    with (dataset_dir / "train.csv").open("a", encoding="utf-8") as file:
        file.write("altered canonical lineage\n")

    with pytest.raises(ValueError, match="contents do not match its metadata"):
        ensure_canonical_dataset(output_root=dataset_root)


def test_reset_materials_list_all_resources_and_bootstrap_dataset_recreation() -> None:
    reset_sources = (
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "notebooks/README.md",
        PROJECT_ROOT / "docs/class-02-mlops-runbook.md",
        PROJECT_ROOT / "docs/class-03-continuity-runbook.md",
        PROJECT_ROOT / "docs/manual-ejecucion-macos.md",
        PROJECT_ROOT / "docs/manual-ejecucion-windows.md",
        NOTEBOOK_01_PATH,
        NOTEBOOK_PATH,
    )
    required_resources = (
        "invoiceops.db",
        "invoiceops.db-shm",
        "invoiceops.db-wal",
        "mlflow.db",
        "mlflow-artifacts",
        "notebook-state/state.json",
        "data/invoice-risk-v1",
    )

    for path in reset_sources:
        content = path.read_text(encoding="utf-8")
        assert "data/invoice-risk-v1" in content, path
        assert "bootstrap" in content.lower(), path
    material = "\n".join(path.read_text(encoding="utf-8") for path in reset_sources)
    for resource in required_resources:
        assert resource in material


def test_student_notebooks_have_clean_metadata_and_no_private_imports() -> None:
    for notebook_path in sorted((PROJECT_ROOT / "notebooks").glob("*.ipynb")):
        notebook = json.loads(notebook_path.read_text(encoding="utf-8"))
        assert notebook["metadata"]["kernelspec"]["display_name"] == "InvoiceOps Python 3.12"
        for cell in notebook["cells"]:
            assert cell.get("execution_count") is None
            assert cell.get("outputs", []) == []
            source = "".join(cell.get("source", []))
            assert "._" not in source


def test_uipath_ownership_keeps_source_and_package_but_ignores_studio_state() -> None:
    ignore_rules = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    ownership = (PROJECT_ROOT / "uipath/README.md").read_text(encoding="utf-8")

    assert "uipath/**/.local/" in ignore_rules
    assert "uipath/**/.storage/" in ignore_rules
    assert "uipath/**/.screenshots/" in ignore_rules
    assert (PROJECT_ROOT / "uipath/InvoiceOps-RPA-Demo/Main.xaml").is_file()
    assert (PROJECT_ROOT / "uipath/InvoiceOps-RPA-Demo.uis").is_file()
    assert "fuente revisable" in ownership
    assert "paquete distribuible" in ownership


def test_isolated_reset_bootstrap_then_notebook_06_selection_states(tmp_path: Path, monkeypatch) -> None:
    """Exercise the guarded clean start and actual Notebook 06 selection cells."""
    monkeypatch.setattr(demo_paths, "PROJECT_ROOT", tmp_path)
    db_path = tmp_path / "var" / "local-demo" / "invoiceops.db"
    repository_dataset_root = NOTEBOOK_PATH.parents[1] / "data" / "invoice-risk-v1"
    repository_dataset_snapshot = (
        tuple(
            (path.relative_to(repository_dataset_root), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(repository_dataset_root.rglob("*"))
        )
        if repository_dataset_root.exists()
        else None
    )
    demo_paths.initialize_demo_root(Path("var/local-demo"))
    reset_local_demo(Path("var/local-demo"), confirmed=True)

    monkeypatch.setattr(bootstrap, "ensure_tracking_available", lambda: object())
    monkeypatch.setattr(
        bootstrap, "find_compatible_random_forest_run", lambda client, dataset_dir: "run-notebook-state"
    )
    monkeypatch.setattr(bootstrap, "evaluate_run", lambda run_id: (0.20, 0.50))
    monkeypatch.setattr(bootstrap, "ensure_registered_version", lambda run_id: ("7", "reused"))
    monkeypatch.setattr(bootstrap, "ensure_champion", lambda version, run_id: "unchanged")

    result = bootstrap.bootstrap_local(db_path)

    assert db_path.is_file()
    assert result.sqlite == "migrated_and_seeded"
    assert (db_path.parent / "data" / "invoice-risk-v1" / "metadata.json").is_file()
    current_repository_dataset_snapshot = (
        tuple(
            (path.relative_to(repository_dataset_root), path.stat().st_size, path.stat().st_mtime_ns)
            for path in sorted(repository_dataset_root.rglob("*"))
        )
        if repository_dataset_root.exists()
        else None
    )
    assert current_repository_dataset_snapshot == repository_dataset_snapshot

    empty = _run_evaluation_selection(db_path, None)
    assert empty["selection"].ready is False

    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-notebook-state",
        model_name="invoice-review",
        model_version="7",
        run_id="run-notebook-state",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )

    class MlflowRun:
        def __init__(self) -> None:
            self.data = SimpleNamespace(
                params={
                    "dataset_version": "invoice-risk-v1",
                    "feature_schema_version": "invoice-features-v1",
                },
                tags={"git_commit": "a" * 40},
            )

    class MlflowClient:
        def get_run(self, run_id: str) -> MlflowRun:
            assert run_id == "run-notebook-state"
            return MlflowRun()

    monkeypatch.setattr("invoiceops.evidence.MlflowClient", MlflowClient)
    invalid_evaluation = _run_evaluation_selection(db_path, 999)
    valid_evaluation = _run_evaluation_selection(db_path, 1)
    assert invalid_evaluation["selection"].ready is False
    assert valid_evaluation["selection"].ready is True

    persist_evidence_records(db_path, [_record(1)])
    batch = create_evidence_batch(db_path, [1])
    empty_batch = _run_batch_selection(db_path, 1, None)
    invalid_batch = _run_batch_selection(db_path, 1, 999)
    valid_batch = _run_batch_selection(db_path, 1, batch.id)
    assert empty_batch["batch"] is None
    assert invalid_batch["batch"] is None
    assert valid_batch["batch"].id == batch.id
