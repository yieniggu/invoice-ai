ALTER TABLE evidence_records ADD COLUMN canonical_version TEXT;
ALTER TABLE evidence_records ADD COLUMN canonical_payload TEXT;
ALTER TABLE evidence_records ADD COLUMN digest_algorithm TEXT;
ALTER TABLE evidence_records ADD COLUMN digest_hex TEXT;
