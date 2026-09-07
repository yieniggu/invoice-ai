"""Configured Portal anchor targets and their server-side preflight checks."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from invoiceops.anchor import (
    LOCAL_CHAIN_ID,
    LOCAL_RPC_URL,
    AnchorConfigurationError,
    AnchorDeployment,
    AnchorError,
    RemoteSigner,
    chain,
    is_root_registered,
    local_signer,
    remote_signer_from_environment,
    resolve_deployment,
)
from invoiceops.evidence import EvidenceBatch

AnchorTargetName = Literal["local", "remote"]
REMOTE_PRIVATE_KEY_VARIABLE = "INVOICEOPS_REMOTE_ANCHOR_PRIVATE_KEY"


@dataclass(frozen=True)
class AnchorTarget:
    name: AnchorTargetName
    label: str
    manifest_variable: str
    rpc_url: str | None
    manifest_path: str | None
    deployment: AnchorDeployment | None
    reason: str | None

    def public_metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "label": self.label,
            "rpc_url": self.rpc_url,
            "chain_id": self.deployment.chain_id if self.deployment else None,
            "contract_address": self.deployment.address if self.deployment else None,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AnchorPreflight:
    target: AnchorTarget
    web3: object
    deployment: AnchorDeployment
    signer: str | RemoteSigner

    @property
    def public_metadata(self) -> dict[str, object]:
        return {
            **self.target.public_metadata(),
            "chain_id": self.deployment.chain_id,
            "contract_address": self.deployment.address,
            "signer": self.signer.address if isinstance(self.signer, RemoteSigner) else self.signer,
        }


def configured_anchor_targets(batch: EvidenceBatch, anchored_targets: set[str]) -> list[AnchorTarget]:
    """Return public target metadata without reading or exposing private key values."""
    return [
        _target("local", batch, "local" in anchored_targets),
        _target("remote", batch, "remote" in anchored_targets),
    ]


def preflight_anchor_target(
    target_name: AnchorTargetName, batch: EvidenceBatch, has_anchor: bool
) -> AnchorPreflight:
    """Revalidate target configuration and chain state immediately before confirmation."""
    target = _target(target_name, batch, has_anchor)
    if target.reason:
        raise AnchorConfigurationError(target.reason)
    assert target.deployment is not None
    assert target.rpc_url is not None
    web3 = chain(target.rpc_url, expected_chain_id=target.deployment.chain_id)
    code = web3.eth.get_code(target.deployment.address)
    if not code or bytes(code) == b"":
        raise AnchorConfigurationError("Anchor contract is not deployed at the configured address.")
    signer: str | RemoteSigner
    if target.name == "local":
        signer = local_signer(web3)
        if target.deployment.signer is None:
            raise AnchorConfigurationError("Local anchor manifest must contain the deployed signer.")
        if signer.lower() != target.deployment.signer.lower():
            raise AnchorConfigurationError("Local Anvil signer does not match the deployment manifest.")
    else:
        try:
            signer = remote_signer_from_environment(web3, REMOTE_PRIVATE_KEY_VARIABLE)
        except AnchorError as error:
            raise AnchorConfigurationError("Remote anchor signer configuration is invalid.") from error
        if target.deployment.signer is not None and signer.address.lower() != target.deployment.signer.lower():
            raise AnchorConfigurationError("Remote signer does not match the deployment manifest.")
    authorized_signer = web3.eth.contract(
        address=target.deployment.address,
        abi=[
            {
                "type": "function",
                "name": "signer",
                "inputs": [],
                "outputs": [{"name": "", "type": "address"}],
                "stateMutability": "view",
            }
        ],
    ).functions.signer().call()
    signer_address = signer.address if isinstance(signer, RemoteSigner) else signer
    if signer_address.lower() != str(authorized_signer).lower():
        raise AnchorConfigurationError(f"{target.label} signer is not authorized by the anchor contract.")
    if is_root_registered(web3, target.deployment.address, batch.root_hash):
        raise AnchorConfigurationError(
            "Root is already registered; reconcile the existing anchor instead of resubmitting."
        )
    return AnchorPreflight(target, web3, target.deployment, signer)


def _target(target_name: AnchorTargetName, batch: EvidenceBatch, has_anchor: bool) -> AnchorTarget:
    if batch.status != "verified":
        return _unready(target_name, "Only a verified batch can be anchored.")
    if has_anchor:
        return _unready(
            target_name,
            f"This batch already has a {target_name} anchor lifecycle; use its recorded status for recovery.",
        )
    if target_name == "local":
        manifest_variable = "INVOICEOPS_LOCAL_ANCHOR_MANIFEST"
        manifest_path = os.getenv(manifest_variable)
        if not manifest_path:
            return _unready(target_name, "Local anchor deployment manifest is not configured.")
        return _deployment_target(
            target_name,
            manifest_variable,
            manifest_path,
            os.getenv("INVOICEOPS_LOCAL_ANCHOR_RPC_URL", LOCAL_RPC_URL),
        )
    manifest_variable = "INVOICEOPS_REMOTE_ANCHOR_MANIFEST"
    manifest_path = os.getenv(manifest_variable)
    if not manifest_path:
        return _unready(target_name, "Remote anchor deployment manifest is not configured.")
    rpc_url = os.getenv("INVOICEOPS_REMOTE_ANCHOR_RPC_URL")
    if not rpc_url:
        return _unready(target_name, "Remote anchor RPC URL is not configured.")
    parsed = urlparse(rpc_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return _unready(target_name, "Remote anchor RPC URL is invalid.")
    if not os.getenv(REMOTE_PRIVATE_KEY_VARIABLE):
        return _unready(target_name, "Remote anchor private key is not configured.")
    return _deployment_target(target_name, manifest_variable, manifest_path, rpc_url)


def _deployment_target(
    target_name: AnchorTargetName, manifest_variable: str, manifest_path: str, rpc_url: str
) -> AnchorTarget:
    try:
        deployment = resolve_deployment(Path(manifest_path))
    except AnchorError as error:
        return _unready(target_name, f"Anchor deployment manifest is invalid: {error}", rpc_url, manifest_path)
    if target_name == "local" and deployment.chain_id != LOCAL_CHAIN_ID:
        return _unready(
            target_name,
            f"Local anchor manifest must use chain ID {LOCAL_CHAIN_ID}.",
            rpc_url,
            manifest_path,
            deployment,
        )
    return AnchorTarget(
        target_name,
        "Local Anvil" if target_name == "local" else _remote_label(deployment),
        manifest_variable,
        rpc_url,
        manifest_path,
        deployment,
        None,
    )


def _remote_label(deployment: AnchorDeployment) -> str:
    return deployment.name or f"Remote chain {deployment.chain_id}"


def _unready(
    target_name: AnchorTargetName,
    reason: str,
    rpc_url: str | None = None,
    manifest_path: str | None = None,
    deployment: AnchorDeployment | None = None,
) -> AnchorTarget:
    return AnchorTarget(
        target_name,
        "Local Anvil" if target_name == "local" else "Configured remote chain",
        "INVOICEOPS_LOCAL_ANCHOR_MANIFEST"
        if target_name == "local"
        else "INVOICEOPS_REMOTE_ANCHOR_MANIFEST",
        rpc_url,
        manifest_path,
        deployment,
        reason,
    )
