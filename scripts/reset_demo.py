from invoiceops.legacy.db import _resolve_db_path, list_invoices, reset_db
from invoiceops.legacy.seed import seed_invoices


def main() -> None:
    db_path = _resolve_db_path(None)
    reset_db(db_path)
    seed_invoices(db_path)
    print(f"Database: {db_path}")
    print(f"Invoices: {len(list_invoices(db_path))}")


if __name__ == "__main__":
    main()
