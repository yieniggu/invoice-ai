CREATE TABLE IF NOT EXISTS evidence_batch_anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    root_hash TEXT NOT NULL,
    chain_id INTEGER NOT NULL,
    contract_address TEXT NOT NULL,
    transaction_hash TEXT,
    block_number INTEGER,
    gas_used INTEGER,
    submitted_at TEXT NOT NULL,
    anchored_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('submitted', 'verified', 'failed', 'ambiguous')),
    FOREIGN KEY (batch_id) REFERENCES evidence_batches(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_batch_anchors_batch_id
    ON evidence_batch_anchors (batch_id);

CREATE INDEX IF NOT EXISTS idx_evidence_batch_anchors_transaction_hash
    ON evidence_batch_anchors (transaction_hash);
