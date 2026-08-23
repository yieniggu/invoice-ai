import inspect

import pytest
from playwright.sync_api import Error as PlaywrightError

from invoiceops.automation import bot
from invoiceops.automation.bot import (
    complete_invoice_decision_via_ui,
    main,
    open_invoice_via_ui,
    parse_arguments,
    parse_currency_to_cents,
    prepare_invoice_decision_via_ui,
    run_bot,
)
from invoiceops.domain.models import Decision
from invoiceops.legacy.faults import FaultState


class FakeLocator:
    def __init__(
        self,
        calls: list[tuple[object, ...]],
        selector: tuple[object, ...],
        text: str = "",
    ) -> None:
        self.calls = calls
        self.selector = selector
        self.text = text

    def fill(self, value: str) -> None:
        self.calls.append(("fill", *self.selector, value))

    def click(self) -> None:
        self.calls.append(("click", *self.selector))

    def inner_text(self) -> str:
        self.calls.append(("inner_text", *self.selector))
        return self.text


class FakePage:
    def __init__(self, test_id_text: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.test_id_text = test_id_text or {}

    def goto(self, url: str) -> None:
        self.calls.append(("goto", url))

    def get_by_role(self, role: str, *, name: str) -> FakeLocator:
        selector = (role, name)
        self.calls.append(("get_by_role", *selector))
        return FakeLocator(self.calls, selector)

    def get_by_test_id(self, test_id: str) -> FakeLocator:
        self.calls.append(("get_by_test_id", test_id))
        return FakeLocator(self.calls, (test_id,), self.test_id_text.get(test_id, ""))

    def screenshot(self, *, path: str) -> None:
        self.calls.append(("screenshot", path))

    def wait_for_timeout(self, timeout: float) -> None:
        self.calls.append(("wait_for_timeout", timeout))


class FakeBrowser:
    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.calls: list[tuple[str, object]] = []

    def new_page(self) -> FakePage:
        self.calls.append(("new_page", None))
        return self.page

    def close(self) -> None:
        self.calls.append(("close", None))


class FakePlaywright:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launches: list[bool] = []
        self.chromium = self

    def launch(self, *, headless: bool) -> FakeBrowser:
        self.launches.append(headless)
        return self.browser


class FakePlaywrightContext:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    def __enter__(self) -> FakePlaywright:
        return self.playwright

    def __exit__(self, *_: object) -> None:
        return None


class FailingPage(FakePage):
    def goto(self, url: str) -> None:
        super().goto(url)
        raise PlaywrightError("navigation failed")


class ScreenshotFailingPage(FailingPage):
    def screenshot(self, *, path: str) -> None:
        super().screenshot(path=path)
        raise PlaywrightError("screenshot failed")


class LaunchFailingPlaywright:
    def __init__(self) -> None:
        self.chromium = self
        self.launches: list[bool] = []

    def launch(self, *, headless: bool) -> FakeBrowser:
        self.launches.append(headless)
        raise PlaywrightError("launch failed")


def test_open_invoice_via_ui_authenticates_searches_and_opens_detail() -> None:
    page = FakePage()

    open_invoice_via_ui(
        page,
        base_url="http://127.0.0.1:8000",
        invoice_id="INV-10023",
        username="analyst",
        password="demo-password",
    )

    assert page.calls == [
        ("goto", "http://127.0.0.1:8000/login"),
        ("get_by_role", "textbox", "Username"),
        ("fill", "textbox", "Username", "analyst"),
        ("get_by_role", "textbox", "Password"),
        ("fill", "textbox", "Password", "demo-password"),
        ("get_by_role", "button", "Sign in"),
        ("click", "button", "Sign in"),
        ("get_by_role", "textbox", "Search ID or vendor"),
        ("fill", "textbox", "Search ID or vendor", "INV-10023"),
        ("get_by_role", "button", "Search"),
        ("click", "button", "Search"),
        ("get_by_role", "link", "INV-10023"),
        ("click", "link", "INV-10023"),
    ]


def test_parse_currency_to_cents_converts_current_ui_format() -> None:
    assert parse_currency_to_cents("$4820.00") == 482_000


@pytest.mark.parametrize("value", ["4820.00", "$4820", "$48.2", "$48.200"])
def test_parse_currency_to_cents_rejects_values_outside_ui_format(value: str) -> None:
    with pytest.raises(ValueError):
        parse_currency_to_cents(value)


def test_parse_arguments_accepts_the_ticket_04_cli_contract() -> None:
    arguments = parse_arguments(
        [
            "--invoice-id",
            "INV-10023",
            "--base-url",
            "http://localhost:9000",
            "--headed",
            "--locator",
            "testid",
        ]
    )

    assert arguments.invoice_id == "INV-10023"
    assert arguments.base_url == "http://localhost:9000"
    assert arguments.headed is True
    assert arguments.locator == "testid"


def test_parse_arguments_uses_ticket_04_defaults() -> None:
    arguments = parse_arguments(["--invoice-id", "INV-10023"])

    assert arguments.invoice_id == "INV-10023"
    assert arguments.base_url == "http://127.0.0.1:8000"
    assert arguments.headed is False
    assert arguments.locator == "role"
    assert arguments.step_delay_seconds == 0


def test_parse_arguments_accepts_non_negative_step_delay_seconds() -> None:
    arguments = parse_arguments(["--invoice-id", "INV-10023", "--step-delay-seconds", "1.25"])

    assert arguments.step_delay_seconds == 1.25


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--invoice-id", "INV-10023", "--locator", "css"],
        ["--invoice-id", "INV-10023", "--user", "analyst"],
        ["--invoice-id", "INV-10023", "--step-delay-seconds", "-1"],
        ["--invoice-id", "INV-10023", "--step-delay-seconds", "not-a-number"],
    ],
)
def test_parse_arguments_rejects_missing_or_unsupported_options(arguments: list[str]) -> None:
    with pytest.raises(SystemExit):
        parse_arguments(arguments)


