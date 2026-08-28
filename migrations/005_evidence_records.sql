CREATE TABLE IF NOT EXISTS evidence_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER NOT NULL,
    contract_version TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (evaluation_id) REFERENCES model_evaluations(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_records_evaluation_version
    ON evidence_records (evaluation_id, contract_version);
