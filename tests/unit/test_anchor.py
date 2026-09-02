import json
import sys
from pathlib import Path
from threading import Event, Thread
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
        lambda command, **kwargs: (
            commands.append(command)
            or SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"deployedTo": ADDRESS}),
                stderr="",
            )
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


def test_remote_signer_is_loaded_only_from_the_injected_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invoiceops.anchor import remote_signer_from_environment

    account = SimpleNamespace(address=SIGNER)
    web3 = SimpleNamespace(
        eth=SimpleNamespace(account=SimpleNamespace(from_key=lambda key: account))
    )
    monkeypatch.setenv("TEST_ANCHOR_SIGNER", "not-persisted-anywhere")

    signer = remote_signer_from_environment(web3, "TEST_ANCHOR_SIGNER")

    assert signer.address == SIGNER
    assert signer.private_key == "not-persisted-anywhere"


def test_remote_register_signs_and_submits_without_an_unlocked_rpc_account() -> None:
    from invoiceops.anchor import RemoteSigner, register_root

    calls: list[tuple[str, object]] = []

    class Registration:
        def build_transaction(self, options: dict[str, object]) -> dict[str, object]:
            calls.append(("build", options))
            return {"to": ADDRESS, **options}

    class Functions:
        def registerRoot(self, root_hash: bytes) -> Registration:
            calls.append(("register", root_hash))
            return Registration()

    receipt = {"status": 1, "transactionHash": b"remote-transaction"}
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=421614,
            contract=lambda address, abi: SimpleNamespace(functions=Functions()),
            get_transaction_count=lambda address: 9,
            account=SimpleNamespace(
                sign_transaction=lambda transaction, private_key: SimpleNamespace(
                    raw_transaction=b"signed-transaction"
                )
            ),
            send_raw_transaction=lambda raw: calls.append(("send", raw)) or b"remote-transaction",
            wait_for_transaction_receipt=lambda transaction_hash: receipt,
        )
    )

    assert (
        register_root(
            web3, ADDRESS, RemoteSigner(address=SIGNER, private_key="in-memory"), ROOT_HASH
        )
        == receipt
    )
    assert calls == [
        ("register", bytes.fromhex(ROOT_HASH)),
        ("build", {"from": SIGNER, "chainId": 421614, "nonce": 9}),
        ("send", b"signed-transaction"),
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


def test_batch_anchor_cli_uses_the_persisted_batch_root(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    from invoiceops import anchor

    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)
    result = anchor.EvidenceBatchAnchor(
        id=4,
        batch_id=7,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash="b" * 64,
        block_number=42,
        gas_used=21_000,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at="2026-01-01T00:01:00Z",
        status="verified",
    )
    calls: list[object] = []
    monkeypatch.setattr(anchor, "resolve_deployment", lambda manifest: deployment)
    monkeypatch.setattr(anchor, "chain", lambda rpc_url, expected_chain_id=None: "chain")
    monkeypatch.setattr(anchor, "local_signer", lambda web3: SIGNER)
    monkeypatch.setattr(
        anchor,
        "get_evidence_batch_row",
        lambda db_path, batch_id: {"root_hash": ROOT_HASH},
    )

    def anchor_batch(db_path: Path, **kwargs: object) -> anchor.EvidenceBatchAnchor:
        calls.append((db_path, kwargs))
        return result

    monkeypatch.setattr(anchor, "anchor_evidence_batch", anchor_batch)
    monkeypatch.setattr(
        sys,
        "argv",
        ["anchor", "batch-anchor", "--db", str(tmp_path / "invoiceops.db"), "--batch-id", "7"],
    )

    anchor.main()

    assert calls == [
        (
            tmp_path / "invoiceops.db",
            {
                "batch_id": 7,
                "root_hash": ROOT_HASH,
                "web3": "chain",
                "deployment": deployment,
                "signer": SIGNER,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out) == anchor.asdict(result)


def test_batch_status_cli_returns_the_persisted_anchor(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    from invoiceops import anchor

    result = anchor.EvidenceBatchAnchor(
        id=4,
        batch_id=7,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash=None,
        block_number=None,
        gas_used=None,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at=None,
        status="ambiguous",
    )
    calls: list[tuple[Path, int]] = []

    def inspect(db_path: Path, anchor_id: int) -> anchor.EvidenceBatchAnchor:
        calls.append((db_path, anchor_id))
        return result

    monkeypatch.setattr(anchor, "inspect_evidence_batch_anchor", inspect)
    monkeypatch.setattr(
        sys,
        "argv",
        ["anchor", "batch-status", "--db", str(tmp_path / "invoiceops.db"), "--anchor-id", "4"],
    )

    anchor.main()

    assert calls == [(tmp_path / "invoiceops.db", 4)]
    assert json.loads(capsys.readouterr().out) == anchor.asdict(result)


def test_batch_reconcile_cli_reconciles_the_requested_anchor(
    monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    from invoiceops import anchor

    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)
    result = anchor.EvidenceBatchAnchor(
        id=4,
        batch_id=7,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash="b" * 64,
        block_number=None,
        gas_used=None,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at=None,
        status="ambiguous",
    )
    calls: list[tuple[Path, int, object]] = []
    monkeypatch.setattr(anchor, "resolve_deployment", lambda manifest: deployment)
    monkeypatch.setattr(anchor, "chain", lambda rpc_url, expected_chain_id=None: "chain")

    def reconcile(db_path: Path, anchor_id: int, web3: object) -> anchor.EvidenceBatchAnchor:
        calls.append((db_path, anchor_id, web3))
        return result

    monkeypatch.setattr(anchor, "reconcile_evidence_batch_anchor", reconcile)
    monkeypatch.setattr(
        sys,
        "argv",
        ["anchor", "batch-reconcile", "--db", str(tmp_path / "invoiceops.db"), "--anchor-id", "4"],
    )

    anchor.main()

    assert calls == [(tmp_path / "invoiceops.db", 4, "chain")]
    assert json.loads(capsys.readouterr().out) == anchor.asdict(result)


def test_receipt_registers_root_accepts_the_matching_root_registered_event() -> None:
    from invoiceops import anchor

    class RootRegistered:
        def process_receipt(self, receipt: object) -> list[dict[str, object]]:
            return [{"args": {"rootHash": bytes.fromhex(ROOT_HASH)}}]

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(
                events=SimpleNamespace(RootRegistered=RootRegistered)
            )
        )
    )

    assert anchor._receipt_registers_root(web3, ADDRESS, {"status": 1}, ROOT_HASH) is True


@pytest.mark.parametrize(
    "events",
    [[], [{"args": {"rootHash": bytes.fromhex("b" * 64)}}]],
    ids=["absent", "wrong-root"],
)
def test_receipt_registers_root_rejects_absent_or_invalid_root_registered_event(
    events: list[dict[str, object]],
) -> None:
    from invoiceops import anchor

    class RootRegistered:
        def process_receipt(self, receipt: object) -> list[dict[str, object]]:
            return events

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(
                events=SimpleNamespace(RootRegistered=RootRegistered)
            )
        )
    )

    assert anchor._receipt_registers_root(web3, ADDRESS, {"status": 1}, ROOT_HASH) is False


