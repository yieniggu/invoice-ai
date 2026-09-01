import json
import os
import secrets
import time
from pathlib import Path
from typing import Annotated
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen
from uuid import uuid4

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from invoiceops.anchor import (
    LOCAL_CHAIN_ID,
    AnchorError,
    anchor_evidence_batch,
    chain,
    is_root_registered,
    local_signer,
    resolve_deployment,
)
from invoiceops.domain.models import Decision
from invoiceops.domain.policy import recommend_from_probability
from invoiceops.evidence import (
    EvidenceBatch,
    EvidenceError,
    build_evidence_record,
    create_evidence_batch,
    create_evidence_batch_successor,
    get_evidence_batch,
    list_evidence_records,
    merkle_tree,
    persist_evidence_records,
    verify_persisted_evidence_record,
)
from invoiceops.legacy import faults
from invoiceops.legacy.auth import (
    auth_settings,
    create_csrf_token,
    credentials_are_valid,
    csrf_token_is_valid,
    is_authenticated,
    session_principal,
)
from invoiceops.legacy.db import (
    InvalidInvoiceTransition,
    _resolve_db_path,
    get_evidence_batch_predecessor,
    get_evidence_context,
    get_invoice,
    get_latest_evidence_batch_anchor,
    get_or_insert_model_evaluation,
    get_persisted_canonical_payload,
    init_db,
    list_decision_events,
    list_evidence_batch_successors,
    list_evidence_batches,
    list_evidence_batches_for_invoice,
    list_evidence_record_batch_memberships,
    list_invoices,
    list_model_evaluations,
    update_invoice_decision,
)
from invoiceops.ml.features import MODEL_FEATURES, invoice_to_features
from invoiceops.model_api.schemas import PredictionResponse
from invoiceops.verification import verify_evidence_batch

BASE_DIR = Path(__file__).parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=BASE_DIR / "templates")
ANCHOR_CHALLENGE_TTL_SECONDS = 300


