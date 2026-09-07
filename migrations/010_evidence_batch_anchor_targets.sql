ALTER TABLE evidence_batch_anchors ADD COLUMN target TEXT NOT NULL DEFAULT 'local'
    CHECK (target IN ('local', 'remote'));

DROP INDEX idx_evidence_batch_anchors_batch_id;

CREATE UNIQUE INDEX idx_evidence_batch_anchors_batch_target
    ON evidence_batch_anchors (batch_id, target);