def test_reconcile_keeps_a_reverted_receipt_failed_when_the_root_preexists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import anchor

    submitted = anchor.EvidenceBatchAnchor(
        id=4,
        batch_id=7,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash="a" * 64,
        block_number=None,
        gas_used=None,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at=None,
        status="submitted",
    )
    failed = anchor.EvidenceBatchAnchor(
        id=4,
        batch_id=7,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash="a" * 64,
        block_number=42,
        gas_used=21_000,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at=None,
        status="failed",
    )
    updates: list[dict[str, object]] = []
    monkeypatch.setattr(
        anchor,
        "inspect_evidence_batch_anchor",
        lambda db_path, anchor_id: failed if updates else submitted,
    )
    monkeypatch.setattr(
        anchor,
        "update_evidence_batch_anchor",
        lambda db_path, anchor_id, **kwargs: updates.append(kwargs),
    )

    class Functions:
        def isRootRegistered(self, root_hash: bytes) -> SimpleNamespace:
            return SimpleNamespace(call=lambda: root_hash == bytes.fromhex(ROOT_HASH))

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            get_transaction_receipt=lambda transaction_hash: {
                "status": 0,
                "blockNumber": 42,
                "gasUsed": 21_000,
            },
            contract=lambda address, abi: SimpleNamespace(functions=Functions()),
        )
    )

    reconciled = anchor.reconcile_evidence_batch_anchor(tmp_path / "invoiceops.db", 4, web3)

    assert reconciled == failed
    assert updates == [{"status": "failed", "block_number": 42, "gas_used": 21_000}]


