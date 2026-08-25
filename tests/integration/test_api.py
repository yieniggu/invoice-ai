import base64
import json
import re
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from invoiceops.domain.models import InvoiceStatus
from invoiceops.legacy import faults
from invoiceops.legacy.app import create_app
from invoiceops.legacy.db import _connect, get_invoice, list_decision_events
from invoiceops.legacy.seed import seed_invoices


def test_health_returns_ok(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_invoice_list_is_limited_to_100_and_reports_more_results(db_path: Path) -> None:
    insert_invoices(db_path, count=101)

    response = TestClient(create_app(db_path)).get("/api/invoices")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"invoices", "has_more"}
    assert len(payload["invoices"]) == 100
    assert payload["has_more"] is True


def test_invoice_list_filters_by_query_and_documents_parameter(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    response = client.get("/api/invoices?q=acME")
    openapi = client.get("/openapi.json")

    assert response.status_code == 200
    assert [invoice["invoice_id"] for invoice in response.json()["invoices"]] == ["INV-10023"]
    parameters = openapi.json()["paths"]["/api/invoices"]["get"]["parameters"]
    assert parameters == [
        {
            "name": "q",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "default": "", "title": "Q"},
        }
    ]


def test_get_invoice_returns_json_invoice(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).get("/api/invoices/INV-10023")

    assert response.status_code == 200
    assert response.json()["invoice_id"] == "INV-10023"


def test_get_invoice_returns_404_for_unknown_invoice(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).get("/api/invoices/UNKNOWN")

    assert response.status_code == 404
    assert response.json() == {"detail": "Invoice not found."}


@pytest.mark.parametrize(
    ("invoice_id", "decision", "status"),
    [
        ("INV-10023", "AUTO_PROCESS", "AUTO_PROCESSED"),
        ("INV-10024", "MANUAL_REVIEW", "MANUAL_REVIEW"),
    ],
)
def test_api_decision_updates_pending_invoice_and_audits_correlation_id(
    db_path: Path, invoice_id: str, decision: str, status: str
) -> None:
    correlation_id = "request-123"

    response = TestClient(create_app(db_path)).post(
        f"/api/v1/invoices/{invoice_id}/decision",
        headers={"X-Correlation-ID": correlation_id},
        json={"decision": decision},
    )

    assert response.status_code == 200
    assert response.json() == {
        "invoice_id": invoice_id,
        "status": status,
        "decision": decision,
        "rule_version": "invoice-rules-v1",
        "correlation_id": correlation_id,
    }
    assert response.headers["X-Correlation-ID"] == correlation_id
    assert get_invoice(db_path, invoice_id).status.value == status
    events = list_decision_events(db_path, invoice_id)
    assert len(events) == 1
    assert events[0]["decision"] == decision
    assert events[0]["actor"] == "api"
    assert events[0]["correlation_id"] == correlation_id


def test_api_decision_generates_and_returns_correlation_id(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).post(
        "/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"}
    )

    assert response.status_code == 200
    correlation_id = response.headers["X-Correlation-ID"]
    assert response.json()["correlation_id"] == correlation_id
    assert UUID(correlation_id)
    assert list_decision_events(db_path, "INV-10023")[0]["correlation_id"] == correlation_id


def test_secure_api_rejects_anonymous_decision_without_mutation(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = secure_client(db_path, monkeypatch)

    response = client.post(
        "/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"}
    )

    assert response.status_code == 401
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


