"""Anchor C3-T03 Merkle roots on the local Anvil chain."""

import argparse
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

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
class AnchorDeployment:
    contract: str
    chain_id: int
    address: str
    signer: str | None = None


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
        raise AnchorSignerError("local EVM has no unlocked account; start Anvil with its default accounts")
    return str(accounts[0])


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
        raise AnchorConfigurationError("deployment manifest is missing address; run anchor deploy first")
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


def is_root_registered(web3: Web3, address: str, root_hash: str) -> bool:
    """Return whether the existing C3-T03 root has been anchored."""
    return bool(_contract(web3, address).functions.isRootRegistered(root_hash_bytes(root_hash)).call())


def register_root(web3: Web3, address: str, signer: str, root_hash: str) -> Any:
    """Register one root using Anvil's unlocked local signer and wait for finality."""
    try:
        transaction_hash = _contract(web3, address).functions.registerRoot(root_hash_bytes(root_hash)).transact(
            {"from": signer}
        )
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
        raise AnchorDeploymentError("Forge is not installed; install Foundry before deploying the anchor")
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
    parser = argparse.ArgumentParser(description="Anchor C3-T03 Merkle roots on local Anvil.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("register", "query", "status"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
        command_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
        command_parser.add_argument("--root-hash", required=True)
    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    deploy_parser.add_argument("--rpc-url", default=LOCAL_RPC_URL)
    args = parser.parse_args()
    try:
        if args.command == "deploy":
            payload = _read_manifest(args.manifest, require_address=False)
            web3 = chain(args.rpc_url, expected_chain_id=int(payload["chain_id"]))
            result: object = asdict(deploy_anchor(web3, local_signer(web3), manifest_path=args.manifest, rpc_url=args.rpc_url))
        else:
            deployment = resolve_deployment(args.manifest)
            web3 = chain(args.rpc_url, expected_chain_id=deployment.chain_id)
            signer = local_signer(web3)
            registered = is_root_registered(web3, deployment.address, args.root_hash)
            if args.command == "register" and not registered:
                receipt = register_root(web3, deployment.address, signer, args.root_hash)
                registered = True
                result = {
                    "address": deployment.address,
                    "chain_id": deployment.chain_id,
                    "registered": registered,
                    "signer": signer,
                    "transaction_hash": Web3.to_hex(receipt["transactionHash"]),
                }
            else:
                result = {
                    "address": deployment.address,
                    "chain_id": deployment.chain_id,
                    "registered": registered,
                    "signer": signer,
                }
    except AnchorError as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