def test_batch_anchor_timeout_is_persisted_and_reconciled_without_resubmitting(
    tmp_path: Path,
) -> None:
    from invoiceops.anchor import (
        reconcile_evidence_batch_anchor,
        submit_evidence_batch_anchor,
    )
    from invoiceops.domain.policy import recommend_from_probability
    from invoiceops.evidence import (
        EvidenceProvenance,
        EvidenceRecord,
        create_evidence_batch,
        persist_evidence_records,
    )
    from invoiceops.legacy.db import insert_model_evaluation
    from invoiceops.legacy.seed import seed_invoices

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-anchor",
        model_name="invoice-review",
        model_version="7",
        run_id="run-anchor",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(
        db_path,
        [
            EvidenceRecord(
                evaluation_id=1,
                invoice_id="INV-10023",
                correlation_id="corr-anchor",
                model_name="invoice-review",
                model_version="7",
                run_id="run-anchor",
                manual_review_probability="0.8",
                policy_version="ml-policy-v1",
                policy_threshold="0.8",
                recommendation="MANUAL_REVIEW",
                source="model",
                reason="probability_at_or_above_threshold",
                evaluation_created_at="2026-01-01T00:00:00Z",
                provenance=EvidenceProvenance(
                    dataset_version="invoice-risk-v1",
                    feature_schema_version="invoice-features-v1",
                    git_commit="a" * 40,
                ),
            )
        ],
    )
    batch = create_evidence_batch(db_path, [1])

    submitted_transaction_hash = b"submitted-transaction"
    receipt = {
        "blockNumber": 42,
        "gasUsed": 21_000,
        "status": 1,
        "transactionHash": submitted_transaction_hash,
    }
    calls: list[tuple[str, object]] = []
    receipt_available = False

    class Register:
        def transact(self, options: dict[str, str]) -> bytes:
            calls.append(("transact", options))
            return submitted_transaction_hash

    class Functions:
        def registerRoot(self, root_hash: bytes) -> Register:
            calls.append(("register", root_hash))
            return Register()

    def get_transaction_receipt(transaction_hash: bytes) -> dict[str, object] | None:
        return receipt if receipt_available else None

    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            contract=lambda address, abi: SimpleNamespace(functions=Functions()),
            get_transaction_receipt=get_transaction_receipt,
        )
    )
    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)

    submitted = submit_evidence_batch_anchor(
        db_path,
        batch_id=batch.id,
        root_hash=batch.root_hash,
        web3=web3,
        deployment=deployment,
        signer=SIGNER,
    )

    assert submitted.status == "submitted"
    assert submitted.root_hash == batch.root_hash
    assert submitted.chain_id == 31337
    assert submitted.contract_address == ADDRESS
    assert submitted.transaction_hash == submitted_transaction_hash.hex()
    assert submitted.block_number is None
    assert submitted.gas_used is None

    ambiguous = reconcile_evidence_batch_anchor(db_path, submitted.id, web3)

    assert ambiguous.status == "ambiguous"

    receipt_available = True
    reconciled = reconcile_evidence_batch_anchor(db_path, submitted.id, web3)

    assert reconciled.status == "verified"
    assert reconciled.root_hash == batch.root_hash
    assert reconciled.chain_id == 31337
    assert reconciled.contract_address == ADDRESS
    assert reconciled.transaction_hash == submitted_transaction_hash.hex()
    assert reconciled.block_number == 42
    assert reconciled.gas_used == 21_000
    assert reconciled.anchored_at is not None
    assert calls == [
        ("register", bytes.fromhex(batch.root_hash)),
        ("transact", {"from": SIGNER}),
    ]


