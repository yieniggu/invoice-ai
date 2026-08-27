CREATE UNIQUE INDEX IF NOT EXISTS idx_model_evaluations_notebook_operation
    ON model_evaluations (invoice_id, correlation_id)
    WHERE correlation_id GLOB 'notebook-05:*';
