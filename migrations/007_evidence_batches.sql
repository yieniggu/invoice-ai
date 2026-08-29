CREATE TABLE IF NOT EXISTS evidence_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_version TEXT NOT NULL,
    root_hash TEXT NOT NULL,
    leaf_count INTEGER NOT NULL CHECK (leaf_count > 0),
    status TEXT NOT NULL CHECK (status = 'verified'),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_batch_items (
    batch_id INTEGER NOT NULL,
    evaluation_id INTEGER NOT NULL,
    evidence_contract_version TEXT NOT NULL,
    leaf_index INTEGER NOT NULL CHECK (leaf_index >= 0),
    leaf_hash TEXT NOT NULL,
    proof_json TEXT NOT NULL,
    PRIMARY KEY (batch_id, evaluation_id),
    UNIQUE (batch_id, leaf_index),
    FOREIGN KEY (batch_id) REFERENCES evidence_batches(id) ON DELETE CASCADE,
    FOREIGN KEY (evaluation_id, evidence_contract_version)
        REFERENCES evidence_records(evaluation_id, contract_version)
);

CREATE INDEX IF NOT EXISTS idx_evidence_batch_items_evaluation
    ON evidence_batch_items (evaluation_id);