def test_secure_api_returns_404_for_unregistered_nested_decision_path(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = secure_client(db_path, monkeypatch)

    response = client.post(
        "/api/v1/invoices/a/b/decision", json={"decision": "AUTO_PROCESS"}
    )

    assert response.status_code == 404


def test_secure_api_rejects_authenticated_unpermitted_principal_without_mutation(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = secure_client(db_path, monkeypatch)
    client.cookies.set(
        "session", signed_session_cookie({"username": "unpermitted-principal"})
    )

    response = client.post(
        "/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"}
    )

    assert response.status_code == 403
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


@pytest.mark.parametrize(
    "content",
    [b"{invalid-json", b'{"decision":"INVALID"}'],
)
def test_secure_api_rejects_anonymous_invalid_body_before_validation_or_faults(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    client = secure_client(db_path, monkeypatch)

    try:
        faults.state.decision_api_unavailable = True
        response = client.post(
            "/api/v1/invoices/INV-10023/decision",
            content=content,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 401
        assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
        assert list_decision_events(db_path, "INV-10023") == []
    finally:
        faults.reset_faults()


@pytest.mark.parametrize(
    "content",
    [b"{invalid-json", b'{"decision":"INVALID"}'],
)
def test_secure_api_rejects_unpermitted_invalid_body_before_validation_or_faults(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, content: bytes
) -> None:
    client = secure_client(db_path, monkeypatch)
    client.cookies.set(
        "session", signed_session_cookie({"username": "unpermitted-principal"})
    )

    try:
        faults.state.decision_api_unavailable = True
        response = client.post(
            "/api/v1/invoices/INV-10023/decision",
            content=content,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 403
        assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
        assert list_decision_events(db_path, "INV-10023") == []
    finally:
        faults.reset_faults()


def test_secure_api_accepts_authorized_signed_session_and_audits_actor(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = secure_client(db_path, monkeypatch)
    client.cookies.set("session", signed_session_cookie({"username": "secure-analyst"}))
    correlation_id = "secure-request-123"

    response = client.post(
        "/api/v1/invoices/INV-10023/decision",
        headers={"X-Correlation-ID": correlation_id},
        json={"decision": "AUTO_PROCESS"},
    )

    assert response.status_code == 200
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    events = list_decision_events(db_path, "INV-10023")
    assert len(events) == 1
    assert events[0]["actor"] == "secure-analyst"
    assert events[0]["correlation_id"] == correlation_id


def test_demo_mode_preserves_existing_behavior(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVOICEOPS_MODE", "demo")
    correlation_id = "demo-request-123"

    response = TestClient(create_app(db_path)).post(
        "/api/v1/invoices/INV-10023/decision",
        headers={"X-Correlation-ID": correlation_id},
        json={"decision": "AUTO_PROCESS"},
    )

    assert response.status_code == 200
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    events = list_decision_events(db_path, "INV-10023")
    assert len(events) == 1
    assert events[0]["actor"] == "api"
    assert events[0]["correlation_id"] == correlation_id


def test_repeated_api_decision_returns_409_without_audit_write(db_path: Path) -> None:
    client = TestClient(create_app(db_path))
    client.post("/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"})

    response = client.post("/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"})

    assert response.status_code == 409
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    assert len(list_decision_events(db_path, "INV-10023")) == 1


def test_api_decision_rejects_invalid_payload_without_writing(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).post(
        "/api/v1/invoices/INV-10023/decision", json={"decision": "INVALID"}
    )

    assert response.status_code == 422
    assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
    assert list_decision_events(db_path, "INV-10023") == []


def test_api_decision_returns_404_without_writing(db_path: Path) -> None:
    response = TestClient(create_app(db_path)).post(
        "/api/v1/invoices/UNKNOWN/decision", json={"decision": "AUTO_PROCESS"}
    )

    assert response.status_code == 404
    assert list_decision_events(db_path) == []


def test_unavailable_decision_api_returns_503_without_writing_and_ui_still_works(
    db_path: Path,
) -> None:
    client = TestClient(create_app(db_path))

    try:
        client.post("/login", data={"username": "analyst", "password": "demo-password"})
        controls = client.get("/admin/faults")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', controls.text)
        assert csrf_match is not None
        enabled = client.post(
            "/admin/faults",
            data={
                "fault": "decision_api_unavailable",
                "enabled": "true",
                "csrf_token": csrf_match.group(1),
            },
            follow_redirects=False,
        )
        response = client.post(
            "/api/v1/invoices/INV-10023/decision", json={"decision": "AUTO_PROCESS"}
        )

        assert enabled.status_code == 303
        assert response.status_code == 503
        assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.PENDING
        assert list_decision_events(db_path, "INV-10023") == []

        detail = client.get("/invoices/INV-10023")
        csrf_match = re.search(r'name="csrf_token" value="([^"]+)"', detail.text)
        assert csrf_match is not None
        response = client.post(
            "/invoices/INV-10023/decision",
            data={"decision": "AUTO_PROCESS", "csrf_token": csrf_match.group(1)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert get_invoice(db_path, "INV-10023").status is InvoiceStatus.AUTO_PROCESSED
    finally:
        faults.reset_faults()


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


def secure_client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("INVOICEOPS_MODE", "secure")
    monkeypatch.setenv("INVOICEOPS_DEMO_USERNAME", "secure-analyst")
    monkeypatch.setenv("INVOICEOPS_DEMO_PASSWORD", "secure-password")
    monkeypatch.setenv("INVOICEOPS_SESSION_SECRET", "secure-test-session-secret")
    monkeypatch.setenv("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS", "secure-analyst")
    return TestClient(create_app(db_path))


def signed_session_cookie(session: dict[str, str]) -> str:
    payload = base64.b64encode(json.dumps(session).encode("utf-8"))
    return TimestampSigner("secure-test-session-secret").sign(payload).decode("utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "invoiceops.db"
    seed_invoices(path)
    return path
