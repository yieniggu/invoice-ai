# InvoiceOps

InvoiceOps is a teaching MVP for invoice operations.

The web login defaults are available only when `INVOICEOPS_MODE=demo`: `analyst` /
`demo-password` with the development session secret `dev-only-change-me`. This is
for local demonstration only.

## Teaching fault controls

The `/admin/faults` panel is for a **single application instance only**. Its
fault state is held in memory by one process: run one worker and one replica.
It does not support multiple workers or replicas. `Reset All` resets only the
current process, and restarting that process also resets every fault.

For a deployment, set `INVOICEOPS_MODE=secure` and provide non-empty values for
`INVOICEOPS_DEMO_USERNAME`, `INVOICEOPS_DEMO_PASSWORD`, and
`INVOICEOPS_SESSION_SECRET`. Secure mode rejects the demo session secret and sets
the session cookie with `Secure`, `HttpOnly`, and `SameSite=Lax`; terminate TLS at
or before the application. The application refuses to start unless its mode is
explicitly selected.
