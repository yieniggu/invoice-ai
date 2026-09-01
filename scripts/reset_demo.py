import argparse
from pathlib import Path

from invoiceops.demo_reset import demo_resources, reset_local_demo
from invoiceops.legacy.db import list_invoices

DEMO_ROOT = Path("var/local-demo")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview or reset the isolated local InvoiceOps demo resources."
    )
    parser.add_argument(
        "--demo-root",
        type=Path,
        default=DEMO_ROOT,
        help="must be the canonical var/local-demo directory containing this local demo's state",
    )
    parser.add_argument(
        "--confirm-reset-local-demo",
        action="store_true",
        help="permanently remove the declared local demo resources, including its dataset, and reseed SQLite",
    )
    arguments = parser.parse_args()
    demo_root = arguments.demo_root
    resources = demo_resources(demo_root)
    print("Local demo resources (and no others):")
    for path in resources:
        print(path)
    if not arguments.confirm_reset_local_demo:
        print("DRY RUN: no files were changed. Re-run with --confirm-reset-local-demo to reset.")
        return

    reset_local_demo(demo_root, confirmed=True)
    database = resources[0]
    print(f"Reset complete. Recreated database: {database}")
    print(f"Invoices: {len(list_invoices(database).invoices)}")


if __name__ == "__main__":
    main()
