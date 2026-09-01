import shutil
from pathlib import Path

from invoiceops.demo_paths import validate_demo_root
from invoiceops.legacy.db import reset_db
from invoiceops.legacy.seed import seed_invoices


def demo_resources(demo_root: Path) -> tuple[Path, ...]:
    """Return the only local artifacts this reset command is allowed to remove."""
    demo_root = validate_demo_root(demo_root, require_marker=False)
    return (
        demo_root / "invoiceops.db",
        demo_root / "invoiceops.db-shm",
        demo_root / "invoiceops.db-wal",
        demo_root / "mlflow.db",
        demo_root / "mlflow-artifacts",
        demo_root / "notebook-state" / "state.json",
        demo_root / "data" / "invoice-risk-v1",
    )


def _reject_symlinked_resources(demo_root: Path, resources: tuple[Path, ...]) -> None:
    for resource in resources:
        path = resource
        while path != demo_root:
            if path.is_symlink():
                raise ValueError(f"Refusing to follow symlinked demo resource: {path}")
            path = path.parent


def reset_local_demo(demo_root: Path, *, confirmed: bool) -> tuple[Path, ...]:
    root = validate_demo_root(demo_root, require_marker=confirmed)
    resources = demo_resources(root)
    if not confirmed:
        return resources

    _reject_symlinked_resources(root, resources)
    database, *removable = resources
    for path in removable:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    reset_db(database)
    seed_invoices(database)
    return resources
