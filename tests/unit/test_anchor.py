import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT_HASH = "a" * 64
ADDRESS = "0x1234567890123456789012345678901234567890"
SIGNER = "0xAbCdEf0123456789aBCdEf0123456789AbCdEf01"


def _manifest(path: Path, *, chain_id: int = 31337, address: str | None = ADDRESS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract": "EvidenceRootAnchor",
                "chain_id": chain_id,
                "address": address,
            }
        )
    )
    return path


def test_root_hash_is_consumed_as_the_c3_t03_bytes32_value() -> None:
    from invoiceops.anchor import root_hash_bytes

    assert root_hash_bytes(ROOT_HASH) == bytes.fromhex(ROOT_HASH)


@pytest.mark.parametrize("root_hash", ["A" * 64, "0x" + ROOT_HASH, "a" * 63, "not-a-root"])
def test_root_hash_rejects_non_canonical_c3_t03_values(root_hash: str) -> None:
    from invoiceops.anchor import AnchorConfigurationError, root_hash_bytes

    with pytest.raises(AnchorConfigurationError, match="64 lowercase hexadecimal"):
        root_hash_bytes(root_hash)


def test_resolve_deployment_reads_the_versioned_manifest_without_console_output(
    tmp_path: Path,
) -> None:
    from invoiceops.anchor import resolve_deployment

    deployment = resolve_deployment(_manifest(tmp_path / "local.json"))

    assert deployment.address == ADDRESS
    assert deployment.chain_id == 31337


def test_resolve_deployment_rejects_an_incomplete_manifest(tmp_path: Path) -> None:
    from invoiceops.anchor import AnchorConfigurationError, resolve_deployment

    path = tmp_path / "local.json"
    path.write_text('{"contract": "EvidenceRootAnchor", "chain_id": 31337}')

    with pytest.raises(AnchorConfigurationError, match="address"):
        resolve_deployment(path)


def test_deploy_persists_the_json_address_in_the_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import anchor

    manifest = _manifest(tmp_path / "deployments" / "local.json", address=None)
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    commands: list[list[str]] = []
    monkeypatch.setattr(anchor.shutil, "which", lambda executable: "/usr/local/bin/forge")
    monkeypatch.setattr(
        anchor.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command)
        or SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"deployedTo": ADDRESS}),
            stderr="",
        ),
    )

    deployment = anchor.deploy_anchor(web3, SIGNER, manifest_path=manifest)

    assert deployment.address == ADDRESS
    assert json.loads(manifest.read_text())["signer"] == SIGNER
    assert "--json" in commands[0]
    assert "--unlocked" in commands[0]


def test_chain_rejects_an_unreachable_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    from invoiceops import anchor

    class UnreachableWeb3:
        class HTTPProvider:
            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

        def __init__(self, provider: object) -> None:
            self.provider = provider

        def is_connected(self) -> bool:
            return False

    monkeypatch.setattr(anchor, "Web3", UnreachableWeb3)

    with pytest.raises(anchor.AnchorRpcError, match="cannot connect"):
        anchor.chain("http://127.0.0.1:8545")


def test_chain_rejects_an_unexpected_chain_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from invoiceops import anchor

    class WrongChainWeb3:
        class HTTPProvider:
            def __init__(self, rpc_url: str) -> None:
                self.rpc_url = rpc_url

        def __init__(self, provider: object) -> None:
            self.eth = SimpleNamespace(chain_id=1)

        def is_connected(self) -> bool:
            return True

    monkeypatch.setattr(anchor, "Web3", WrongChainWeb3)

    with pytest.raises(anchor.AnchorConfigurationError, match="expected chain ID 31337, got 1"):
        anchor.chain("http://127.0.0.1:8545")


def test_local_signer_requires_an_unlocked_account() -> None:
    from invoiceops.anchor import AnchorSignerError, local_signer

    with pytest.raises(AnchorSignerError, match="unlocked account"):
        local_signer(SimpleNamespace(eth=SimpleNamespace(accounts=[])))


def test_register_and_query_use_the_same_contract_api() -> None:
    from invoiceops.anchor import is_root_registered, register_root

    calls: list[tuple[str, object]] = []

    class Register:
        def transact(self, options: dict[str, str]) -> bytes:
            calls.append(("transact", options))
            return b"transaction"

    class Functions:
        def registerRoot(self, root_hash: bytes) -> Register:
            calls.append(("register", root_hash))
            return Register()

        def isRootRegistered(self, root_hash: bytes) -> SimpleNamespace:
            calls.append(("query", root_hash))
            return SimpleNamespace(call=lambda: True)

    receipt = {"status": 1, "transactionHash": b"transaction"}
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(functions=Functions()),
            wait_for_transaction_receipt=lambda transaction_hash: receipt,
        )
    )

    assert register_root(web3, ADDRESS, SIGNER, ROOT_HASH) == receipt
    assert is_root_registered(web3, ADDRESS, ROOT_HASH) is True
    assert calls == [
        ("register", bytes.fromhex(ROOT_HASH)),
        ("transact", {"from": SIGNER}),
        ("query", bytes.fromhex(ROOT_HASH)),
    ]


def test_anchor_cli_reuses_the_python_api(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from invoiceops import anchor

    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)
    monkeypatch.setattr(anchor, "chain", lambda rpc_url, expected_chain_id=None: "chain")
    monkeypatch.setattr(anchor, "local_signer", lambda web3: SIGNER)
    monkeypatch.setattr(anchor, "resolve_deployment", lambda manifest: deployment)
    monkeypatch.setattr(anchor, "is_root_registered", lambda web3, address, root_hash: True)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "anchor",
            "status",
            "--manifest",
            "contracts/deployments/local.json",
            "--root-hash",
            ROOT_HASH,
        ],
    )

    anchor.main()

    assert json.loads(capsys.readouterr().out) == {
        "address": ADDRESS,
        "chain_id": 31337,
        "registered": True,
        "signer": SIGNER,
    }
