# InvoiceOps

InvoiceOps is a local teaching MVP for invoice operations. It provides a FastAPI
web portal, a JSON API, SQLite-backed seed data, a Playwright automation bot,
and in-memory fault controls for controlled demo use.

## Requirements

- Python 3.12 (`.python-version` and `pyproject.toml` require it)
- [uv](https://docs.astral.sh/uv/)
- Docker and Docker Compose only when using the Compose workflow
- Chromium for the Playwright bot

Clone the repository, then work from the project directory:

```bash
git clone https://github.com/yieniggu/invoice-ai.git
cd invoice-ai
```

## Local setup and startup

Install the project and development dependencies:

```bash
uv sync --all-groups
```

Install the browser required by the Playwright bot:

```bash
uv run playwright install chromium
```

Start the local demo application:

```bash
INVOICEOPS_MODE=demo uv run uvicorn invoiceops.legacy.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/login`. The default demo credentials are
`analyst` / `demo-password`.

The default SQLite database is `var/invoiceops.db`. Set `INVOICEOPS_DB_PATH`
to use a different database path.

## Docker Compose

Start the containerized demo:

```bash
docker compose up --build
```

The application is available at `http://localhost:8000/login`. Compose mounts
the `invoice-data` volume at `/app/var`, so its SQLite database persists across
container restarts.

Stop the stack while preserving the volume:

```bash
docker compose down
```

> Warning: this command permanently removes the Compose `invoice-data` volume
> and its SQLite database. Use it only when a complete containerized-demo reset
> is intended.

```bash
docker compose down -v
```

## Reset demo data

Resetting drops the `invoices` and `decision_events` SQLite tables, then creates
the schema and inserts the six seed invoices. It permanently discards local
decisions and audit events in the selected database.

```bash
INVOICEOPS_MODE=demo uv run python scripts/reset_demo.py
```

The seed invoice IDs are `INV-10023` through `INV-10028`. `INV-10028` starts in
the `CANCELLED` state; the other seed invoices start as `PENDING`.

## Web portal and bot

The portal requires login for `/invoices` and `/admin/faults`. Use the demo
credentials above in demo mode. A successful decision is recorded with the
`ui` actor and a generated correlation ID.

With the application running, process a pending invoice through the browser UI:

```bash
INVOICEOPS_MODE=demo uv run python -m invoiceops.automation.bot --invoice-id INV-10023
```

The bot defaults to `http://127.0.0.1:8000`, uses role-based locators, and runs
headlessly. Use `--headed`, `--locator testid`, `--base-url`, or
`--step-delay-seconds` when needed. A Playwright failure may write
`artifacts/playwright_failure.png`.

Run `scripts/reset_demo.py` before repeating a bot decision for an invoice: an
invoice can only be decided while it is `PENDING`.

## Chaos controls

After login, open `http://127.0.0.1:8000/admin/faults`. The panel can change the
Process button label, add 0/1000/3000/5000 ms detail-page latency, or make the
decision API return `503`. `Reset All` clears all faults for the current process.

The fault state is process-local memory. Run exactly one application worker and
one replica while using fault controls. Restarting the process also clears every
fault; multiple workers or replicas are unsupported for this feature.

## API, health, and audit data

Check the health endpoint:

```bash
curl http://127.0.0.1:8000/api/health
```

The JSON API exposes:

- `GET /api/invoices?q=<query>`: lists up to 100 invoices and returns `has_more`.
- `GET /api/invoices/{invoice_id}`: returns one invoice or `404`.
- `POST /api/v1/invoices/{invoice_id}/decision`: accepts a JSON body with
  `{"decision":"AUTO_PROCESS"}` or `{"decision":"MANUAL_REVIEW"}`.

Successful API decisions return and persist a correlation ID, and create a
`decision_events` audit record with actor `api`. The invoice detail page displays
its audit events for UI decisions.

## Tests

Run the test suite:

```bash
uv run pytest
```

## Demo and secure modes

`INVOICEOPS_MODE` must be explicitly set to either `demo` or `secure`.

- `demo` defaults to the public local credentials shown above and the
  development session secret `dev-only-change-me`. It is for local demo use only.
- `secure` requires non-empty `INVOICEOPS_DEMO_USERNAME`,
  `INVOICEOPS_DEMO_PASSWORD`, and `INVOICEOPS_SESSION_SECRET`. It rejects the
  demo session secret and uses `Secure`, `HttpOnly`, and `SameSite=Lax` session
  cookie attributes. Terminate TLS at or before the application.

The Compose credentials and session secret are demo-only values, not production
secret management.

## Repository map

- `src/invoiceops/domain/`: invoice models and decision rules.
- `src/invoiceops/legacy/`: FastAPI application, authentication, SQLite access,
  seed data, templates, static assets, and fault controls.
- `src/invoiceops/automation/bot.py`: Playwright UI automation bot.
- `scripts/reset_demo.py`: destructive SQLite reset and seed command.
- `tests/`: unit and integration tests.
- `compose.yml` and `Dockerfile`: containerized demo runtime.

## Documentation scope

This README contains public product-operation information only. Instructor-facing
class scripts, pre-class instructions, demo sequences, and fallback procedures
are **DEFERRED_APPROVED/local-only** and are intentionally not tracked in this
repository.
