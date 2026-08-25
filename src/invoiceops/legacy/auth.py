import os
import secrets
from dataclasses import dataclass

from fastapi import Request

DEMO_USERNAME = "analyst"
DEMO_PASSWORD = "demo-password"
SESSION_SECRET = "dev-only-change-me"


@dataclass(frozen=True)
class AuthSettings:
    username: str
    password: str
    session_secret: str
    secure_cookies: bool
    allowed_decision_principals: frozenset[str]


def auth_settings() -> AuthSettings:
    mode = os.environ.get("INVOICEOPS_MODE")
    if mode == "demo":
        return AuthSettings(
            username=os.environ.get("INVOICEOPS_DEMO_USERNAME", DEMO_USERNAME),
            password=os.environ.get("INVOICEOPS_DEMO_PASSWORD", DEMO_PASSWORD),
            session_secret=os.environ.get("INVOICEOPS_SESSION_SECRET", SESSION_SECRET),
            secure_cookies=False,
            allowed_decision_principals=frozenset(),
        )
    if mode != "secure":
        raise ValueError("INVOICEOPS_MODE must be explicitly set to 'demo' or 'secure'.")

    username = _required_environment_value("INVOICEOPS_DEMO_USERNAME")
    password = _required_environment_value("INVOICEOPS_DEMO_PASSWORD")
    session_secret = _required_environment_value("INVOICEOPS_SESSION_SECRET")
    allowed_decision_principals = _allowed_decision_principals()
    if session_secret == SESSION_SECRET:
        raise ValueError("INVOICEOPS_SESSION_SECRET must not use the demo secret in secure mode.")
    return AuthSettings(
        username=username,
        password=password,
        session_secret=session_secret,
        secure_cookies=True,
        allowed_decision_principals=allowed_decision_principals,
    )


def _required_environment_value(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ValueError(f"{name} must be set in secure mode.")
    return value


def _allowed_decision_principals() -> frozenset[str]:
    configured_principals = _required_environment_value("INVOICEOPS_ALLOWED_DECISION_PRINCIPALS")
    principals = [principal.strip() for principal in configured_principals.split(",")]
    if not all(principals):
        raise ValueError(
            "INVOICEOPS_ALLOWED_DECISION_PRINCIPALS must be a comma-separated list of principals."
        )
    return frozenset(principals)


def credentials_are_valid(username: str, password: str, settings: AuthSettings) -> bool:
    return secrets.compare_digest(
        username.encode(), settings.username.encode()
    ) and secrets.compare_digest(password.encode(), settings.password.encode())


def is_authenticated(request: Request) -> bool:
    return request.session.get("username") is not None


def session_principal(request: Request) -> str | None:
    principal = request.session.get("username")
    return principal if isinstance(principal, str) and principal else None


def create_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_token_is_valid(request: Request, submitted_token: str | None) -> bool:
    expected_token = request.session.get("csrf_token")
    return (
        isinstance(expected_token, str)
        and isinstance(submitted_token, str)
        and secrets.compare_digest(submitted_token, expected_token)
    )