def test_anchor_evidence_batch_waits_for_the_submitted_receipt_before_reconciling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invoiceops import anchor

    submitted = anchor.EvidenceBatchAnchor(
        id=7,
        batch_id=3,
        root_hash=ROOT_HASH,
        chain_id=31337,
        contract_address=ADDRESS,
        transaction_hash="ab" * 32,
        block_number=None,
        gas_used=None,
        submitted_at="2026-01-01T00:00:00Z",
        anchored_at=None,
        status="submitted",
    )
    calls: list[object] = []
    monkeypatch.setattr(anchor, "submit_evidence_batch_anchor", lambda *args, **kwargs: submitted)
    monkeypatch.setattr(
        anchor,
        "reconcile_evidence_batch_anchor",
        lambda db_path, anchor_id, web3: calls.append((db_path, anchor_id, web3)) or submitted,
    )
    web3 = SimpleNamespace(
        eth=SimpleNamespace(
            wait_for_transaction_receipt=lambda transaction_hash, timeout: calls.append(
                (transaction_hash, timeout)
            )
        )
    )

    result = anchor.anchor_evidence_batch(
        "invoiceops.db",
        batch_id=3,
        root_hash=ROOT_HASH,
        web3=web3,
        deployment=SimpleNamespace(),
        signer=SIGNER,
    )

    assert result == submitted
    assert calls == [(bytes.fromhex("ab" * 32), 5), ("invoiceops.db", 7, web3)]


def test_batch_anchor_keeps_a_registered_root_ambiguous_when_hash_persistence_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from invoiceops import anchor
    from invoiceops.domain.policy import recommend_from_probability
    from invoiceops.evidence import (
        EvidenceProvenance,
        EvidenceRecord,
        create_evidence_batch,
        persist_evidence_records,
    )
    from invoiceops.legacy.db import insert_model_evaluation
    from invoiceops.legacy.seed import seed_invoices

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-anchor-hash-persistence-failure",
        model_name="invoice-review",
        model_version="7",
        run_id="run-anchor-hash-persistence-failure",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(
        db_path,
        [
            EvidenceRecord(
                evaluation_id=1,
                invoice_id="INV-10023",
                correlation_id="corr-anchor-hash-persistence-failure",
                model_name="invoice-review",
                model_version="7",
                run_id="run-anchor-hash-persistence-failure",
                manual_review_probability="0.8",
                policy_version="ml-policy-v1",
                policy_threshold="0.8",
                recommendation="MANUAL_REVIEW",
                source="model",
                reason="probability_at_or_above_threshold",
                evaluation_created_at="2026-01-01T00:00:00Z",
                provenance=EvidenceProvenance(
                    dataset_version="invoice-risk-v1",
                    feature_schema_version="invoice-features-v1",
                    git_commit="a" * 40,
                ),
            )
        ],
    )
    batch = create_evidence_batch(db_path, [1])
    registered_roots: set[bytes] = set()
    calls: list[tuple[str, object]] = []

    class Register:
        def __init__(self, root_hash: bytes) -> None:
            self.root_hash = root_hash

        def transact(self, options: dict[str, str]) -> bytes:
            calls.append(("transact", options))
            registered_roots.add(self.root_hash)
            return b"accepted-before-hash-persistence"

    class Functions:
        def registerRoot(self, root_hash: bytes) -> Register:
            calls.append(("register", root_hash))
            return Register(root_hash)

        def isRootRegistered(self, root_hash: bytes) -> SimpleNamespace:
            calls.append(("query", root_hash))
            return SimpleNamespace(call=lambda: root_hash in registered_roots)

    web3 = SimpleNamespace(
        eth=SimpleNamespace(contract=lambda address, abi: SimpleNamespace(functions=Functions()))
    )
    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)

    def fail_hash_persistence(*args: object) -> None:
        raise LookupError("simulated hash persistence failure")

    monkeypatch.setattr(
        anchor,
        "set_evidence_batch_anchor_transaction",
        fail_hash_persistence,
    )

    with pytest.raises(anchor.AnchorTransactionError, match="simulated hash persistence failure"):
        anchor.submit_evidence_batch_anchor(
            db_path,
            batch_id=batch.id,
            root_hash=batch.root_hash,
            web3=web3,
            deployment=deployment,
            signer=SIGNER,
        )

    reserved = anchor.submit_evidence_batch_anchor(
        db_path,
        batch_id=batch.id,
        root_hash=batch.root_hash,
        web3=web3,
        deployment=deployment,
        signer=SIGNER,
    )
    reconciled = anchor.reconcile_evidence_batch_anchor(db_path, reserved.id, web3)

    assert reserved.status == "ambiguous"
    assert reserved.transaction_hash is None
    assert reconciled.status == "ambiguous"
    assert reconciled.transaction_hash is None
    assert reconciled.block_number is None
    assert reconciled.gas_used is None
    assert reconciled.anchored_at is None
    assert [call for call in calls if call[0] == "transact"] == [("transact", {"from": SIGNER})]


