import argparse
import math
import re
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright

from invoiceops.domain.models import Decision, Invoice, InvoiceStatus
from invoiceops.domain.rules import decide_invoice
from invoiceops.legacy.auth import auth_settings

_UI_CURRENCY_PATTERN = re.compile(r"\$(\d+)\.(\d{2})")
_FAILURE_SCREENSHOT = Path("artifacts/playwright_failure.png")


def _non_negative_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a non-negative number") from error
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be a non-negative number")
    return seconds


def parse_currency_to_cents(value: str) -> int:
    match = _UI_CURRENCY_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("Currency must use the UI format $<dollars>.<cents>.")

    dollars, cents = match.groups()
    return int(dollars) * 100 + int(cents)


def open_invoice_via_ui(
    page: Page,
    *,
    base_url: str,
    invoice_id: str,
    username: str,
    password: str,
    step_delay_milliseconds: int = 0,
) -> None:
    page.goto(f"{base_url}/login")
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    page.get_by_role("textbox", name="Username").fill(username)
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    page.get_by_role("textbox", name="Password").fill(password)
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    page.get_by_role("button", name="Sign in").click()
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    page.get_by_role("textbox", name="Search ID or vendor").fill(invoice_id)
    page.get_by_role("button", name="Search").click()
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    page.get_by_role("link", name=invoice_id).click()
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)


def prepare_invoice_decision_via_ui(
    page: Page, *, locator: str, step_delay_milliseconds: int = 0
) -> tuple[Decision, Locator]:
    invoice_amount_cents = parse_currency_to_cents(
        page.get_by_test_id("invoice-amount").inner_text()
    )
    has_purchase_order = page.get_by_test_id("invoice-has-po").inner_text() == "Yes"
    three_way_match = page.get_by_test_id("invoice-three-way-match").inner_text() == "Yes"
    timestamp = datetime.now(UTC)
    decision = decide_invoice(
        Invoice(
            invoice_id="",
            vendor_name="",
            invoice_amount_cents=invoice_amount_cents,
            has_purchase_order=has_purchase_order,
            three_way_match=three_way_match,
            status=InvoiceStatus.PENDING,
            created_at=timestamp,
            updated_at=timestamp,
        )
    )

    if locator == "role":
        control = page.get_by_role(
            "button", name="Process" if decision is Decision.AUTO_PROCESS else "Manual Review"
        )
    else:
        control = page.get_by_test_id(
            "invoice-process" if decision is Decision.AUTO_PROCESS else "invoice-manual-review"
        )
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    return decision, control


def complete_invoice_decision_via_ui(
    page: Page, *, decision: Decision, control: Locator, step_delay_milliseconds: int = 0
) -> dict[str, str]:
    control.click()
    if step_delay_milliseconds:
        page.wait_for_timeout(step_delay_milliseconds)
    status = page.get_by_test_id("invoice-status").inner_text()
    expected_status = (
        InvoiceStatus.AUTO_PROCESSED.value
        if decision is Decision.AUTO_PROCESS
        else InvoiceStatus.MANUAL_REVIEW.value
    )
    if status != expected_status:
        raise ValueError("Final invoice status does not match selected decision.")
    return {"decision": decision.value, "status": status}


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--invoice-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--locator", choices=("role", "testid"), default="role")
    parser.add_argument("--step-delay-seconds", type=_non_negative_seconds, default=0)
    return parser.parse_args(arguments)


def run_bot(
    *,
    base_url: str,
    invoice_id: str,
    locator: str,
    headed: bool,
    step_delay_seconds: float,
    username: str,
    password: str,
) -> dict[str, str]:
    step_delay_milliseconds = max(1, int(step_delay_seconds * 1_000)) if step_delay_seconds else 0
    with sync_playwright() as playwright:
        browser = None
        page = None
        try:
            browser = playwright.chromium.launch(headless=not headed)
            page = browser.new_page()
            open_invoice_via_ui(
                page,
                base_url=base_url,
                invoice_id=invoice_id,
                username=username,
                password=password,
                step_delay_milliseconds=step_delay_milliseconds,
            )
            decision, control = prepare_invoice_decision_via_ui(
                page, locator=locator, step_delay_milliseconds=step_delay_milliseconds
            )
            return complete_invoice_decision_via_ui(
                page,
                decision=decision,
                control=control,
                step_delay_milliseconds=step_delay_milliseconds,
            )
        except PlaywrightError as error:
            if page is not None:
                try:
                    _FAILURE_SCREENSHOT.parent.mkdir(exist_ok=True)
                    page.screenshot(path=str(_FAILURE_SCREENSHOT))
                except PlaywrightError:
                    print(f"Playwright failed; screenshot capture failed: {error}")
                else:
                    print(
                        "Playwright failed; screenshot saved to artifacts/playwright_failure.png: "
                        f"{error}"
                    )
            else:
                print(
                    "Playwright failed; screenshot could not be captured because no page was created: "
                    f"{error}"
                )
            raise
        finally:
            if browser is not None:
                browser.close()


def main(arguments: list[str] | None = None) -> int:
    arguments = parse_arguments(arguments)
    settings = auth_settings()
    try:
        result = run_bot(
            base_url=arguments.base_url,
            invoice_id=arguments.invoice_id,
            locator=arguments.locator,
            headed=arguments.headed,
            step_delay_seconds=arguments.step_delay_seconds,
            username=settings.username,
            password=settings.password,
        )
    except PlaywrightError:
        return 1

    print(f"{arguments.invoice_id}: {result['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
