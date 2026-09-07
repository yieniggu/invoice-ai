import json
import os
from pathlib import Path

import pytest

from invoiceops.anchor import AnchorConfigurationError
from invoiceops.legacy.remote_anchor_bootstrap import bootstrap_remote_manifest

ADDRESS = "0x1234567890123456789012345678901234567890"
SIGNER = "0xAbCdEf0123456789aBCdEf0123456789AbCdEf01"


def _write_manifest(path: Path, **overrides: object) -> None:
    manifest = {
        "contract": "EvidenceRootAnchor",
        "chain_id": 10200,
        "address": ADDRESS,
        "signer": SIGNER,
    }
    manifest.update(overrides)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    path.chmod(0o600)


def test_remote_bootstrap_copies_a_protected_source_for_the_portal_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "host-manifest.json"
    runtime = tmp_path / "runtime" / "contract-manifest.json"
    status = tmp_path / "runtime" / "status"
    _write_manifest(source)
    ownership: list[tuple[Path, int, int]] = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: ownership.append((Path(path), uid, gid)))

    assert (source.stat().st_uid, source.stat().st_gid, source.stat().st_mode & 0o777) == (
        os.getuid(),
        os.getgid(),
        0o600,
    )
    assert bootstrap_remote_manifest(
        source=source, destination=runtime, status_path=status, configured=True
    ) == "ready"

    assert json.loads(runtime.read_text(encoding="utf-8"))["address"] == ADDRESS
    assert runtime.stat().st_mode & 0o777 == 0o640
    assert any(uid == 0 and gid == 101 for _path, uid, gid in ownership)
    assert status.read_text(encoding="utf-8") == "ready\n"


def test_remote_bootstrap_skips_absent_configuration_and_removes_stale_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = tmp_path / "runtime" / "contract-manifest.json"
    runtime.parent.mkdir()
    runtime.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(os, "chown", lambda *_args: None)

    assert bootstrap_remote_manifest(
        destination=runtime, status_path=runtime.parent / "status", configured=False
    ) == "skipped"

    assert not runtime.exists()
    assert (runtime.parent / "status").read_text(encoding="utf-8") == "skipped\n"


@pytest.mark.parametrize(
    "manifest",
    (
        {"private_key": "must-not-copy"},
        {"contract": "EvidenceRootAnchor", "chain_id": 10200, "address": ADDRESS},
        {"contract": "not-the-anchor", "chain_id": 10200, "address": ADDRESS, "signer": SIGNER},
    ),
)
def test_remote_bootstrap_rejects_private_or_invalid_manifests(
    tmp_path: Path, manifest: dict[str, object]
) -> None:
    source = tmp_path / "host-manifest.json"
    destination = tmp_path / "runtime" / "contract-manifest.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    source.chmod(0o600)

    with pytest.raises(AnchorConfigurationError):
        bootstrap_remote_manifest(
            source=source,
            destination=destination,
            status_path=tmp_path / "runtime" / "status",
            configured=True,
        )
    assert not destination.exists()
