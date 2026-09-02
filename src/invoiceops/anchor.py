"""Anchor C3-T03 Merkle roots on Anvil or a configured remote EVM chain."""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, TransactionNotFound

from invoiceops.legacy.db import (
    get_evidence_batch as get_evidence_batch_row,
)
from invoiceops.legacy.db import (
    get_evidence_batch_anchor,
    get_latest_evidence_batch_anchor,
    insert_evidence_batch_anchor,
    set_evidence_batch_anchor_transaction,
    update_evidence_batch_anchor,
)

LOCAL_CHAIN_ID = 31337
LOCAL_RPC_URL = "http://127.0.0.1:8545"
DEFAULT_MANIFEST_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "deployments" / "local.json"
)

ANCHOR_ABI: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "registerRoot",
        "inputs": [{"name": "rootHash", "type": "bytes32"}],
        "outputs": [],
        "stateMutability": "nonpayable",
    },
    {
        "type": "function",
        "name": "isRootRegistered",
        "inputs": [{"name": "rootHash", "type": "bytes32"}],
        "outputs": [{"name": "", "type": "bool"}],
        "stateMutability": "view",
    },
    {
        "type": "event",
        "name": "RootRegistered",
        "anonymous": False,
        "inputs": [
            {"name": "rootHash", "type": "bytes32", "indexed": True},
            {"name": "signer", "type": "address", "indexed": True},
        ],
    },
]


class AnchorError(RuntimeError):
    """Base class for actionable local EVM anchoring errors."""


class AnchorConfigurationError(AnchorError):
    """Raised when local EVM configuration is incomplete or invalid."""


class AnchorRpcError(AnchorError):
    """Raised when the local Anvil RPC cannot be used."""


class AnchorSignerError(AnchorError):
    """Raised when Anvil has no unlocked local sender."""


class AnchorDeploymentError(AnchorError):
    """Raised when Forge cannot deploy or report the anchor contract."""


class AnchorTransactionError(AnchorError):
    """Raised when root registration is rejected or does not complete."""


@dataclass(frozen=True)
class EvidenceBatchAnchor:
    id: int
    batch_id: int
    root_hash: str
    chain_id: int
    contract_address: str
    transaction_hash: str | None
    block_number: int | None
    gas_used: int | None
    submitted_at: str
    anchored_at: str | None
    status: str


@dataclass(frozen=True)
class AnchorDeployment:
    contract: str
    chain_id: int
    address: str
    signer: str | None = None


@dataclass(frozen=True)
class RemoteSigner:
    """An in-memory key supplied by an injected environment variable."""

    address: str
    private_key: str


def root_hash_bytes(root_hash: str) -> bytes:
    """Validate and encode the existing lowercase C3-T03 root without rebuilding it."""
    if not isinstance(root_hash, str) or len(root_hash) != 64:
        raise AnchorConfigurationError("root_hash must be a 64 lowercase hexadecimal C3-T03 root")
    try:
        encoded = bytes.fromhex(root_hash)
    except ValueError as error:
        raise AnchorConfigurationError(
            "root_hash must be a 64 lowercase hexadecimal C3-T03 root"
        ) from error
    if encoded.hex() != root_hash:
        raise AnchorConfigurationError("root_hash must be a 64 lowercase hexadecimal C3-T03 root")
    return encoded


def chain(rpc_url: str = LOCAL_RPC_URL, *, expected_chain_id: int | None = LOCAL_CHAIN_ID) -> Web3:
    """Connect to Anvil and verify the configured chain before any contract action."""
    web3 = Web3(Web3.HTTPProvider(rpc_url))
    if not web3.is_connected():
        raise AnchorRpcError(f"cannot connect to local EVM RPC at {rpc_url}; start Anvil first")
    actual_chain_id = chain_id(web3)
    if expected_chain_id is not None and actual_chain_id != expected_chain_id:
        raise AnchorConfigurationError(
            f"expected chain ID {expected_chain_id}, got {actual_chain_id}; check RPC URL and manifest"
        )
    return web3


def chain_id(web3: Web3) -> int:
    """Return the connected chain ID as an integer."""
    return int(web3.eth.chain_id)


