CREATE TABLE IF NOT EXISTS evidence_batch_successors (
    origin_batch_id INTEGER NOT NULL,
    successor_batch_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (origin_batch_id, successor_batch_id),
    FOREIGN KEY (origin_batch_id) REFERENCES evidence_batches(id),
    FOREIGN KEY (successor_batch_id) REFERENCES evidence_batches(id),
    CHECK (origin_batch_id != successor_batch_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_batch_successors_origin
    ON evidence_batch_successors (origin_batch_id);
