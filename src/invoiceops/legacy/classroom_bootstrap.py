"""Initialize the classroom SQLite volume without changing application startup."""

from invoiceops.legacy.seed import seed_invoices


def bootstrap_classroom_database() -> None:
    """Apply migrations and insert the idempotent classroom invoice fixtures."""
    seed_invoices()


def main() -> None:
    bootstrap_classroom_database()
    print("Classroom SQLite bootstrap complete.")


if __name__ == "__main__":
    main()
