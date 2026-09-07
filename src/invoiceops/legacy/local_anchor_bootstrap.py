"""Deploy the disposable Anvil anchor used by local Compose profiles."""

import os
import shutil
from pathlib import Path

from invoiceops.anchor import LOCAL_CHAIN_ID, chain, deploy_anchor, local_signer


def main() -> None:
    source_manifest = Path("/app/contracts/deployments/local.json")
    destination = Path(os.environ["INVOICEOPS_LOCAL_ANCHOR_MANIFEST"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    web3 = chain(os.environ["INVOICEOPS_EVM_RPC_URL"], expected_chain_id=LOCAL_CHAIN_ID)
    deploy_anchor(
        web3,
        local_signer(web3),
        manifest_path=source_manifest,
        rpc_url=os.environ["INVOICEOPS_EVM_RPC_URL"],
    )
    shutil.copyfile(source_manifest, destination)
    destination.chmod(0o644)


if __name__ == "__main__":
    main()
