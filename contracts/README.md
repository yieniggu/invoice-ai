# Local EVM contracts

`EvidenceRootAnchor` anchors only a C3-T03 Merkle root (`bytes32`) on the local
Anvil chain. It does not receive Evidence Records, proofs, invoices, ML data,
policy decisions, or private keys.

The contract allows only the signer configured at deployment to register roots.
Duplicate roots revert. The `RootRegistered(bytes32,address)` event is the
minimal on-chain audit signal.

Run the Solidity tests from the repository root:

```bash
forge test --root contracts
```

Use `invoiceops.anchor` for deployment and interaction. It owns the ABI,
manifest resolution, RPC validation, and local signer handling so notebooks and
the CLI do not duplicate these concerns.
