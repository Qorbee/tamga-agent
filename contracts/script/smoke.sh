#!/usr/bin/env bash
# One payment's life on the real chain: a throwaway PolicyVault on Arc testnet, paid from and to the
# wallet that runs this. Foundry's own EVM does not run Arc's rules (USDC is the native balance, and
# its blocklist is enforced), so every step is executed by Arc's node: the transactions with
# `cast send`, the refusals with `cast call`.
#
#   ETH_RPC_URL=… USDC_ADDRESS=… SMOKE_PRIVATE_KEY=… contracts/script/smoke.sh
#
# The wallet needs about 0.2 test USDC: 0.05 goes through the vault, the rest is gas. Nothing
# deployed here is used again; the vault Tamga pays from is deployed by
# backend/scripts/deploy_policy_vault.py.
set -euo pipefail

: "${ETH_RPC_URL:?the Arc RPC endpoint}"
: "${USDC_ADDRESS:?USDC's ERC-20 interface on that chain}"
: "${SMOKE_PRIVATE_KEY:?a funded test wallet's key}"
cd "$(dirname "$0")/.."

key=(--private-key "$SMOKE_PRIVATE_KEY")
me="$(cast wallet address "${key[@]}")"
chain="$(cast chain-id)"
stranger=0x000000000000000000000000000000000000dEaD
company="$(cast keccak "smoke-$(date +%s)")"
invoice="$(cast keccak invoice-1)"
timelock=60

# A transaction that must succeed; prints its hash.
send() {
  local receipt
  receipt="$(cast send "${key[@]}" --json "$@")"
  [[ "$(jq -r .status <<<"$receipt")" == "0x1" ]] || { echo "failed: $*" >&2; exit 1; }
  jq -r .transactionHash <<<"$receipt"
}

# A call the vault must refuse with the named error.
refused() {
  local error="$1" output
  shift
  if output="$(cast call --from "$me" "$vault" "$@" 2>&1)"; then
    echo "not refused, expected $error: $*" >&2
    exit 1
  fi
  local selector
  selector="$(cast sig "$error")"
  [[ "$output" == *"${selector#0x}"* || "$output" == *"${error%%(*}"* ]] \
    || { echo "refused, but not with $error: $output" >&2; exit 1; }
  echo "refused with ${error%%(*}"
}

echo "wallet $me on chain $chain"
echo "USDC before: $(cast call "$USDC_ADDRESS" 'balanceOf(address)(uint256)' "$me")"

deployed="$(forge create src/PolicyVault.sol:PolicyVault "${key[@]}" --broadcast --json \
  --constructor-args "$USDC_ADDRESS" "$me" "$me" "$timelock")"
vault="$(jq -r .deployedTo <<<"$deployed")"
echo "vault $vault (deploy $(jq -r .transactionHash <<<"$deployed"))"

# Per payment 0.02 USDC, per day 0.03; this wallet is admin, executor, owner and the one payee.
echo "register  $(send "$vault" 'registerCompany(bytes32,address,uint256,uint256,uint256,address[])' \
  "$company" "$me" "$timelock" 20000 30000 "[$me]")"
echo "approve   $(send "$USDC_ADDRESS" 'approve(address,uint256)' "$vault" 50000)"
echo "deposit   $(send "$vault" 'deposit(bytes32,uint256)' "$company" 50000)"
echo "release   $(send "$vault" 'release(bytes32,bytes32,address,uint256)' "$company" "$invoice" "$me" 20000)"

release='release(bytes32,bytes32,address,uint256)'
refused 'AlreadyPaid(bytes32,bytes32)' "$release" "$company" "$invoice" "$me" 20000
refused 'OverPerPaymentCap(uint256,uint256)' "$release" "$company" "$(cast keccak invoice-2)" "$me" 20001
refused 'OverDailyCap(uint256,uint256)' "$release" "$company" "$(cast keccak invoice-2)" "$me" 10001
refused 'PayeeNotActive(bytes32,address)' "$release" "$company" "$(cast keccak invoice-2)" "$stranger" 1

# A change signed as typed data, as a wallet builds it from the vault's own domain.
typed="$(jq -n --arg vault "$vault" --arg company "$company" --arg payee "$stranger" --argjson chain "$chain" '{
  types: {
    EIP712Domain: [
      {name: "name", type: "string"}, {name: "version", type: "string"},
      {name: "chainId", type: "uint256"}, {name: "verifyingContract", type: "address"}
    ],
    SetPayee: [
      {name: "company", type: "bytes32"}, {name: "payee", type: "address"},
      {name: "allowed", type: "bool"}, {name: "nonce", type: "uint256"}
    ]
  },
  primaryType: "SetPayee",
  domain: {name: "PolicyVault", version: "1", chainId: $chain, verifyingContract: $vault},
  message: {company: $company, payee: $payee, allowed: true, nonce: 0}
}')"
signature="$(cast wallet sign "${key[@]}" --data "$typed")"
echo "setPayee  $(send "$vault" 'setPayee(bytes32,address,bool,uint256,bytes)' \
  "$company" "$stranger" true 0 "$signature")"
# Listed, and still inside its timelock.
refused 'PayeeNotActive(bytes32,address)' "$release" "$company" "$(cast keccak invoice-2)" "$stranger" 1
refused 'BadNonce(uint256,uint256)' 'setPayee(bytes32,address,bool,uint256,bytes)' \
  "$company" "$stranger" true 0 "$signature"

echo "anchor    $(send "$vault" 'anchor(bytes32)' "$(cast keccak root-1)")"
echo "release   $(send "$vault" "$release" "$company" "$(cast keccak invoice-2)" "$me" 10000)"

echo "state: $(cast call "$vault" \
  'companyState(bytes32)((address,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256,address,uint256))' \
  "$company")"
echo "USDC after: $(cast call "$USDC_ADDRESS" 'balanceOf(address)(uint256)' "$me")"
echo "ok: 0.02 USDC stays in the throwaway vault, past its daily cap"
