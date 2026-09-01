from pathlib import Path

from invoiceops.legacy.db import PROJECT_ROOT

DEMO_ROOT_MARKER = ".invoiceops-demo-root"
DEMO_ROOT_MARKER_CONTENT = "invoiceops-local-demo-v1\n"


def canonical_demo_root() -> Path:
    """Return the one directory this project permits the reset command to manage."""
    return PROJECT_ROOT / "var" / "local-demo"


def validate_demo_root(demo_root: Path, *, require_marker: bool) -> Path:
    """Return the canonical demo root only when it cannot escape via a symlink."""
    candidate = demo_root if demo_root.is_absolute() else PROJECT_ROOT / demo_root
    candidate = candidate.absolute()
    resolved = candidate.resolve(strict=False)
    expected = canonical_demo_root().absolute()
    expected_resolved = expected.resolve(strict=False)

    if candidate != resolved or expected != expected_resolved:
        raise ValueError("Demo root must not contain symbolic links")
    if resolved != expected_resolved:
        raise ValueError("Demo root must be the canonical project-local var/local-demo directory")

    marker = resolved / DEMO_ROOT_MARKER
    if require_marker:
        if marker.is_symlink() or not marker.is_file():
            raise ValueError("Demo root is not initialized by InvoiceOps bootstrap")
        if marker.read_text(encoding="utf-8") != DEMO_ROOT_MARKER_CONTENT:
            raise ValueError("Demo root has an invalid InvoiceOps ownership marker")
    return resolved


def initialize_demo_root(demo_root: Path) -> Path:
    """Create the ownership marker used to authorize local demo cleanup."""
    root = validate_demo_root(demo_root, require_marker=False)
    if root.exists() and not root.is_dir():
        raise ValueError("Demo root must be a directory")
    if root.exists():
        entries = tuple(root.iterdir())
        marker = root / DEMO_ROOT_MARKER
        if entries and (entries != (marker,) or not marker.is_file()):
            raise ValueError("Demo root contains files not initialized by InvoiceOps")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / DEMO_ROOT_MARKER
    if marker.is_symlink():
        raise ValueError("Demo root ownership marker must not be a symbolic link")
    marker.write_text(DEMO_ROOT_MARKER_CONTENT, encoding="utf-8")
    return root


def ensure_demo_root(demo_root: Path) -> Path:
    """Reuse a marked canonical root or initialize an empty one for bootstrap."""
    root = validate_demo_root(demo_root, require_marker=False)
    marker = root / DEMO_ROOT_MARKER
    if marker.is_file() and not marker.is_symlink():
        return validate_demo_root(root, require_marker=True)
    return initialize_demo_root(root)
