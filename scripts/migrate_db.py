import argparse
from pathlib import Path

from invoiceops.legacy.db import run_migrations


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply pending SQLite migrations.")
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()
    run_migrations(args.db_path)


if __name__ == "__main__":
    main()
