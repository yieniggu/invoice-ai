import json
import re
from pathlib import Path
from typing import Self
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

import invoiceops.legacy.app as legacy_app
from invoiceops.domain.models import InvoiceStatus
from invoiceops.domain.policy import recommend_from_probability
from invoiceops.evidence import (
    EvidenceError,
    EvidenceProvenance,
    EvidenceRecord,
    create_evidence_batch,
)
from invoiceops.legacy import faults
from invoiceops.legacy.app import create_app
from invoiceops.legacy.db import (
    _connect,
    get_invoice,
    insert_evidence_batch_anchor,
    insert_model_evaluation,
    list_decision_events,
    list_model_evaluations,
    resolve_db_path,
    update_evidence_batch_anchor,
)
from invoiceops.legacy.seed import seed_invoices
from invoiceops.model_api.schemas import PredictionResponse


def test_login_required(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    for path in ("/invoices", "/invoices/INV-10023", "/admin/faults"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/evidence/batches"),
        ("POST", "/evidence/batches"),
        ("GET", "/evidence/batches/1"),
        ("POST", "/evidence/batches/1/anchor/request"),
        ("POST", "/evidence/batches/1/anchor/confirm"),
    ],
)
def test_evidence_routes_require_login(db_path: Path, method: str, path: str) -> None:
    client = TestClient(create_app(db_path))

    response = client.request(method, path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_success(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    response = client.post(
        "/login",
        data={"username": "analyst", "password": "demo-password"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/invoices"
    csrf_token = csrf_token_from(client.get("/invoices"))
    logout = client.post("/logout", data={"csrf_token": csrf_token}, follow_redirects=False)
    assert logout.headers["location"] == "/login"


def test_login_rejects_invalid_credentials(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    response = client.post("/login", data={"username": "analyst", "password": "wrong-password"})

    assert response.status_code == 401
    assert client.get("/invoices", follow_redirects=False).headers["location"] == "/login"


def test_login_rejects_unicode_invalid_credentials(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    response = client.post("/login", data={"username": "analysté", "password": "wrong-password"})

    assert response.status_code == 401
    assert client.get("/invoices", follow_redirects=False).headers["location"] == "/login"


def test_secure_mode_requires_explicit_non_demo_configuration(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVOICEOPS_MODE", "secure")
    monkeypatch.delenv("INVOICEOPS_SESSION_SECRET", raising=False)
    monkeypatch.setenv("INVOICEOPS_DEMO_USERNAME", "secure-analyst")
    monkeypatch.setenv("INVOICEOPS_DEMO_PASSWORD", "secure-password")

    with pytest.raises(ValueError, match="INVOICEOPS_SESSION_SECRET"):
        create_app(db_path)


def test_mode_must_be_explicit_outside_local_demo(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INVOICEOPS_MODE")

    with pytest.raises(ValueError, match="INVOICEOPS_MODE"):
        create_app(db_path)


def test_secure_mode_rejects_demo_session_secret(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVOICEOPS_MODE", "secure")
    monkeypatch.setenv("INVOICEOPS_DEMO_USERNAME", "secure-analyst")
    monkeypatch.setenv("INVOICEOPS_DEMO_PASSWORD", "secure-password")
    monkeypatch.setenv("INVOICEOPS_SESSION_SECRET", "dev-only-change-me")
    monkeypatch.setenv("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS", "secure-analyst")

    with pytest.raises(ValueError, match="must not use the demo secret"):
        create_app(db_path)


@pytest.mark.parametrize("allowed_principals", [None, "", "secure-analyst,"])
def test_secure_mode_requires_valid_decision_principal_allow_list(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, allowed_principals: str | None
) -> None:
    monkeypatch.setenv("INVOICEOPS_MODE", "secure")
    monkeypatch.setenv("INVOICEOPS_DEMO_USERNAME", "secure-analyst")
    monkeypatch.setenv("INVOICEOPS_DEMO_PASSWORD", "secure-password")
    monkeypatch.setenv(
        "INVOICEOPS_SESSION_SECRET", "secure-test-secret-that-is-not-the-demo-secret"
    )
    if allowed_principals is None:
        monkeypatch.delenv("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS", raising=False)
    else:
        monkeypatch.setenv("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS", allowed_principals)

    with pytest.raises(ValueError, match="INVOICEOPS_ALLOWED_DECISION_PRINCIPALS"):
        create_app(db_path)


def test_secure_mode_sets_secure_session_cookie(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVOICEOPS_MODE", "secure")
    monkeypatch.setenv(
        "INVOICEOPS_SESSION_SECRET", "secure-test-secret-that-is-not-the-demo-secret"
    )
    monkeypatch.setenv("INVOICEOPS_DEMO_USERNAME", "secure-analyst")
    monkeypatch.setenv("INVOICEOPS_DEMO_PASSWORD", "secure-password")
    monkeypatch.setenv("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS", "secure-analyst")
    client = TestClient(create_app(db_path))

    response = client.post(
        "/login",
        data={"username": "secure-analyst", "password": "secure-password"},
        follow_redirects=False,
    )

    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_invoice_list(db_path: Path) -> None:
    client = authenticated_client(db_path)

    response = client.get("/invoices?q=acME")

    assert response.status_code == 200
    assert "INV-10023" in response.text
    assert "Acme Industrial" in response.text
    assert "Northwind Parts" not in response.text


def test_invoice_list_discloses_when_results_are_limited(db_path: Path) -> None:
    insert_invoices(db_path, count=101)
    client = authenticated_client(db_path)

    response = client.get("/invoices")

    assert response.status_code == 200
    assert (
        "Showing the first 100 results. Refine your search to see fewer results." in response.text
    )


def test_invoice_detail(db_path: Path) -> None:
    response = authenticated_client(db_path).get("/invoices/INV-10030")

    assert response.status_code == 200
    assert 'data-testid="invoice-id">INV-10030' in response.text
    assert 'data-testid="invoice-amount">$4200.00' in response.text
    assert 'data-testid="invoice-has-po">Yes' in response.text
    assert 'data-testid="invoice-three-way-match">Yes' in response.text
    assert 'data-testid="invoice-status">PENDING' in response.text
    assert 'data-testid="invoice-process"' in response.text
    assert 'data-testid="invoice-manual-review"' in response.text
    assert "<h2>Risk Context</h2>" in response.text
    assert "Vendor tenure" in response.text
    assert "12 days" in response.text
    assert "Previous incidents" in response.text
    assert ">4<" in response.text
    assert "Bank account changed" in response.text
    assert ">Yes<" in response.text
    assert "Amount vs vendor median" in response.text
    assert ">3.40x<" in response.text
    assert "Country risk" in response.text
    assert ">High<" in response.text
    assert "Model evaluations" in response.text
    assert "No model evaluations recorded." in response.text
    for feature in (
        "invoice_amount_cents",
        "has_purchase_order",
        "three_way_match",
        "vendor_tenure_days",
        "previous_incidents_12m",
        "bank_account_recently_changed",
        "amount_vs_vendor_median",
        "country_risk",
    ):
        assert feature in response.text

    other_demo_response = authenticated_client(db_path).get("/invoices/INV-10029")
    non_demo_response = authenticated_client(db_path).get("/invoices/INV-10023")

    assert "Model features" in other_demo_response.text
    assert "2200" in other_demo_response.text
    assert "Model features" in non_demo_response.text


def test_invoice_detail_shows_model_evaluation_history(db_path: Path) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10030",
        correlation_id="corr-model",
        model_name="manual-review-model",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.82,
        recommendation=recommend_from_probability(0.82),
        created_at="2026-01-01T00:00:00+00:00",
    )

    response = authenticated_client(db_path).get("/invoices/INV-10030")

    assert "Model evaluations" in response.text
    assert "0.82" in response.text
    assert "manual-review-model" in response.text
    assert "probability_at_or_above_threshold" in response.text
    assert "corr-model" in response.text
    assert "ml-policy-v1 at 0.8" in response.text
    assert "model" in response.text


def test_ui_model_evaluation_is_idempotent_and_does_not_decide_invoice(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))
    monkeypatch.setattr(
        legacy_app,
        "request_model_prediction",
        lambda _: PredictionResponse(
            manual_review_probability=0.82,
            model_name="invoice-review",
            model_version="7",
            run_id="run-123",
        ),
    )

    first = client.post(
        "/invoices/INV-10023/evaluate", data={"csrf_token": csrf_token}, follow_redirects=False
    )
    second = client.post(
        "/invoices/INV-10023/evaluate", data={"csrf_token": csrf_token}, follow_redirects=False
    )
    detail = client.get("/invoices/INV-10023")

    assert first.status_code == 303
    assert second.status_code == 303
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert len(list_model_evaluations(db_path, "INV-10023")) == 1
    assert "0.82" in detail.text
    assert "invoice-review v7" in detail.text
    assert "run-123" in detail.text
    assert "ml-policy-v1 at 0.8" in detail.text


def test_ui_model_evaluation_failure_is_recoverable_and_does_not_persist(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))
    monkeypatch.setattr(
        legacy_app,
        "request_model_prediction",
        lambda _: (_ for _ in ()).throw(legacy_app.ModelApiUnavailableError("Model API is unavailable.")),
    )

    response = client.post("/invoices/INV-10023/evaluate", data={"csrf_token": csrf_token})

    assert response.status_code == 503
    assert "Model API is unavailable." in response.text
    assert list_model_evaluations(db_path, "INV-10023") == []


def test_local_dotenv_configures_model_api_without_overriding_process_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("INVOICEOPS_MODEL_API_URL=http://model-api.test\n")
    monkeypatch.delenv("INVOICEOPS_MODEL_API_URL", raising=False)
    calls: list[tuple[str, float]] = []

    class Response:
        status = 200

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"manual_review_probability": 0.82, "model_name": "invoice-review", "model_version": "7", "run_id": "run-123"}'

    def fake_urlopen(request: object, *, timeout: float) -> Response:
        calls.append((request.full_url, timeout))
        return Response()

    monkeypatch.setattr(legacy_app, "urlopen", fake_urlopen)
    legacy_app.load_local_env(dotenv_path)

    prediction = legacy_app.request_model_prediction({"invoice_amount_cents": 100})

    assert prediction.model_name == "invoice-review"
    assert calls == [("http://model-api.test/predict", 5.0)]

    monkeypatch.setenv("INVOICEOPS_MODEL_API_URL", "https://process.example")
    legacy_app.load_local_env(dotenv_path)

    assert legacy_app.os.environ["INVOICEOPS_MODEL_API_URL"] == "https://process.example"


def test_env_example_configures_portal_database_at_canonical_local_demo_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = Path(__file__).parents[2]
    monkeypatch.delenv("INVOICEOPS_DB_PATH", raising=False)

    legacy_app.load_local_env(project_root / ".env.example")

    assert resolve_db_path(None) == project_root / "var" / "local-demo" / "invoiceops.db"


def test_ui_evidence_persists_only_after_complete_lineage(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-evidence",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.82,
        recommendation=recommend_from_probability(0.82),
    )
    evaluation = list_model_evaluations(db_path, "INV-10023")[0]
    record = EvidenceRecord(
        evaluation_id=evaluation["id"],
        invoice_id="INV-10023",
        correlation_id="corr-evidence",
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability="0.82",
        policy_version="ml-policy-v1",
        policy_threshold="0.8",
        recommendation="MANUAL_REVIEW",
        source="model",
        reason="probability_at_or_above_threshold",
        evaluation_created_at=evaluation["created_at"],
        provenance=EvidenceProvenance("dataset-v1", "invoice-features-v1", "commit-123"),
    )
    monkeypatch.setattr(legacy_app, "build_evidence_record", lambda *_: record)
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))

    response = client.post(
        "/invoices/INV-10023/evidence", data={"csrf_token": csrf_token}, follow_redirects=False
    )
    detail = client.get("/invoices/INV-10023")

    assert response.status_code == 303
    assert "invoice-evidence-v1" in detail.text
    assert "Build evidence record" not in detail.text


def test_ui_evidence_failure_is_recoverable_and_does_not_persist(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-incomplete-lineage",
        recommendation=recommend_from_probability(0.82),
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.82,
    )
    monkeypatch.setattr(
        legacy_app,
        "build_evidence_record",
        lambda *_: (_ for _ in ()).throw(EvidenceError("MLflow run is unavailable: run-123")),
    )
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))

    response = client.post("/invoices/INV-10023/evidence", data={"csrf_token": csrf_token})

    assert response.status_code == 503
    assert "Evidence could not be saved" in response.text
    assert "No evidence record is persisted." in response.text


def test_ui_evidence_shows_persisted_batch_anchor_and_configured_transaction_url(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-anchor",
        recommendation=recommend_from_probability(0.82),
        model_name="invoice-review",
        model_version="7",
        run_id="run-123",
        manual_review_probability=0.82,
    )
    evaluation = list_model_evaluations(db_path, "INV-10023")[0]
    record = EvidenceRecord(
        evaluation_id=evaluation["id"], invoice_id="INV-10023", correlation_id="corr-anchor",
        model_name="invoice-review", model_version="7", run_id="run-123",
        manual_review_probability="0.82", policy_version="ml-policy-v1", policy_threshold="0.8",
        recommendation="MANUAL_REVIEW", source="model", reason="probability_at_or_above_threshold",
        evaluation_created_at=evaluation["created_at"],
        provenance=EvidenceProvenance("dataset-v1", "invoice-features-v1", "commit-123"),
    )
    legacy_app.persist_evidence_records(db_path, [record])
    batch = create_evidence_batch(db_path, [evaluation["id"]])
    anchor_id = insert_evidence_batch_anchor(
        db_path, batch_id=batch.id, root_hash=batch.root_hash, chain_id=31337,
        contract_address="0x0000000000000000000000000000000000000001", transaction_hash="0xabc",
        submitted_at="2026-01-01T00:00:00Z", status="submitted",
    )
    update_evidence_batch_anchor(
        db_path, anchor_id, status="verified", block_number=42, gas_used=21000,
        anchored_at="2026-01-01T00:01:00Z",
    )
    monkeypatch.setenv("INVOICEOPS_EVM_EXPLORER_TX_URL_TEMPLATE", "https://explorer.test/tx/{tx_hash}")

    response = authenticated_client(db_path).get("/invoices/INV-10023")

    assert batch.root_hash in response.text
    assert "31337" in response.text
    assert "42 / 21000" in response.text
    assert 'href="https://explorer.test/tx/0xabc"' in response.text

    monkeypatch.setenv("INVOICEOPS_EVM_EXPLORER_TX_URL_TEMPLATE", "https://explorer.test/tx")
    fallback_response = authenticated_client(db_path).get("/invoices/INV-10023")

    assert '<span class="copyable">0xabc</span>' in fallback_response.text


def test_notebook_05_and_portal_share_the_operational_database_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "invoiceops.db"
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    seed_invoices()
    insert_model_evaluation(
        None,
        "INV-10030",
        correlation_id="notebook-05-visible-in-portal",
        recommendation=recommend_from_probability(0.82),
        model_name="invoice-review",
        model_version="7",
        run_id="run-notebook-05",
        manual_review_probability=0.82,
    )

    notebook_path = Path(__file__).parents[2] / "notebooks" / "05_serving_policy_and_audit.ipynb"
    notebook = json.loads(notebook_path.read_text())
    notebook_source = "\n".join(
        "".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == "code"
    )
    client = TestClient(create_app())
    client.post("/login", data={"username": "analyst", "password": "demo-password"})

    response = client.get("/invoices/INV-10030")

    assert "INVOICEOPS_DB_PATH" in notebook_source
    assert response.status_code == 200
    assert "notebook-05-visible-in-portal" in response.text
    assert "invoice-review" in response.text


def test_faults_page_and_button_label_fault(db_path: Path) -> None:
    client = authenticated_client(db_path)

    try:
        faults_page = client.get("/admin/faults")
        csrf_token = csrf_token_from(faults_page)
        response = client.post(
            "/admin/faults",
            data={
                "fault": "change_process_button_label",
                "enabled": "true",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        detail = client.get("/invoices/INV-10023")

        assert faults_page.status_code == 200
        assert "Button label OFF" in faults_page.text
        assert response.status_code == 303
        assert 'data-testid="invoice-process"' in detail.text
        assert ">Complete</button>" in detail.text
    finally:
        faults.reset_faults()


def test_fault_controls_validate_latency_and_reset(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = authenticated_client(db_path)
    sleep_calls: list[float] = []
    monkeypatch.setattr(faults, "sleep", sleep_calls.append)

    try:
        csrf_token = csrf_token_from(client.get("/admin/faults"))
        missing_csrf = client.post(
            "/admin/faults",
            data={"fault": "portal_latency_ms", "latency_ms": "3000"},
            follow_redirects=False,
        )
        latency = client.post(
            "/admin/faults",
            data={"fault": "portal_latency_ms", "latency_ms": "3000", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        detail = client.get("/invoices/INV-10023")
        invalid = client.post(
            "/admin/faults",
            data={"fault": "portal_latency_ms", "latency_ms": "1", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        unknown = client.post(
            "/admin/faults",
            data={"fault": "unknown", "csrf_token": csrf_token},
            follow_redirects=False,
        )
        button_label = client.post(
            "/admin/faults",
            data={
                "fault": "change_process_button_label",
                "enabled": "true",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        decision_api = client.post(
            "/admin/faults",
            data={
                "fault": "decision_api_unavailable",
                "enabled": "true",
                "csrf_token": csrf_token,
            },
            follow_redirects=False,
        )
        reset = client.post(
            "/admin/faults/reset",
            data={"csrf_token": csrf_token},
            follow_redirects=False,
        )
        reset_page = client.get("/admin/faults")

        assert missing_csrf.status_code == 403
        assert latency.status_code == 303
        assert detail.status_code == 200
        assert sleep_calls == [3.0]
        assert invalid.status_code == 422
        assert unknown.status_code == 422
        assert button_label.status_code == 303
        assert decision_api.status_code == 303
        assert reset.status_code == 303
        assert "Latency 0 ms" in reset_page.text
        assert "Button label OFF" in reset_page.text
        assert "Decision API AVAILABLE" in reset_page.text
    finally:
        faults.reset_faults()


def test_ui_decision(db_path: Path) -> None:
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))

    response = client.post(
        "/invoices/INV-10023/decision",
        data={"decision": "AUTO_PROCESS", "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/invoices/INV-10023"
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    events = list_decision_events(db_path, "INV-10023")
    assert len(events) == 1
    assert events[0]["actor"] == "ui"
    assert events[0]["rule_version"] == "invoice-rules-v1"
    assert UUID(events[0]["correlation_id"])
    detail = client.get("/invoices/INV-10023")
    assert "invoice-rules-v1" in detail.text
    assert "ui" in detail.text
    assert events[0]["correlation_id"] in detail.text


def test_invalid_decision_is_controlled_and_does_not_mutate(db_path: Path) -> None:
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/invoices/INV-10023"))

    missing = client.post(
        "/invoices/UNKNOWN/decision", data={"decision": "AUTO_PROCESS", "csrf_token": csrf_token}
    )
    cancelled = client.post(
        "/invoices/INV-10028/decision",
        data={"decision": "AUTO_PROCESS", "csrf_token": csrf_token},
    )

    assert missing.status_code == 404
    assert "Invoice not found." in missing.text
    assert cancelled.status_code == 409
    assert "Invoice cannot be decided" in cancelled.text
    assert get_invoice(db_path, "INV-10028").status is InvoiceStatus.CANCELLED
    assert list_decision_events(db_path, "INV-10028") == []


def test_logout_rejects_missing_csrf_and_keeps_session(db_path: Path) -> None:
    client = authenticated_client(db_path)

    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 403
    assert client.get("/invoices").status_code == 200


def test_decision_rejects_missing_csrf_without_mutating_sqlite(db_path: Path) -> None:
    client = authenticated_client(db_path)

    response = client.post(
        "/invoices/INV-10023/decision", data={"decision": "AUTO_PROCESS"}, follow_redirects=False
    )

    assert response.status_code == 403
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


def test_csrf_token_is_bound_to_its_client_session(db_path: Path) -> None:
    first_client = authenticated_client(db_path)
    second_client = authenticated_client(db_path)
    first_token = csrf_token_from(first_client.get("/invoices/INV-10023"))
    second_token = csrf_token_from(second_client.get("/invoices/INV-10023"))

    response = second_client.post(
        "/invoices/INV-10023/decision",
        data={"decision": "AUTO_PROCESS", "csrf_token": first_token},
        follow_redirects=False,
    )

    assert first_token != second_token
    assert response.status_code == 403
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


def test_evidence_batch_ui_requires_two_verified_records_and_shows_proofs(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/evidence/batches"))

    too_small = client.post(
        "/evidence/batches", data={"evaluation_id": str(first_id), "csrf_token": csrf_token}
    )
    created = client.post(
        "/evidence/batches",
        data={"evaluation_id": [str(first_id), str(second_id)], "csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert too_small.status_code == 422
    assert "Select at least two verified evidence records." in too_small.text
    assert created.status_code == 303
    detail = client.get(created.headers["location"])
    assert "Merkle root" in detail.text
    assert "Technical details" in detail.text
    assert "Leaves: Evidence Records" in detail.text
    assert "Batch verified" in detail.text
    assert "How this batch is sealed" in detail.text
    assert "Evidence source" in detail.text
    assert "Canonical payload (persisted)" in detail.text
    assert "keccak256(left || right)" in detail.text
    assert "matches persisted batch root" in detail.text
    assert "Merkle root is valid" in detail.text
    assert "Root is not anchored" in detail.text


def test_evidence_batch_detail_uses_the_default_database_path(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    monkeypatch.setenv("INVOICEOPS_DB_PATH", str(db_path))
    client = TestClient(create_app())
    client.post("/login", data={"username": "analyst", "password": "demo-password"})
    csrf_token = csrf_token_from(client.get("/evidence/batches"))

    created = client.post(
        "/evidence/batches",
        data={"evaluation_id": [str(first_id), str(second_id)], "csrf_token": csrf_token},
        follow_redirects=False,
    )
    before_detail = db_path.read_bytes()
    detail = client.get(created.headers["location"])

    assert created.status_code == 303
    assert detail.status_code == 200
    assert "Batch verified" in detail.text
    assert db_path.read_bytes() == before_detail


def test_evidence_batch_errors_exclude_invalid_persisted_records(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_id, invalid_id = persist_two_evidence_records(db_path, monkeypatch)
    with _connect(db_path) as connection:
        connection.execute(
            "UPDATE evidence_records SET digest_hex = ? WHERE evaluation_id = ?",
            ("0" * 64, invalid_id),
        )
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get("/evidence/batches"))

    response = client.post(
        "/evidence/batches",
        data={"evaluation_id": str(valid_id), "csrf_token": csrf_token},
    )

    def rejected_batch(*_args: object) -> object:
        raise EvidenceError("Evidence batch is invalid.")

    monkeypatch.setattr(
        legacy_app,
        "create_evidence_batch",
        rejected_batch,
    )
    batch_failure = client.post(
        "/evidence/batches",
        data={"evaluation_id": [str(valid_id), str(invalid_id)], "csrf_token": csrf_token},
    )

    assert response.status_code == 422
    assert batch_failure.status_code == 422
    for error_response in (response, batch_failure):
        assert f'value="{valid_id}"' in error_response.text
        assert f'value="{invalid_id}"' not in error_response.text


def test_evidence_batch_pages_list_lineage_and_render_guided_merkle_view(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    insert_model_evaluation(
        db_path,
        "INV-10024",
        correlation_id="corr-successor-ui",
        recommendation=recommend_from_probability(0.82),
        model_name="invoice-review",
        model_version="7",
        run_id="run-successor-ui",
        manual_review_probability=0.82,
    )
    third_id = list_model_evaluations(db_path, "INV-10024")[0]["id"]
    legacy_app.persist_evidence_records(db_path, [_evidence_record(third_id, "INV-10024", "3")])
    origin = create_evidence_batch(db_path, [first_id, second_id])
    successor = legacy_app.create_evidence_batch_successor(db_path, origin.id, [third_id])
    client = authenticated_client(db_path)

    listing = client.get("/evidence/batches")
    detail = client.get(f"/evidence/batches/{successor.id}")
    invoice = client.get("/invoices/INV-10023")

    assert f"Evidence batch {origin.id}" in listing.text
    assert f"/evidence/batches/{origin.id}" in listing.text
    assert f"Already included in batch {origin.id}" in listing.text
    assert f"Already included in batch {successor.id}" in listing.text
    assert listing.text.count(f'href="/evidence/batches/{origin.id}"') >= 2
    assert listing.text.count(f'href="/evidence/batches/{successor.id}"') >= 2
    assert "Already included in batch" in listing.text
    assert "Merkle tree" in detail.text
    assert "Technical details" in detail.text
    assert "right duplicates the final hash" in detail.text
    assert "sibling" not in detail.text
    assert f"Successor of batch {origin.id}" in detail.text
    assert "/invoices/INV-10023" in detail.text
    assert "Related evidence batches" in invoice.text
    assert f"Batch {origin.id}" in invoice.text
    assert f"Batch {successor.id}" in invoice.text


def test_evidence_batch_successor_post_rejects_reused_evidence_without_creating_batch(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    origin = create_evidence_batch(db_path, [first_id, second_id])
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get(f"/evidence/batches?source_batch_id={origin.id}"))

    response = client.post(
        "/evidence/batches",
        data={
            "source_batch_id": str(origin.id),
            "evaluation_id": str(first_id),
            "csrf_token": csrf_token,
        },
    )

    assert response.status_code == 422
    assert "already belongs" in response.text
    with _connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM evidence_batches").fetchone()[0] == 1


def test_local_anchor_confirmation_is_server_side_single_use(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    batch = create_evidence_batch(db_path, [first_id, second_id])
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get(f"/evidence/batches/{batch.id}"))
    calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        legacy_app,
        "local_anchor_preflight",
        lambda _db_path, _batch: {"web3": object(), "deployment": object(), "chain_id": 31337, "contract_address": "0x0000000000000000000000000000000000000001", "signer": "0x0000000000000000000000000000000000000002"},
    )
    monkeypatch.setattr(
        legacy_app,
        "anchor_evidence_batch",
        lambda _db_path, *, batch_id, root_hash, web3, deployment, signer: calls.append((batch_id, root_hash)),
    )

    challenge = client.post(
        f"/evidence/batches/{batch.id}/anchor/request",
        data={"csrf_token": csrf_token},
    )
    token = re.search(r'name="challenge_token" value="([^"]+)"', challenge.text)
    assert token is not None
    confirmed = client.post(
        f"/evidence/batches/{batch.id}/anchor/confirm",
        data={"csrf_token": csrf_token, "challenge_token": token.group(1)},
        follow_redirects=False,
    )
    repeated = client.post(
        f"/evidence/batches/{batch.id}/anchor/confirm",
        data={"csrf_token": csrf_token, "challenge_token": token.group(1)},
    )

    assert challenge.status_code == 200
    assert "Confirm local Anvil anchor" in challenge.text
    assert confirmed.status_code == 303
    assert repeated.status_code == 409
    assert calls == [(batch.id, batch.root_hash)]


def test_local_anchor_preflight_failure_is_recoverable_without_submission(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_id, second_id = persist_two_evidence_records(db_path, monkeypatch)
    batch = create_evidence_batch(db_path, [first_id, second_id])
    client = authenticated_client(db_path)
    csrf_token = csrf_token_from(client.get(f"/evidence/batches/{batch.id}"))
    submitted = False

    def failed_preflight(_db_path: Path, _batch: object) -> dict[str, object]:
        raise legacy_app.AnchorError("Anvil is unavailable")

    def unexpected_submission(*args: object, **kwargs: object) -> None:
        nonlocal submitted
        submitted = True

    monkeypatch.setattr(legacy_app, "local_anchor_preflight", failed_preflight)
    monkeypatch.setattr(legacy_app, "anchor_evidence_batch", unexpected_submission)

    response = client.post(
        f"/evidence/batches/{batch.id}/anchor/request", data={"csrf_token": csrf_token}
    )

    assert response.status_code == 503
    assert "Local Anvil preflight failed" in response.text
    assert not submitted


def persist_two_evidence_records(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[int, int]:
    evaluation_ids: list[int] = []
    for number, invoice_id in enumerate(("INV-10023", "INV-10030"), start=1):
        insert_model_evaluation(
            db_path,
            invoice_id,
            correlation_id=f"corr-batch-{number}",
            recommendation=recommend_from_probability(0.82),
            model_name="invoice-review",
            model_version="7",
            run_id=f"run-batch-{number}",
            manual_review_probability=0.82,
        )
        evaluation_ids.append(list_model_evaluations(db_path, invoice_id)[0]["id"])
    records = [
        EvidenceRecord(
            evaluation_id=evaluation_id,
            invoice_id=invoice_id,
            correlation_id=f"corr-batch-{number}",
            model_name="invoice-review",
            model_version="7",
            run_id=f"run-batch-{number}",
            manual_review_probability="0.82",
            policy_version="ml-policy-v1",
            policy_threshold="0.8",
            recommendation="MANUAL_REVIEW",
            source="model",
            reason="probability_at_or_above_threshold",
            evaluation_created_at="2026-01-01T00:00:00+00:00",
            provenance=EvidenceProvenance("dataset-v1", "invoice-features-v1", "commit-123"),
        )
        for number, (evaluation_id, invoice_id) in enumerate(
            zip(evaluation_ids, ("INV-10023", "INV-10030"), strict=True), start=1
        )
    ]
    legacy_app.persist_evidence_records(db_path, records)
    return tuple(evaluation_ids)


def _evidence_record(evaluation_id: int, invoice_id: str, suffix: str) -> EvidenceRecord:
    return EvidenceRecord(
        evaluation_id=evaluation_id,
        invoice_id=invoice_id,
        correlation_id=f"corr-batch-{suffix}",
        model_name="invoice-review",
        model_version="7",
        run_id=f"run-batch-{suffix}",
        manual_review_probability="0.82",
        policy_version="ml-policy-v1",
        policy_threshold="0.8",
        recommendation="MANUAL_REVIEW",
        source="model",
        reason="probability_at_or_above_threshold",
        evaluation_created_at="2026-01-01T00:00:00+00:00",
        provenance=EvidenceProvenance("dataset-v1", "invoice-features-v1", "commit-123"),
    )


def authenticated_client(db_path: Path) -> TestClient:
    client = TestClient(create_app(db_path))
    client.post("/login", data={"username": "analyst", "password": "demo-password"})
    return client


def csrf_token_from(response: object) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def insert_invoices(db_path: Path, *, count: int) -> None:
    rows = [
        (
            f"INV-EXTRA-{number:03d}",
            "Bulk Vendor",
            100,
            1,
            1,
            InvoiceStatus.PENDING.value,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        )
        for number in range(count)
    ]
    with _connect(db_path) as connection:
        connection.executemany(
            """
            INSERT INTO invoices (
                invoice_id, vendor_name, invoice_amount_cents, has_purchase_order,
                three_way_match, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "invoiceops.db"
    seed_invoices(path)
    return path
