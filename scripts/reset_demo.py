import argparse
import os
from pathlib import Path

from invoiceops.legacy.db import _resolve_db_path, list_invoices, reset_db
from invoiceops.legacy.seed import seed_invoices


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reset the selected local InvoiceOps demo database."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm deletion of invoices, decisions, and model evaluations",
    )
    arguments = parser.parse_args()
    if not arguments.confirm:
        parser.error(
            "--confirm is required because this permanently deletes selected database data"
        )

    db_path = _resolve_db_path(None)
    demo_root = Path(os.environ.get("INVOICEOPS_NOTEBOOK_DEMO_ROOT", "var/t23_5_demo"))
    state_path = demo_root / "state.json"
    print(f"Resetting database: {db_path}")
    print("Impact: invoices, decisions, and model evaluations will be permanently deleted.")
    print(f"Removing auxiliary notebook state: {state_path}")
    reset_db(db_path)
    seed_invoices(db_path)
    state_path.unlink(missing_ok=True)
    print(f"Database: {db_path}")
    print(f"Invoices: {len(list_invoices(db_path).invoices)}")


if __name__ == "__main__":
    main()
