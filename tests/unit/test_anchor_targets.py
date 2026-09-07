import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoiceops import anchor_targets
from invoiceops.anchor import AnchorConfigurationError, RemoteSigner

ROOT_HASH = "a" * 64
ADDRESS = "0x1234567890123456789012345678901234567890"
AUTHORIZED_SIGNER = "0xAbCdEf0123456789aBCdEf0123456789AbCdEf01"
OTHER_SIGNER = "0x1111111111111111111111111111111111111111"


def _configure_remote_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    manifest = tmp_path / "remote.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "Test remote",
                "contract": "EvidenceRootAnchor",
                "chain_id": 10200,
                "address": ADDRESS,
                "signer": AUTHORIZED_SIGNER,
            }
        )
    )
    monkeypatch.setenv("INVOICEOPS_REMOTE_ANCHOR_MANIFEST", str(manifest))
    monkeypatch.setenv("INVOICEOPS_REMOTE_ANCHOR_RPC_URL", "https://rpc.example.test")
    monkeypatch.setenv("INVOICEOPS_REMOTE_ANCHOR_PRIVATE_KEY", "test-key-not-rendered")


def test_remote_preflight_rejects_an_incorrect_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_remote_target(monkeypatch, tmp_path)
    monkeypatch.setattr(
        anchor_targets,
        "chain",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AnchorConfigurationError("expected chain ID 10200, got 1; check RPC URL and manifest")
        ),
    )

    with pytest.raises(AnchorConfigurationError, match="expected chain ID 10200, got 1"):
        anchor_targets.preflight_anchor_target(
            "remote", SimpleNamespace(status="verified", root_hash=ROOT_HASH), False
        )


def test_remote_preflight_rejects_a_signer_not_declared_by_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _configure_remote_target(monkeypatch, tmp_path)
    web3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda _address: b"contract"))
    monkeypatch.setattr(anchor_targets, "chain", lambda *_args, **_kwargs: web3)
    monkeypatch.setattr(
        anchor_targets,
        "remote_signer_from_environment",
        lambda *_args: RemoteSigner(address=OTHER_SIGNER, private_key="in-memory"),
    )

    with pytest.raises(AnchorConfigurationError, match="does not match the deployment manifest"):
        anchor_targets.preflight_anchor_target(
            "remote", SimpleNamespace(status="verified", root_hash=ROOT_HASH), False
        )
