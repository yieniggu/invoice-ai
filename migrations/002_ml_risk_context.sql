ALTER TABLE invoices ADD COLUMN vendor_tenure_days INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN previous_incidents_12m INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN bank_account_recently_changed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN amount_vs_vendor_median REAL NOT NULL DEFAULT 1.0;
ALTER TABLE invoices ADD COLUMN country_risk TEXT NOT NULL DEFAULT 'medium';
