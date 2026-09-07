# Anclaje de evidencia en Gnosis Chiado

El contrato almacena exclusivamente roots `bytes32`. Su contrato público es `EvidenceRootAnchor`, `registerRoot(bytes32)`, `isRootRegistered(bytes32)` y el evento `RootRegistered(bytes32,address)`.

## Ruta manual con Remix

1. Compilar `contracts/src/EvidenceRootAnchor.sol` con Solidity `0.8.24` y optimizador 200.
2. En Remix, seleccionar Gnosis Chiado mediante un wallet con fondos de testnet obtenidos de un faucet público compatible, sin confiar en uno concreto.
3. Desplegar con la dirección autorizada como argumento `allowedSigner`.
4. Guardar dirección, `chain_id` `10200` y signer en un manifest fuera de secretos; verificar el código y el constructor en Blockscout Chiado.
5. Obtener un root ya verificado desde `evidence_batches`; nunca reconstruirlo ni anclar datos de factura.

## Ruta automatizada con Foundry

### Configuración local inicial

Desde `contracts/`, cree una configuración local ignorada por Git y protéjala. Si ya está dentro de `contracts/`, no ejecute `cd contracts` otra vez: ese segundo cambio de directorio produce el error porque no existe `contracts/contracts`.

```bash
cp .env.example .env
chmod 600 .env
```

`GNOSIS_CHIADO_RPC_URL` selecciona Gnosis Chiado. `PRIVATE_KEY` es la clave que firma y paga el deploy. `EVIDENCE_ROOT_ANCHOR_SIGNER` es solo la dirección que el contrato autoriza para registrar roots. En un laboratorio ambas pueden corresponder a la misma dirección, pero no son semánticamente intercambiables: una firma el despliegue y la otra define la autorización del contrato. Complete los dos valores vacíos sin comillas ni espacios y no copie `.env` a Git, logs o chat.

Cada terminal debe cargar el archivo explícitamente; Foundry no debe depender de una carga implícita:

```bash
set -a; . ./.env; set +a
test -n "$GNOSIS_CHIADO_RPC_URL"
test -n "$PRIVATE_KEY"
test -n "$EVIDENCE_ROOT_ANCHOR_SIGNER"

chain_id="$(cast chain-id --rpc-url "$GNOSIS_CHIADO_RPC_URL")"
test "$chain_id" = "10200"
printf 'RPC Gnosis Chiado confirmado: chain ID %s\n' "$chain_id"

# Muestra solo la dirección pública que firmará y pagará el deploy.
cast wallet address --private-key "$PRIVATE_KEY"
```

Después de compilar y probar, despliegue desde el mismo directorio y la misma terminal:

```bash
forge script script/DeployEvidenceRootAnchor.s.sol:DeployEvidenceRootAnchor \
  --rpc-url "$GNOSIS_CHIADO_RPC_URL" --private-key "$PRIVATE_KEY" --broadcast
```

Foundry puede mostrar una dirección simulada antes del broadcast. Si `--broadcast` termina sin éxito, esa dirección no corresponde a un contrato desplegado on-chain. Solo después de que el comando anterior termine exitosamente, extraiga el address y la transacción del broadcast. Foundry guarda la transacción de deploy en `.hash`, no en `.transactionHash`:

```bash
broadcast="broadcast/DeployEvidenceRootAnchor.s.sol/10200/run-latest.json"
test -f "$broadcast"
CONTRACT_ADDRESS="$(jq -r '.transactions[] | select(.contractName == "EvidenceRootAnchor") | .contractAddress' "$broadcast" | tail -n 1)"
DEPLOY_TX_HASH="$(jq -r '.transactions[] | select(.contractName == "EvidenceRootAnchor") | .hash' "$broadcast" | tail -n 1)"
test -n "$CONTRACT_ADDRESS"
test "$CONTRACT_ADDRESS" != "null"
test -n "$DEPLOY_TX_HASH"
test "$DEPLOY_TX_HASH" != "null"
printf 'CONTRACT_ADDRESS=%s\nDEPLOY_TX_HASH=%s\n' "$CONTRACT_ADDRESS" "$DEPLOY_TX_HASH"

receipt_status="$(cast receipt "$DEPLOY_TX_HASH" status --rpc-url "$GNOSIS_CHIADO_RPC_URL")"
test "$receipt_status" = "1"
runtime_bytecode="$(cast code "$CONTRACT_ADDRESS" --rpc-url "$GNOSIS_CHIADO_RPC_URL")"
test -n "$runtime_bytecode"
test "$runtime_bytecode" != "0x"

constructor_args="$(cast abi-encode 'constructor(address)' "$EVIDENCE_ROOT_ANCHOR_SIGNER")"
verify_source() {
  forge verify-contract --chain-id 10200 \
    --verifier blockscout \
    --verifier-url 'https://gnosis-chiado.blockscout.com/api?' \
    --constructor-args "$constructor_args" \
    "$CONTRACT_ADDRESS" src/EvidenceRootAnchor.sol:EvidenceRootAnchor
}
verify_source || { sleep 60; verify_source; }
```

`cast receipt` con estado `1` y `cast code` con bytecode distinto de `0x` son la prueba canónica de la cadena: la transacción tuvo éxito y la dirección contiene código runtime. Blockscout es un índice secundario; si inicialmente informa que la dirección no es un smart contract, espere exactamente 60 segundos y repita la verificación de source una vez, como hace el bloque anterior. No haga reintentos indefinidos ni otro broadcast.

