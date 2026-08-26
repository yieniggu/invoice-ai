CREATE TABLE IF NOT EXISTS model_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    model_name TEXT,
    model_version TEXT,
    run_id TEXT,
    manual_review_probability REAL,
    policy_version TEXT NOT NULL,
    policy_threshold REAL NOT NULL,
    recommendation TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('model', 'fallback')),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);

CREATE INDEX IF NOT EXISTS idx_model_evaluations_invoice_id
    ON model_evaluations (invoice_id);