def load_local_env(path: Path = PROJECT_ROOT / ".env") -> None:
    """Load simple local environment assignments without replacing process configuration."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.isascii() or not key.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_local_env()


class ApiDecisionRequest(BaseModel):
    decision: Decision


class ModelApiUnavailableError(RuntimeError):
    """Raised when a complete prediction cannot be obtained from the Model API."""


def request_model_prediction(features: dict[str, object]) -> PredictionResponse:
    base_url = os.getenv("INVOICEOPS_MODEL_API_URL", "").rstrip("/")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ModelApiUnavailableError("Model API is not configured.")
    try:
        timeout = float(os.getenv("INVOICEOPS_MODEL_API_TIMEOUT_SECONDS", "5"))
    except ValueError as error:
        raise ModelApiUnavailableError("Model API timeout configuration is invalid.") from error
    if not 0 < timeout <= 60:
        raise ModelApiUnavailableError("Model API timeout configuration is invalid.")
    request = UrlRequest(
        f"{base_url}/predict",
        data=json.dumps(features).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise ModelApiUnavailableError("Model API did not return a prediction.")
            payload = json.loads(response.read())
        return PredictionResponse.model_validate(payload)
    except (
        HTTPError,
        URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
    ) as error:
        raise ModelApiUnavailableError("Model API is unavailable or returned an invalid response.") from error


def transaction_url(transaction_hash: str | None) -> str | None:
    template = os.getenv("INVOICEOPS_EVM_EXPLORER_TX_URL_TEMPLATE", "")
    if not transaction_hash or "{tx_hash}" not in template:
        return None
    candidate = template.replace("{tx_hash}", transaction_hash)
    parsed_url = urlparse(candidate)
    return candidate if parsed_url.scheme in {"http", "https"} and parsed_url.netloc else None


def local_anchor_preflight(db_path: str | Path | None, batch: EvidenceBatch) -> dict[str, object]:
    """Validate the fixed local deployment before a confirmation can be issued."""
    if batch.status != "verified":
        raise AnchorError("Only a verified batch can be anchored.")
    deployment = resolve_deployment()
    if deployment.chain_id != LOCAL_CHAIN_ID:
        raise AnchorError(f"Local anchor manifest must use chain ID {LOCAL_CHAIN_ID}.")
    if deployment.signer is None:
        raise AnchorError("Local anchor manifest must contain the deployed signer.")
    web3 = chain(expected_chain_id=LOCAL_CHAIN_ID)
    signer = local_signer(web3)
    if signer.lower() != deployment.signer.lower():
        raise AnchorError("Local Anvil signer does not match the deployment manifest.")
    if is_root_registered(web3, deployment.address, batch.root_hash):
        raise AnchorError("Root is already registered; reconcile the existing anchor instead of resubmitting.")
    return {
        "web3": web3,
        "deployment": deployment,
        "signer": signer,
        "chain_id": LOCAL_CHAIN_ID,
        "contract_address": deployment.address,
    }


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = auth_settings()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    init_db(db_path)

    def detail_context(request: Request, invoice_id: str, *, error: str | None = None) -> dict[str, object]:
        invoice = get_invoice(db_path, invoice_id)
        if invoice is None:
            return {"invoice": None, "events": [], "error": "Invoice not found."}
        evaluations = list_model_evaluations(db_path, invoice_id)
        latest_evaluation = evaluations[0] if evaluations else None
        evidence_context = (
            get_evidence_context(db_path, latest_evaluation["id"])
            if latest_evaluation is not None
            else None
        )
        if evidence_context is not None:
            evidence_context = dict(evidence_context)
            evidence_context["transaction_url"] = transaction_url(evidence_context["transaction_hash"])
            evidence_context["verified"] = verify_persisted_evidence_record(
                db_path, latest_evaluation["id"]
            )
        return {
            "invoice": invoice,
            "events": reversed(list_decision_events(db_path, invoice_id)),
            "model_features": invoice_to_features(invoice),
            "model_feature_names": MODEL_FEATURES,
            "model_evaluations": evaluations,
            "latest_evaluation": latest_evaluation,
            "evidence_context": evidence_context,
            "faults": faults.state,
            "error": error,
            "batch_history": list_evidence_batches_for_invoice(db_path, invoice_id),
        }

    def batch_context(request: Request, batch_id: int, *, error: str | None = None) -> dict[str, object]:
        try:
            batch = get_evidence_batch(db_path, batch_id)
        except EvidenceError:
            return {"batch": None, "error": "Evidence batch not found."}
        anchor = get_latest_evidence_batch_anchor(db_path, batch.id)
        checks = [
            verify_evidence_batch(_resolve_db_path(db_path), batch.id, item.evaluation_id).to_dict()
            for item in batch.items
        ]
        records_by_evaluation = {
            record.evaluation_id: record for record in list_evidence_records(db_path)
        }
        tree = merkle_tree([item.leaf_hash for item in batch.items], sort_leaves=False)
        merkle_levels = []
        for level_index in range(len(tree.levels) - 1, -1, -1):
            level = tree.levels[level_index]
            children = tree.levels[level_index - 1].hashes if level_index else []
            merkle_levels.append(
                {
                    "label": "Merkle root" if level_index == len(tree.levels) - 1 else (
                        "Leaves: Evidence Records" if level_index == 0 else f"Hash level {level_index}"
                    ),
                    "is_root": level_index == len(tree.levels) - 1,
                    "is_leaf_level": level_index == 0,
                    "nodes": [
                        {
                            "hash": node_hash,
                            "left_hash": children[node_index * 2] if children else None,
                            "right_hash": (
                                children[node_index * 2 + 1]
                                if children and node_index * 2 + 1 < len(children)
                                else children[-1] if children else None
                            ),
                            "duplicates_right": bool(
                                children
                                and node_index * 2 + 1 >= len(children)
                            ),
                            "item": batch.items[node_index] if level_index == 0 else None,
                        }
                        for node_index, node_hash in enumerate(level.hashes)
                    ],
                }
            )
        evidence_sources = [
            {
                "record": records_by_evaluation[item.evaluation_id],
                "canonical_payload": get_persisted_canonical_payload(db_path, item.evaluation_id),
            }
            for item in batch.items
        ]
        return {
            "batch": batch,
            "anchor": anchor,
            "checks": checks,
            "records_by_evaluation": records_by_evaluation,
            "merkle_levels": merkle_levels,
            "root_matches_persisted": tree.root == batch.root_hash,
            "evidence_sources": evidence_sources,
            "predecessor": get_evidence_batch_predecessor(db_path, batch.id),
            "successors": list_evidence_batch_successors(db_path, batch.id),
            "error": error,
        }

    def verified_evidence_records() -> list[object]:
        records = []
        for record in list_evidence_records(db_path):
            try:
                if verify_persisted_evidence_record(db_path, record.evaluation_id):
                    records.append(record)
            except EvidenceError:
                continue
        return records

    def evidence_batches_context(
        request: Request, *, source_batch_id: int | None = None, error: str | None = None
    ) -> dict[str, object]:
        source_batch = None
        if source_batch_id is not None:
            try:
                source_batch = get_evidence_batch(db_path, source_batch_id)
            except EvidenceError:
                error = "Source evidence batch not found."
        memberships: dict[int, list[int]] = {}
        for membership in list_evidence_record_batch_memberships(db_path):
            memberships.setdefault(membership["evaluation_id"], []).append(membership["batch_id"])
        records = verified_evidence_records()
        return {
            "batches": list_evidence_batches(db_path),
            "records": [record for record in records if record.evaluation_id not in memberships],
            "batched_records": [
                {"record": record, "batch_ids": memberships[record.evaluation_id]}
                for record in records
                if record.evaluation_id in memberships
            ],
            "source_batch": source_batch,
            "error": error,
        }

    def api_decision_actor(request: Request) -> str:
        if not settings.secure_cookies:
            return "api"
        actor = session_principal(request)
        if actor is None:
            raise HTTPException(status_code=401, detail="Authentication is required.")
        if actor not in settings.allowed_decision_principals:
            raise HTTPException(
                status_code=403, detail="Principal is not authorized to decide invoices."
            )
        return actor

    @app.middleware("http")
    async def require_login(request: Request, call_next: RequestResponseEndpoint) -> Response:
        path_segments = request.url.path.split("/")
        is_api_decision = request.method == "POST" and (
            len(path_segments) == 6
            and path_segments[:4] == ["", "api", "v1", "invoices"]
            and bool(path_segments[4])
            and path_segments[5] == "decision"
        )
        if is_api_decision:
            try:
                request.state.api_decision_actor = api_decision_actor(request)
            except HTTPException as exception:
                return JSONResponse(
                    status_code=exception.status_code, content={"detail": exception.detail}
                )
        protected_path = request.url.path == "/invoices" or request.url.path.startswith(
            "/invoices/"
        )
        if (
            protected_path
            or request.url.path.startswith("/admin/")
            or request.url.path.startswith("/evidence/")
        ) and not is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        https_only=settings.secure_cookies,
        same_site="lax",
    )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/invoices")
    def api_invoice_list(q: str = "") -> dict[str, object]:
        result = list_invoices(db_path, query=q)
        return {"invoices": result.invoices, "has_more": result.has_more}

    @app.get("/api/invoices/{invoice_id}")
    def api_invoice_detail(invoice_id: str) -> object:
        invoice = get_invoice(db_path, invoice_id)
        if invoice is None:
            raise HTTPException(status_code=404, detail="Invoice not found.")
        return invoice

    @app.post("/api/v1/invoices/{invoice_id}/decision")
    def api_decide_invoice(
        invoice_id: str,
        payload: ApiDecisionRequest,
        response: Response,
        request: Request,
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> dict[str, str]:
        actor = request.state.api_decision_actor
        if faults.state.decision_api_unavailable:
            raise HTTPException(status_code=503, detail="Decision API is unavailable.")
        correlation_id = str(uuid4()) if correlation_id is None else correlation_id
        try:
            invoice = update_invoice_decision(
                db_path,
                invoice_id,
                payload.decision,
                actor=actor,
                correlation_id=correlation_id,
            )
        except LookupError:
            raise HTTPException(status_code=404, detail="Invoice not found.") from None
        except InvalidInvoiceTransition:
            raise HTTPException(
                status_code=409, detail="Invoice cannot be decided from its current state."
            ) from None

        response.headers["X-Correlation-ID"] = correlation_id
        return {
            "invoice_id": invoice.invoice_id,
            "status": invoice.status.value,
            "decision": payload.decision.value,
            "rule_version": "invoice-rules-v1",
            "correlation_id": correlation_id,
        }

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="login.html")

    @app.post("/login", response_class=HTMLResponse)
    def login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        if not credentials_are_valid(username, password, settings):
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={"error": "Invalid demo credentials."},
                status_code=401,
            )
        request.session.clear()
        request.session["username"] = username
        request.session["csrf_token"] = create_csrf_token()
        return RedirectResponse("/invoices", status_code=303)

    @app.post("/logout")
    def logout(
        request: Request, csrf_token: Annotated[str | None, Form()] = None
    ) -> RedirectResponse:
        require_valid_csrf_token(request, csrf_token)
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/invoices", response_class=HTMLResponse)
    def invoice_list(request: Request, q: str = "") -> HTMLResponse:
        result = list_invoices(db_path, query=q)
        return templates.TemplateResponse(
            request=request,
            name="invoices.html",
            context={"invoices": result.invoices, "q": q, "has_more": result.has_more},
        )

    @app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
    def invoice_detail(request: Request, invoice_id: str) -> HTMLResponse:
        faults.apply_portal_latency()
        context = detail_context(request, invoice_id)
        if context["invoice"] is None:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context=context,
                status_code=404,
            )
        return templates.TemplateResponse(request=request, name="invoice_detail.html", context=context)

    @app.get("/evidence/batches", response_class=HTMLResponse)
    def evidence_batches(request: Request, source_batch_id: int | None = None) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="evidence_batches.html",
            context=evidence_batches_context(request, source_batch_id=source_batch_id),
        )

    @app.post("/evidence/batches", response_class=HTMLResponse)
    def create_batch(
        request: Request,
        evaluation_id: Annotated[list[int], Form()],
        source_batch_id: Annotated[int | None, Form()] = None,
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        minimum_records = 1 if source_batch_id is not None else 2
        if len(evaluation_id) < minimum_records:
            return templates.TemplateResponse(
                request=request,
                name="evidence_batches.html",
                context=evidence_batches_context(
                    request,
                    source_batch_id=source_batch_id,
                    error=(
                        "Select at least one new verified evidence record."
                        if source_batch_id is not None
                        else "Select at least two verified evidence records."
                    ),
                ),
                status_code=422,
            )
        try:
            batch = (
                create_evidence_batch_successor(db_path, source_batch_id, evaluation_id)
                if source_batch_id is not None
                else create_evidence_batch(db_path, evaluation_id)
            )
        except EvidenceError as error:
            return templates.TemplateResponse(
                request=request,
                name="evidence_batches.html",
                context=evidence_batches_context(
                    request, source_batch_id=source_batch_id, error=str(error)
                ),
                status_code=422,
            )
        return RedirectResponse(f"/evidence/batches/{batch.id}", status_code=303)

    @app.get("/evidence/batches/{batch_id}", response_class=HTMLResponse)
    def evidence_batch_detail(request: Request, batch_id: int) -> HTMLResponse:
        context = batch_context(request, batch_id)
        return templates.TemplateResponse(
            request=request,
            name="evidence_batch_detail.html",
            context=context,
            status_code=404 if context["batch"] is None else 200,
        )

    @app.post("/evidence/batches/{batch_id}/anchor/request", response_class=HTMLResponse)
    def request_local_anchor(
        request: Request, batch_id: int, csrf_token: Annotated[str | None, Form()] = None
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        context = batch_context(request, batch_id)
        batch = context["batch"]
        if batch is None:
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=404)
        if context["anchor"] is not None:
            context["error"] = "This batch already has an anchor lifecycle; use its recorded status for recovery."
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=409)
        try:
            preflight = local_anchor_preflight(db_path, batch)
        except (AnchorError, ValueError) as error:
            context["error"] = f"Local Anvil preflight failed: {error}"
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=503)
        token = secrets.token_urlsafe(32)
        request.session["local_anchor_challenge"] = {
            "token": token,
            "username": session_principal(request),
            "batch_id": batch.id,
            "root_hash": batch.root_hash,
            "expires_at": time.time() + ANCHOR_CHALLENGE_TTL_SECONDS,
        }
        return templates.TemplateResponse(
            request=request,
            name="anchor_confirm.html",
            context={"batch": batch, "challenge_token": token, "preflight": preflight},
        )

    @app.post("/evidence/batches/{batch_id}/anchor/confirm")
    def confirm_local_anchor(
        request: Request,
        batch_id: int,
        challenge_token: Annotated[str, Form()],
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        challenge = request.session.pop("local_anchor_challenge", None)
        if not isinstance(challenge, dict) or (
            challenge.get("token") != challenge_token
            or challenge.get("username") != session_principal(request)
            or challenge.get("batch_id") != batch_id
            or not isinstance(challenge.get("expires_at"), float)
            or challenge["expires_at"] < time.time()
        ):
            context = batch_context(request, batch_id, error="Anchor confirmation is invalid or expired. Request a new preflight.")
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=409)
        context = batch_context(request, batch_id)
        batch = context["batch"]
        if batch is None or batch.root_hash != challenge.get("root_hash"):
            context["error"] = "Batch root changed or no longer exists. Request a new preflight."
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=409)
        if context["anchor"] is not None:
            context["error"] = "This batch already has an anchor lifecycle; no transaction was sent."
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=409)
        try:
            preflight = local_anchor_preflight(db_path, batch)
            anchor_evidence_batch(
                db_path,
                batch_id=batch.id,
                root_hash=batch.root_hash,
                web3=preflight["web3"],
                deployment=preflight["deployment"],
                signer=preflight["signer"],
            )
        except (AnchorError, ValueError) as error:
            context["error"] = f"Local anchor was not submitted: {error}"
            return templates.TemplateResponse(request=request, name="evidence_batch_detail.html", context=context, status_code=503)
        return RedirectResponse(f"/evidence/batches/{batch_id}", status_code=303)

    @app.get("/admin/faults", response_class=HTMLResponse)
    def fault_controls(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="faults.html",
            context={
                "faults": faults.state,
                "latency_options": sorted(faults.PORTAL_LATENCY_OPTIONS_MS),
            },
        )

    @app.post("/admin/faults")
    def update_fault_controls(
        request: Request,
        fault: Annotated[str, Form()],
        csrf_token: Annotated[str | None, Form()] = None,
        enabled: Annotated[str | None, Form()] = None,
        latency_ms: Annotated[int | None, Form()] = None,
    ) -> RedirectResponse:
        require_valid_csrf_token(request, csrf_token)
        if fault == "change_process_button_label":
            if enabled not in {"true", "false"}:
                raise HTTPException(status_code=422, detail="Invalid button label fault value.")
            faults.state.change_process_button_label = enabled == "true"
        elif fault == "portal_latency_ms":
            if latency_ms not in faults.PORTAL_LATENCY_OPTIONS_MS:
                raise HTTPException(status_code=422, detail="Invalid portal latency.")
            faults.state.portal_latency_ms = latency_ms
        elif fault == "decision_api_unavailable":
            if enabled not in {"true", "false"}:
                raise HTTPException(status_code=422, detail="Invalid Decision API fault value.")
            faults.state.decision_api_unavailable = enabled == "true"
        else:
            raise HTTPException(status_code=422, detail="Unknown fault.")
        return RedirectResponse("/admin/faults", status_code=303)

    @app.post("/admin/faults/reset")
    def reset_fault_controls(
        request: Request, csrf_token: Annotated[str | None, Form()] = None
    ) -> RedirectResponse:
        require_valid_csrf_token(request, csrf_token)
        faults.reset_faults()
        return RedirectResponse("/admin/faults", status_code=303)

    @app.post("/invoices/{invoice_id}/decision")
    def decide_invoice(
        request: Request,
        invoice_id: str,
        decision: Annotated[Decision, Form()],
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        try:
            update_invoice_decision(
                db_path,
                invoice_id,
                decision,
                actor="ui",
                correlation_id=str(uuid4()),
            )
        except LookupError:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context={"invoice": None, "events": [], "error": "Invoice not found."},
                status_code=404,
            )
        except InvalidInvoiceTransition:
            invoice = get_invoice(db_path, invoice_id)
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context={
                    "invoice": invoice,
                    "events": reversed(list_decision_events(db_path, invoice_id)),
                    "model_evaluations": list_model_evaluations(db_path, invoice_id),
                    "error": "Invoice cannot be decided from its current status.",
                },
                status_code=409,
            )
        return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)

    @app.post("/invoices/{invoice_id}/evaluate")
    def evaluate_invoice(
        request: Request,
        invoice_id: str,
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        invoice = get_invoice(db_path, invoice_id)
        if invoice is None:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context=detail_context(request, invoice_id),
                status_code=404,
            )
        correlation_id = f"portal:{request.session['csrf_token']}:{invoice_id}"
        if any(evaluation["correlation_id"] == correlation_id for evaluation in list_model_evaluations(db_path, invoice_id)):
            return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)
        try:
            prediction = request_model_prediction(invoice_to_features(invoice))
            get_or_insert_model_evaluation(
                db_path,
                invoice_id,
                correlation_id=correlation_id,
                recommendation=recommend_from_probability(prediction.manual_review_probability),
                model_name=prediction.model_name,
                model_version=prediction.model_version,
                run_id=prediction.run_id,
                manual_review_probability=prediction.manual_review_probability,
            )
        except (ModelApiUnavailableError, ValueError) as error:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context=detail_context(request, invoice_id, error=str(error)),
                status_code=503,
            )
        return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)

    @app.post("/invoices/{invoice_id}/evidence")
    def persist_invoice_evidence(
        request: Request,
        invoice_id: str,
        csrf_token: Annotated[str | None, Form()] = None,
    ) -> Response:
        require_valid_csrf_token(request, csrf_token)
        evaluations = list_model_evaluations(db_path, invoice_id)
        if not evaluations:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context=detail_context(request, invoice_id, error="No model evaluation is available for evidence."),
                status_code=409,
            )
        evaluation = evaluations[0]
        if get_evidence_context(db_path, evaluation["id"]) is not None:
            return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)
        try:
            persist_evidence_records(db_path, [build_evidence_record(db_path, evaluation["id"])])
        except (EvidenceError, OSError) as error:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context=detail_context(request, invoice_id, error=f"Evidence could not be saved: {error}"),
                status_code=503,
            )
        return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)

    return app


def require_valid_csrf_token(request: Request, csrf_token: str | None) -> None:
    if not csrf_token_is_valid(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


app = create_app()
