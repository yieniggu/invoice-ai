from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.sessions import SessionMiddleware

from invoiceops.domain.models import Decision
from invoiceops.legacy.auth import (
    auth_settings,
    create_csrf_token,
    credentials_are_valid,
    csrf_token_is_valid,
    is_authenticated,
)
from invoiceops.legacy.db import (
    InvalidInvoiceTransition,
    get_invoice,
    init_db,
    list_decision_events,
    list_invoices,
    update_invoice_decision,
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def create_app(db_path: str | Path | None = None) -> FastAPI:
    settings = auth_settings()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    init_db(db_path)

    @app.middleware("http")
    async def require_login(request: Request, call_next: RequestResponseEndpoint) -> Response:
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
    def logout(request: Request, csrf_token: Annotated[str | None, Form()] = None) -> RedirectResponse:
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
            context={"invoice": invoice, "events": reversed(list_decision_events(db_path, invoice_id))},
        )

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
