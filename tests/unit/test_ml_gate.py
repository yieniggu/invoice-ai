import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest


def load_gate(
    monkeypatch: pytest.MonkeyPatch, metrics: dict[str, float]
) -> tuple[ModuleType, list[tuple[str, object]]]:
    calls: list[tuple[str, object]] = []
    mlflow = ModuleType("mlflow")
    tracking = ModuleType("mlflow.tracking")

    def set_tracking_uri(uri: str) -> None:
        calls.append(("set_tracking_uri", uri))

    class StubMlflowClient:
        def get_run(self, run_id: str) -> SimpleNamespace:
            calls.append(("get_run", run_id))
            return SimpleNamespace(data=SimpleNamespace(metrics=metrics))

    mlflow.set_tracking_uri = set_tracking_uri
    tracking.MlflowClient = StubMlflowClient
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.delitem(sys.modules, "invoiceops.ml.gate", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.example")
    return importlib.import_module("invoiceops.ml.gate"), calls


def test_gate_cli_passes_when_the_explicit_run_meets_both_thresholds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gate, calls = load_gate(
        monkeypatch, {"recall": 0.1856763925729443, "precision": 0.4861111111111111}
    )
    monkeypatch.setattr(sys, "argv", ["gate", "--run-id", "run-123"])

    gate.main()

    assert calls == [("set_tracking_uri", "http://mlflow.example"), ("get_run", "run-123")]
    assert capsys.readouterr().out == (
        "Recall: 0.185676 PASS\nPrecision: 0.486111 PASS\nMODEL GATE: PASS\n"
    )


def test_gate_cli_fails_when_the_explicit_run_misses_a_threshold(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gate, _ = load_gate(monkeypatch, {"recall": 0.18, "precision": 0.47})
    monkeypatch.setattr(sys, "argv", ["gate", "--run-id", "run-123"])

    with pytest.raises(SystemExit, match="1"):
        gate.main()

    assert capsys.readouterr().out == (
        "Recall: 0.180000 PASS\nPrecision: 0.470000 FAIL\nMODEL GATE: FAIL\n"
    )


def test_gate_rejects_runs_without_required_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    gate, _ = load_gate(monkeypatch, {"recall": 0.1856763925729443})

    with pytest.raises(ValueError, match="Missing required MLflow metrics: precision"):
        gate.evaluate_run("run-123")