def test_competing_batch_anchor_submissions_transact_once(tmp_path: Path) -> None:
    from invoiceops.anchor import submit_evidence_batch_anchor
    from invoiceops.domain.policy import recommend_from_probability
    from invoiceops.evidence import (
        EvidenceProvenance,
        EvidenceRecord,
        create_evidence_batch,
        persist_evidence_records,
    )
    from invoiceops.legacy.db import insert_model_evaluation
    from invoiceops.legacy.seed import seed_invoices

    db_path = tmp_path / "invoiceops.db"
    seed_invoices(db_path)
    insert_model_evaluation(
        db_path,
        "INV-10023",
        correlation_id="corr-competing-anchor",
        model_name="invoice-review",
        model_version="7",
        run_id="run-competing-anchor",
        manual_review_probability=0.8,
        recommendation=recommend_from_probability(0.8),
    )
    persist_evidence_records(
        db_path,
        [
            EvidenceRecord(
                evaluation_id=1,
                invoice_id="INV-10023",
                correlation_id="corr-competing-anchor",
                model_name="invoice-review",
                model_version="7",
                run_id="run-competing-anchor",
                manual_review_probability="0.8",
                policy_version="ml-policy-v1",
                policy_threshold="0.8",
                recommendation="MANUAL_REVIEW",
                source="model",
                reason="probability_at_or_above_threshold",
                evaluation_created_at="2026-01-01T00:00:00Z",
                provenance=EvidenceProvenance(
                    dataset_version="invoice-risk-v1",
                    feature_schema_version="invoice-features-v1",
                    git_commit="a" * 40,
                ),
            )
        ],
    )
    batch = create_evidence_batch(db_path, [1])
    transact_started = Event()
    release_transaction = Event()
    calls: list[tuple[str, object]] = []

    class Register:
        def transact(self, options: dict[str, str]) -> bytes:
            calls.append(("transact", options))
            transact_started.set()
            assert release_transaction.wait(timeout=1)
            return b"competing-transaction"

    class Functions:
        def registerRoot(self, root_hash: bytes) -> Register:
            calls.append(("register", root_hash))
            return Register()

    web3 = SimpleNamespace(
        eth=SimpleNamespace(contract=lambda address, abi: SimpleNamespace(functions=Functions()))
    )
    deployment = SimpleNamespace(address=ADDRESS, chain_id=31337)
    results: list[object] = []

    def submit() -> None:
        results.append(
            submit_evidence_batch_anchor(
                db_path,
                batch_id=batch.id,
                root_hash=batch.root_hash,
                web3=web3,
                deployment=deployment,
                signer=SIGNER,
            )
        )

    first = Thread(target=submit)
    first.start()
    assert transact_started.wait(timeout=1)
    second = Thread(target=submit)
    second.start()
    second.join(timeout=1)
    release_transaction.set()
    first.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 2
    assert [call for call in calls if call[0] == "transact"] == [("transact", {"from": SIGNER})]