Para registrar desde InvoiceOps, el signer se carga solo en memoria desde una variable ya inyectada por el mecanismo de secretos aprobado; no requiere cuenta desbloqueada ni escribe una clave en disco. `ROOT_HASH` es una entrada pública proporcionada por el operador: cópiela del batch `verified` ya creado por Portal o del registro de evidencia persistido. Valide su forma antes de cualquier envío; este bloque no imprime la clave:

```bash
: "${INVOICEOPS_ANCHOR_SIGNER_PRIVATE_KEY:?Inyecte la clave del signer mediante el mecanismo de secretos aprobado}"
: "${ROOT_HASH:?Copie la root de 64 hexadecimales minusculas del batch verified}"
: "${REMOTE_MANIFEST:?Defina la ruta del manifest remoto validado fuera del repositorio}"
[[ "$ROOT_HASH" =~ ^[0-9a-f]{64}$ ]] || {
  printf '%s\n' 'ROOT_HASH debe tener exactamente 64 caracteres hexadecimales minusculos.' >&2
  exit 1
}
uv run python -m invoiceops.anchor register \
  --manifest "$REMOTE_MANIFEST" \
  --rpc-url "$GNOSIS_CHIADO_RPC_URL" \
  --signer-env INVOICEOPS_ANCHOR_SIGNER_PRIVATE_KEY \
  --root-hash "$ROOT_HASH"
```

For direct CLI use, start from `contracts/deployments/gnosis-chiado.example.json` and create an operational manifest outside the repository with `contract`, `chain_id`, and `address`; `signer` is optional for that CLI path. The manifest never contains private keys. For a batch, use `batch-anchor` with the same arguments. Preserve the receipt, transaction hash, block number, gas used, and `RootRegistered` event. Reconcile an `ambiguous` result with `batch-reconcile`; never resubmit it.

Anvil `31337` conserva sus comandos y signer desbloqueado actuales. Las pruebas Foundry y Python cubren el contrato y ambas rutas de envío.

## Portal de demostración con destino remoto

El Portal declara dos destinos independientes: `local` (Anvil) y `remote` (cualquier cadena EVM configurada). Chiado `10200` es un ejemplo, no una suposición del código. El destino remoto se habilita solamente cuando el proceso del Portal recibe estas tres variables en runtime:

`INVOICEOPS_REMOTE_ANCHOR_MANIFEST`, `INVOICEOPS_REMOTE_ANCHOR_RPC_URL` e `INVOICEOPS_REMOTE_ANCHOR_PRIVATE_KEY` son entradas de runtime proporcionadas por el operador. No use rutas, hosts o claves de ejemplo. En la topología de producción documentada, Runbook 09 deriva y valida las rutas canónicas, solicita RPC y clave sin mostrarlos, y ejecuta el preflight antes del recreate.

The bridged Remote manifest is public identity data, not a secret. It requires `contract`, integer `chain_id`, `address`, and `signer`; `name` is optional. Generate it after the deployment verification, outside the repository, with the RPC URL and private key kept out of the file:

```bash
REMOTE_MANIFEST="${REMOTE_MANIFEST:?Defina una ruta protegida fuera del repositorio para el manifest remoto}"
umask 077
jq -n \
  --arg name "Gnosis Chiado" \
  --arg contract "EvidenceRootAnchor" \
  --argjson chain_id "$chain_id" \
  --arg address "$CONTRACT_ADDRESS" \
  --arg signer "$EVIDENCE_ROOT_ANCHOR_SIGNER" \
  '{name: $name, contract: $contract, chain_id: $chain_id, address: $address, signer: $signer}' \
  > "$REMOTE_MANIFEST"
jq -e '
  type == "object" and
  .contract == "EvidenceRootAnchor" and
  (.chain_id | type == "number") and
  (.address | type == "string") and
  (.signer | type == "string") and
  ([keys[] | ascii_downcase | contains("private")] | any | not)
' "$REMOTE_MANIFEST" >/dev/null
```

Set `INVOICEOPS_REMOTE_ANCHOR_MANIFEST` to `REMOTE_MANIFEST`; set `INVOICEOPS_REMOTE_ANCHOR_RPC_URL` separately. The application accepts a valid lowercase address in the manifest and resolves it to EIP-55 checksum casing before RPC calls. The private key never belongs in the manifest, SQLite, HTML, browser, logs, or error messages. The Portal validates the manifest, RPC, chain identity, contract code, and signer authorization before issuing the challenge and again before broadcast. If any requirement is missing, the button remains disabled with the cause and never falls back to Anvil.

Esta firma directa es una excepción limitada al demo desechable. Para MLOps o despliegues duraderos, sustituir la variable por el mecanismo de secretos aprobado y un límite de custodia/rotación; no reutilizar este flujo como diseño de producción.

## Reset State

El deploy, el registro de roots, receipts y saldo de testnet son estado externo irreversible: no existe un comando de reset. Para volver a ejecutar la preparación local sin conservar secretos en la shell, cierre la terminal o ejecute:

```bash
unset GNOSIS_CHIADO_RPC_URL PRIVATE_KEY EVIDENCE_ROOT_ANCHOR_SIGNER \
  INVOICEOPS_ANCHOR_SIGNER_PRIVATE_KEY ROOT_HASH REMOTE_MANIFEST
test -z "${PRIVATE_KEY:-}" && test -z "${INVOICEOPS_ANCHOR_SIGNER_PRIVATE_KEY:-}"
```

Si `contracts/.env` fue creado solo para este laboratorio, elimínelo con `rm -f contracts/.env` desde la raíz del repositorio y confirme `test ! -e contracts/.env`. No borre manifests operacionales ni broadcasts que puedan ser evidencia de un deploy; siga la limpieza con comprobación de propiedad de `07` o `08` cuando esos runbooks sean los propietarios.
