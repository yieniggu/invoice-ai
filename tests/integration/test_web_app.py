import json
import re
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from invoiceops.domain.models import InvoiceStatus
from invoiceops.domain.policy import recommend_from_probability
from invoiceops.legacy import faults
from invoiceops.legacy.app import create_app
from invoiceops.legacy.db import (
    _connect,
    get_invoice,
    insert_model_evaluation,
    list_decision_events,
)
from invoiceops.legacy.seed import seed_invoices


def test_login_required(db_path: Path) -> None:
    client = TestClient(create_app(db_path))

    for path in ("/invoices", "/invoices/INV-10023", "/admin/faults"):
        response = client.get(path, follow_redirects=False)
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
    assert "Teaching demo: eight model features" in response.text
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
    assert "portal does not call the Model API" in response.text

    other_demo_response = authenticated_client(db_path).get("/invoices/INV-10029")
    non_demo_response = authenticated_client(db_path).get("/invoices/INV-10023")

    assert "Teaching demo: eight model features" in other_demo_response.text
    assert "2200" in other_demo_response.text
    assert "Teaching demo: eight model features" not in non_demo_response.text


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