def local_signer(web3: Web3) -> str:
    """Return Anvil's first unlocked account without accepting or storing a private key."""
    accounts = web3.eth.accounts
    if not accounts:
        raise AnchorSignerError(
            "local EVM has no unlocked account; start Anvil with its default accounts"
        )
    return str(accounts[0])


def remote_signer_from_environment(web3: Web3, variable_name: str) -> RemoteSigner:
    """Load a remote signing key without persisting it to disk or a manifest."""
    private_key = os.getenv(variable_name)
    if not private_key:
        raise AnchorSignerError(f"remote signer variable is not set: {variable_name}")
    try:
        account = web3.eth.account.from_key(private_key)
    except (TypeError, ValueError) as error:
        raise AnchorSignerError(f"remote signer variable is invalid: {variable_name}") from error
    return RemoteSigner(address=str(account.address), private_key=private_key)


def _read_manifest(path: Path, *, require_address: bool) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as error:
        raise AnchorConfigurationError(f"deployment manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise AnchorConfigurationError(f"deployment manifest is invalid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise AnchorConfigurationError("deployment manifest must be a JSON object")
    for field in ("contract", "chain_id"):
        if field not in payload:
            raise AnchorConfigurationError(f"deployment manifest is missing {field}")
    if require_address and not payload.get("address"):
        raise AnchorConfigurationError(
            "deployment manifest is missing address; run anchor deploy first"
        )
    return payload


def resolve_deployment(path: str | Path = DEFAULT_MANIFEST_PATH) -> AnchorDeployment:
    """Resolve a deployed anchor address from the manifest, never from CLI output."""
    manifest_path = Path(path)
    payload = _read_manifest(manifest_path, require_address=True)
    contract = payload["contract"]
    configured_chain_id = payload["chain_id"]
    address = payload["address"]
    signer = payload.get("signer")
    if contract != "EvidenceRootAnchor":
        raise AnchorConfigurationError("deployment manifest contract must be EvidenceRootAnchor")
    if isinstance(configured_chain_id, bool) or not isinstance(configured_chain_id, int):
        raise AnchorConfigurationError("deployment manifest chain_id must be an integer")
    if not isinstance(address, str) or not Web3.is_address(address):
        raise AnchorConfigurationError("deployment manifest address must be an Ethereum address")
    if signer is not None and (not isinstance(signer, str) or not Web3.is_address(signer)):
        raise AnchorConfigurationError("deployment manifest signer must be an Ethereum address")
    return AnchorDeployment(contract, configured_chain_id, address, signer)


def _contract(web3: Web3, address: str) -> Any:
    return web3.eth.contract(address=Web3.to_checksum_address(address), abi=ANCHOR_ABI)


def _submit_root_transaction(
    web3: Web3, address: str, signer: str | RemoteSigner, root_hash: str
) -> Any:
    registration = _contract(web3, address).functions.registerRoot(root_hash_bytes(root_hash))
    if isinstance(signer, str):
        return registration.transact({"from": signer})
    try:
        transaction = registration.build_transaction(
            {
                "from": signer.address,
                "chainId": chain_id(web3),
                "nonce": web3.eth.get_transaction_count(signer.address),
            }
        )
        signed = web3.eth.account.sign_transaction(transaction, signer.private_key)
        raw_transaction = getattr(signed, "raw_transaction", None)
        if raw_transaction is None:
            raw_transaction = signed.rawTransaction
        return web3.eth.send_raw_transaction(raw_transaction)
    except (AttributeError, TypeError, ValueError) as error:
        raise AnchorTransactionError(
            "remote root registration could not be signed or submitted"
        ) from error


def is_root_registered(web3: Web3, address: str, root_hash: str) -> bool:
    """Return whether the existing C3-T03 root has been anchored."""
    return bool(
        _contract(web3, address).functions.isRootRegistered(root_hash_bytes(root_hash)).call()
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _transaction_hash_hex(transaction_hash: object) -> str:
    if isinstance(transaction_hash, bytes):
        return transaction_hash.hex()
    if isinstance(transaction_hash, str):
        return transaction_hash.removeprefix("0x")
    try:
        return bytes(transaction_hash).hex()
    except (TypeError, ValueError) as error:
        raise AnchorTransactionError(
            "transaction submission did not return a transaction hash"
        ) from error


def _anchor_from_row(row: Any) -> EvidenceBatchAnchor:
    return EvidenceBatchAnchor(
        id=row["id"],
        batch_id=row["batch_id"],
        root_hash=row["root_hash"],
        chain_id=row["chain_id"],
        contract_address=row["contract_address"],
        transaction_hash=row["transaction_hash"],
        block_number=row["block_number"],
        gas_used=row["gas_used"],
        submitted_at=row["submitted_at"],
        anchored_at=row["anchored_at"],
        status=row["status"],
    )


def inspect_evidence_batch_anchor(db_path: str | Path, anchor_id: int) -> EvidenceBatchAnchor:
    row = get_evidence_batch_anchor(db_path, anchor_id)
    if row is None:
        raise AnchorTransactionError(f"evidence batch anchor not found: {anchor_id}")
    return _anchor_from_row(row)


def submit_evidence_batch_anchor(
    db_path: str | Path,
    *,
    batch_id: int,
    root_hash: str,
    web3: Web3,
    deployment: AnchorDeployment,
    signer: str | RemoteSigner,
) -> EvidenceBatchAnchor:
    """Durably reserve the verified batch before submitting its canonical transaction."""
    root_hash_bytes(root_hash)
    batch = get_evidence_batch_row(db_path, batch_id)
    if batch is None:
        raise AnchorTransactionError(f"evidence batch not found: {batch_id}")
    if batch["status"] != "verified" or batch["root_hash"] != root_hash:
        raise AnchorTransactionError("evidence batch is not the verified canonical root")
    try:
        anchor_id = insert_evidence_batch_anchor(
            db_path,
            batch_id=batch_id,
            root_hash=root_hash,
            chain_id=deployment.chain_id,
            contract_address=deployment.address,
            transaction_hash=None,
            submitted_at=_utc_now(),
            status="ambiguous",
        )
    except sqlite3.IntegrityError:
        existing = get_latest_evidence_batch_anchor(db_path, batch_id)
        if existing is None:
            raise AnchorTransactionError("evidence batch anchor reservation was not persisted")
        existing_anchor = _anchor_from_row(existing)
        if existing_anchor.root_hash != root_hash:
            raise AnchorTransactionError(
                "evidence batch already has an anchor for a different root"
            )
        return existing_anchor
    except (LookupError, ValueError) as error:
        raise AnchorTransactionError(str(error)) from error
    try:
        transaction_hash = _submit_root_transaction(web3, deployment.address, signer, root_hash)
    except (ContractLogicError, ValueError) as error:
        if is_root_registered(web3, deployment.address, root_hash):
            raise AnchorTransactionError(
                "root is already registered without a persisted anchor; reconcile the prior transaction"
            ) from error
        raise AnchorTransactionError("root registration transaction was rejected") from error
    try:
        set_evidence_batch_anchor_transaction(
            db_path, anchor_id, _transaction_hash_hex(transaction_hash)
        )
    except LookupError as error:
        raise AnchorTransactionError(str(error)) from error
    return inspect_evidence_batch_anchor(db_path, anchor_id)


def _receipt_registers_root(web3: Web3, address: str, receipt: Any, root_hash: str) -> bool:
    """Validate the contract event when the provider exposes decoded event support."""
    events = getattr(_contract(web3, address), "events", None)
    root_registered = getattr(events, "RootRegistered", None)
    if root_registered is None:
        return True
    logs = root_registered().process_receipt(receipt)
    return any(_transaction_hash_hex(log["args"]["rootHash"]) == root_hash for log in logs)


def reconcile_evidence_batch_anchor(
    db_path: str | Path, anchor_id: int, web3: Web3
) -> EvidenceBatchAnchor:
    """Resolve a reserved anchor from its transaction hash; never resubmit."""
    anchor = inspect_evidence_batch_anchor(db_path, anchor_id)
    if anchor.status in {"verified", "failed"}:
        return anchor
    if anchor.transaction_hash is None:
        try:
            is_root_registered(web3, anchor.contract_address, anchor.root_hash)
        except (AttributeError, ValueError):
            pass
        # Root presence cannot identify the submitted transaction or its receipt.
        update_evidence_batch_anchor(db_path, anchor_id, status="ambiguous")
        return inspect_evidence_batch_anchor(db_path, anchor_id)
    try:
        receipt = web3.eth.get_transaction_receipt(bytes.fromhex(anchor.transaction_hash))
    except (TimeExhausted, TransactionNotFound, ValueError):
        update_evidence_batch_anchor(db_path, anchor_id, status="ambiguous")
        return inspect_evidence_batch_anchor(db_path, anchor_id)
    if receipt is None:
        update_evidence_batch_anchor(db_path, anchor_id, status="ambiguous")
        return inspect_evidence_batch_anchor(db_path, anchor_id)
    if int(receipt["status"]) == 1:
        if not _receipt_registers_root(web3, anchor.contract_address, receipt, anchor.root_hash):
            update_evidence_batch_anchor(db_path, anchor_id, status="ambiguous")
            return inspect_evidence_batch_anchor(db_path, anchor_id)
        update_evidence_batch_anchor(
            db_path,
            anchor_id,
            status="verified",
            block_number=int(receipt["blockNumber"]),
            gas_used=int(receipt["gasUsed"]),
            anchored_at=_utc_now(),
        )
    else:
        update_evidence_batch_anchor(
            db_path,
            anchor_id,
            status="failed",
            block_number=int(receipt["blockNumber"]),
            gas_used=int(receipt["gasUsed"]),
        )
    return inspect_evidence_batch_anchor(db_path, anchor_id)


def anchor_evidence_batch(
    db_path: str | Path,
    *,
    batch_id: int,
    root_hash: str,
    web3: Web3,
    deployment: AnchorDeployment,
    signer: str | RemoteSigner,
) -> EvidenceBatchAnchor:
    """Submit and reconcile a canonical evidence batch using the persisted transaction identity."""
    submitted = submit_evidence_batch_anchor(
        db_path,
        batch_id=batch_id,
        root_hash=root_hash,
        web3=web3,
        deployment=deployment,
        signer=signer,
    )
    if submitted.transaction_hash is not None:
        try:
            # Anvil may accept a transaction before its receipt is queryable on the next RPC call.
            web3.eth.wait_for_transaction_receipt(
                bytes.fromhex(submitted.transaction_hash), timeout=5
            )
        except (AttributeError, TimeExhausted, TransactionNotFound, ValueError):
            pass
    return reconcile_evidence_batch_anchor(db_path, submitted.id, web3)


def register_root(web3: Web3, address: str, signer: str | RemoteSigner, root_hash: str) -> Any:
    """Register one root with an unlocked local signer or an injected remote signer."""
    try:
        transaction_hash = _submit_root_transaction(web3, address, signer, root_hash)
        receipt = web3.eth.wait_for_transaction_receipt(transaction_hash)
    except (ContractLogicError, TimeExhausted, ValueError) as error:
        raise AnchorTransactionError(
            "root registration was rejected or timed out; verify signer, deployment and duplicate root"
        ) from error
    if int(receipt["status"]) != 1:
        raise AnchorTransactionError("root registration transaction reverted")
    return receipt


def deploy_anchor(
    web3: Web3,
    signer: str,
    *,
    manifest_path: str | Path = DEFAULT_MANIFEST_PATH,
    rpc_url: str = LOCAL_RPC_URL,
) -> AnchorDeployment:
    """Deploy with Forge and persist its machine-readable address in the manifest."""
    if shutil.which("forge") is None:
        raise AnchorDeploymentError(
            "Forge is not installed; install Foundry before deploying the anchor"
        )
    path = Path(manifest_path)
    payload = _read_manifest(path, require_address=False)
    if payload["chain_id"] != chain_id(web3):
        raise AnchorConfigurationError("manifest chain_id does not match the connected local EVM")
    command = [
        "forge",
        "create",
        "--root",
        str(path.parent.parent),
        "--rpc-url",
        rpc_url,
        "--unlocked",
        "--from",
        signer,
        "--broadcast",
        "--json",
        "src/EvidenceRootAnchor.sol:EvidenceRootAnchor",
        "--constructor-args",
        signer,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AnchorDeploymentError(
            f"Forge deployment failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    try:
        result = json.loads(completed.stdout)
        address = result["deployedTo"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise AnchorDeploymentError("Forge did not return a deployment address as JSON") from error
    if not isinstance(address, str) or not Web3.is_address(address):
        raise AnchorDeploymentError("Forge returned an invalid deployment address")
    payload.update({"address": address, "signer": signer})
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return resolve_deployment(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Anchor C3-T03 Merkle roots on a configured EVM chain."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("register", "query", "status"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
        command_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
        command_parser.add_argument("--root-hash", required=True)
        if command == "register":
            command_parser.add_argument(
                "--signer-env",
                help="environment variable containing an injected remote private key",
            )
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    deploy_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
    batch_anchor_parser = subparsers.add_parser("batch-anchor")
    batch_anchor_parser.add_argument("--db", required=True, type=Path)
    batch_anchor_parser.add_argument("--batch-id", required=True, type=int)
    batch_anchor_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    batch_anchor_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
    batch_anchor_parser.add_argument(
        "--signer-env", help="environment variable containing an injected remote private key"
    )
    batch_status_parser = subparsers.add_parser("batch-status")
    batch_status_parser.add_argument("--db", required=True, type=Path)
    batch_status_parser.add_argument("--anchor-id", required=True, type=int)
    batch_reconcile_parser = subparsers.add_parser("batch-reconcile")
    batch_reconcile_parser.add_argument("--db", required=True, type=Path)
    batch_reconcile_parser.add_argument("--anchor-id", required=True, type=int)
    batch_reconcile_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    batch_reconcile_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
    args = parser.parse_args()
    try:
        if args.command == "deploy":
            payload = _read_manifest(args.manifest, require_address=False)
            web3 = chain(args.rpc_url, expected_chain_id=int(payload["chain_id"]))
            result: object = asdict(
                deploy_anchor(
                    web3, local_signer(web3), manifest_path=args.manifest, rpc_url=args.rpc_url
                )
            )
        elif args.command == "batch-status":
            result = asdict(inspect_evidence_batch_anchor(args.db, args.anchor_id))
        elif args.command in {"batch-anchor", "batch-reconcile"}:
            deployment = resolve_deployment(args.manifest)
            web3 = chain(args.rpc_url, expected_chain_id=deployment.chain_id)
            if args.command == "batch-anchor":
                batch = get_evidence_batch_row(args.db, args.batch_id)
                if batch is None:
                    raise AnchorTransactionError(f"evidence batch not found: {args.batch_id}")
                result = asdict(
                    anchor_evidence_batch(
                        args.db,
                        batch_id=args.batch_id,
                        root_hash=batch["root_hash"],
                        web3=web3,
                        deployment=deployment,
                        signer=(
                            remote_signer_from_environment(web3, args.signer_env)
                            if args.signer_env
                            else local_signer(web3)
                        ),
                    )
                )
            else:
                result = asdict(reconcile_evidence_batch_anchor(args.db, args.anchor_id, web3))
        else:
            deployment = resolve_deployment(args.manifest)
            web3 = chain(args.rpc_url, expected_chain_id=deployment.chain_id)
            signer = (
                remote_signer_from_environment(web3, args.signer_env)
                if args.command == "register" and args.signer_env
                else local_signer(web3)
            )
            registered = is_root_registered(web3, deployment.address, args.root_hash)
            if args.command == "register" and not registered:
                receipt = register_root(web3, deployment.address, signer, args.root_hash)
                registered = True
                result = {
                    "address": deployment.address,
                    "chain_id": deployment.chain_id,
                    "registered": registered,
                    "signer": signer.address if isinstance(signer, RemoteSigner) else signer,
                    "transaction_hash": Web3.to_hex(receipt["transactionHash"]),
                }
            else:
                result = {
                    "address": deployment.address,
                    "chain_id": deployment.chain_id,
                    "registered": registered,
                    "signer": signer.address if isinstance(signer, RemoteSigner) else signer,
                }
    except AnchorError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
