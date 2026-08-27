from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from invoiceops.domain.models import Decision
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
    get_invoice,
    init_db,
    list_decision_events,
    list_invoices,
    list_model_evaluations,
    update_invoice_decision,
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


class ApiDecisionRequest(BaseModel):
    decision: Decision


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = auth_settings()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    init_db(db_path)

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
        if (protected_path or request.url.path.startswith("/admin/")) and not is_authenticated(
            request
        ):
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
        invoice = get_invoice(db_path, invoice_id)
        if invoice is None:
            return templates.TemplateResponse(
                request=request,
                name="invoice_detail.html",
                context={"invoice": None, "events": [], "error": "Invoice not found."},
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request,
            name="invoice_detail.html",
            context={
                "invoice": invoice,
                "events": reversed(list_decision_events(db_path, invoice_id)),
                "model_evaluations": list_model_evaluations(db_path, invoice_id),
                "faults": faults.state,
                "is_teaching_demo_invoice": invoice_id in {"INV-10029", "INV-10030"},
            },
        )

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

    return app


def require_valid_csrf_token(request: Request, csrf_token: str | None) -> None:
    if not csrf_token_is_valid(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token.")


app = create_app()