@pytest.mark.parametrize(
    ("test_id_text", "locator", "expected_decision", "expected_control"),
    [
        (
            {
                "invoice-amount": "$4820.00",
                "invoice-has-po": "Yes",
                "invoice-three-way-match": "Yes",
            },
            "role",
            "AUTO_PROCESS",
            ("get_by_role", "button", "Process"),
        ),
        (
            {
                "invoice-amount": "$8100.00",
                "invoice-has-po": "Yes",
                "invoice-three-way-match": "Yes",
            },
            "testid",
            "MANUAL_REVIEW",
            ("get_by_test_id", "invoice-manual-review"),
        ),
    ],
)
def test_prepare_invoice_decision_via_ui_reads_facts_applies_rules_and_selects_control(
    test_id_text: dict[str, str],
    locator: str,
    expected_decision: str,
    expected_control: tuple[object, ...],
) -> None:
    page = FakePage(test_id_text)

    decision, control = prepare_invoice_decision_via_ui(page, locator=locator)

    assert decision.value == expected_decision
    assert expected_control in page.calls
    assert control.calls == page.calls
    assert not any(call[0] == "click" for call in page.calls)


def test_testid_selector_keeps_auto_process_when_label_fault_shows_complete() -> None:
    fault_state = FaultState(change_process_button_label=True)
    page = FakePage(
        {
            "invoice-amount": "$4820.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-process": "Complete" if fault_state.change_process_button_label else "Process",
        }
    )

    decision, control = prepare_invoice_decision_via_ui(page, locator="testid")

    assert fault_state.change_process_button_label is True
    assert decision is Decision.AUTO_PROCESS
    assert control.selector == ("invoice-process",)
    assert control.inner_text() == "Complete"


@pytest.mark.parametrize(
    ("invoice_id", "decision", "final_status"),
    [
        ("INV-10023", "AUTO_PROCESS", "AUTO_PROCESSED"),
        ("INV-10024", "MANUAL_REVIEW", "MANUAL_REVIEW"),
    ],
)
def test_complete_invoice_decision_via_ui_clicks_selected_control_and_reads_final_status(
    invoice_id: str, decision: str, final_status: str
) -> None:
    page = FakePage({"invoice-status": final_status})
    control = page.get_by_test_id(f"decision-{invoice_id}")

    result = complete_invoice_decision_via_ui(page, decision=Decision(decision), control=control)

    assert result == {"decision": decision, "status": final_status}
    assert page.calls == [
        ("get_by_test_id", f"decision-{invoice_id}"),
        ("click", f"decision-{invoice_id}"),
        ("get_by_test_id", "invoice-status"),
        ("inner_text", "invoice-status"),
    ]


def test_run_bot_uses_only_the_ui_flow_and_closes_headless_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(
        {
            "invoice-amount": "$4820.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-status": "AUTO_PROCESSED",
        }
    )
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))

    result = run_bot(
        base_url="http://127.0.0.1:8000",
        invoice_id="INV-10023",
        locator="role",
        headed=False,
        step_delay_seconds=0,
        username="demo-user",
        password="demo-password",
    )

    assert result == {"decision": "AUTO_PROCESS", "status": "AUTO_PROCESSED"}
    assert playwright.launches == [True]
    assert browser.calls == [("new_page", None), ("close", None)]
    assert page.calls[0] == ("goto", "http://127.0.0.1:8000/login")
    assert not any("/api/" in str(call) for call in page.calls)


