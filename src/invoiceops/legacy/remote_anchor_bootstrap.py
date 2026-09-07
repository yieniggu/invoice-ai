"""Bridge an optional protected Remote manifest into Portal's runtime volume."""

import json
import os
from pathlib import Path

from invoiceops.anchor import AnchorConfigurationError, resolve_deployment

SOURCE_PATH = Path("/host/invoiceops/contract-manifest.json")
RUNTIME_PATH = Path("/run/invoiceops/contract-manifest.json")
STATUS_PATH = Path("/run/invoiceops/remote-manifest-bootstrap-status")
PORTAL_GID = 101


def _write_status(path: Path, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{status}\n", encoding="utf-8")
    os.chown(path, 0, PORTAL_GID)
    path.chmod(0o640)


def _validate_manifest(path: Path) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AnchorConfigurationError("remote manifest is unreadable or invalid JSON") from error
    if not isinstance(payload, dict):
        raise AnchorConfigurationError("remote manifest must be a JSON object")
    if any("private" in field.lower() for field in payload):
        raise AnchorConfigurationError("remote manifest cannot contain private fields")
    if any(not payload.get(field) for field in ("chain_id", "contract", "address", "signer")):
        raise AnchorConfigurationError("remote manifest is missing required public identity")
    resolve_deployment(path)


def bootstrap_remote_manifest(
    *,
    source: Path = SOURCE_PATH,
    destination: Path = RUNTIME_PATH,
    status_path: Path = STATUS_PATH,
    configured: bool | None = None,
) -> str:
    """Copy a validated optional manifest, or explicitly clear a disabled runtime."""
    if configured is None:
        configured = bool(os.getenv("INVOICEOPS_REMOTE_ANCHOR_MANIFEST_HOST"))
    if not configured:
        destination.unlink(missing_ok=True)
        _write_status(status_path, "skipped")
        return "skipped"
    if not source.is_file() or source.stat().st_size == 0:
        raise AnchorConfigurationError("configured remote manifest is missing or empty")

    _validate_manifest(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(source.read_bytes())
    os.chown(temporary, 0, PORTAL_GID)
    temporary.chmod(0o640)
    temporary.replace(destination)
    _write_status(status_path, "ready")
    return "ready"


def main() -> None:
    status = bootstrap_remote_manifest()
    print(f"remote_manifest={status}")


if __name__ == "__main__":
    main()