def test_run_bot_waits_between_visible_steps_only_when_delay_is_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(
        {
            "invoice-amount": "$4820.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-status": "AUTO_PROCESSED",
        }
    )
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))

    run_bot(
        base_url="http://127.0.0.1:8000",
        invoice_id="INV-10023",
        locator="role",
        headed=True,
        step_delay_seconds=1.25,
        username="demo-user",
        password="demo-password",
    )

    assert [
        (index, call) for index, call in enumerate(page.calls) if call[0] == "wait_for_timeout"
    ] == [
        (1, ("wait_for_timeout", 1250)),
        (4, ("wait_for_timeout", 1250)),
        (7, ("wait_for_timeout", 1250)),
        (10, ("wait_for_timeout", 1250)),
        (15, ("wait_for_timeout", 1250)),
        (18, ("wait_for_timeout", 1250)),
        (26, ("wait_for_timeout", 1250)),
        (28, ("wait_for_timeout", 1250)),
    ]


def test_run_bot_waits_at_least_one_millisecond_for_positive_submillisecond_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = FakePage(
        {
            "invoice-amount": "$4820.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-status": "AUTO_PROCESSED",
        }
    )
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))

    run_bot(
        base_url="http://127.0.0.1:8000",
        invoice_id="INV-10023",
        locator="role",
        headed=False,
        step_delay_seconds=0.0001,
        username="demo-user",
        password="demo-password",
    )

    assert [call for call in page.calls if call[0] == "wait_for_timeout"] == [
        ("wait_for_timeout", 1)
    ] * 8


def test_run_bot_does_not_wait_when_step_delay_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    page = FakePage(
        {
            "invoice-amount": "$4820.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-status": "AUTO_PROCESSED",
        }
    )
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))

    run_bot(
        base_url="http://127.0.0.1:8000",
        invoice_id="INV-10023",
        locator="role",
        headed=False,
        step_delay_seconds=0,
        username="demo-user",
        password="demo-password",
    )

    assert not any(call[0] == "wait_for_timeout" for call in page.calls)


def test_bot_module_has_no_sqlite_or_api_dependency() -> None:
    source = inspect.getsource(bot)

    assert "sqlite3" not in source
    assert "/api/" not in source


def test_main_runs_headed_when_requested_and_prints_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = FakePage(
        {
            "invoice-amount": "$8100.00",
            "invoice-has-po": "Yes",
            "invoice-three-way-match": "Yes",
            "invoice-status": "MANUAL_REVIEW",
        }
    )
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))
    monkeypatch.setattr(
        bot, "auth_settings", lambda: type("Settings", (), {"username": "u", "password": "p"})()
    )

    assert main(["--invoice-id", "INV-10024", "--headed"]) == 0
    assert playwright.launches == [False]
    assert capsys.readouterr().out == "INV-10024: MANUAL_REVIEW\n"


def test_main_saves_fixed_failure_screenshot_closes_browser_and_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path
) -> None:
    page = FailingPage()
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))
    monkeypatch.setattr(
        bot, "auth_settings", lambda: type("Settings", (), {"username": "u", "password": "p"})()
    )

    assert main(["--invoice-id", "INV-10023"]) == 1
    assert browser.calls == [("new_page", None), ("close", None)]
    assert page.calls[-1] == ("screenshot", "artifacts/playwright_failure.png")
    assert (tmp_path / "artifacts").is_dir()
    assert capsys.readouterr().out == (
        "Playwright failed; screenshot saved to artifacts/playwright_failure.png: navigation failed\n"
    )


def test_main_reports_when_launch_failure_prevents_screenshot_capture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    playwright = LaunchFailingPlaywright()
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))
    monkeypatch.setattr(
        bot, "auth_settings", lambda: type("Settings", (), {"username": "u", "password": "p"})()
    )

    assert main(["--invoice-id", "INV-10023"]) == 1
    assert playwright.launches == [True]
    assert capsys.readouterr().out == (
        "Playwright failed; screenshot could not be captured because no page was created: launch failed\n"
    )


def test_main_reports_when_failure_screenshot_capture_fails_and_closes_browser(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    page = ScreenshotFailingPage()
    browser = FakeBrowser(page)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(bot, "sync_playwright", lambda: FakePlaywrightContext(playwright))
    monkeypatch.setattr(
        bot, "auth_settings", lambda: type("Settings", (), {"username": "u", "password": "p"})()
    )

    assert main(["--invoice-id", "INV-10023"]) == 1
    assert browser.calls == [("new_page", None), ("close", None)]
    assert page.calls[-1] == ("screenshot", "artifacts/playwright_failure.png")
    assert (
        capsys.readouterr().out
        == "Playwright failed; screenshot capture failed: navigation failed\n"
    )
