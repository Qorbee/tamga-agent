# Contracts

The Solidity package, built and tested with Foundry. It holds PolicyVault: one contract that holds
every company's test USDC, each company's apart, and pays only through the executor, within that
company's caps and payee list, once per payment key. A company's limits change only on its owner
key's signature; a change that loosens one waits out the company's timelock.

## Map

| Path | What is there |
|---|---|
| `foundry.toml` | Compiler (solc) and EVM target, remappings, warnings as errors |
| `.foundry-version` | The Foundry release this package is built with; CI reads it |
| `src/PolicyVault.sol` | The contract. Contracts are one per file, named after it |
| `test/PolicyVault.t.sol` | Its tests: every refusal, one company against another, the timelock's edges, signatures replayed; `test/mocks/` holds the tokens and the smart account they use |
| `script/smoke.sh` | A throwaway vault run through a payment on Arc testnet, each step executed by Arc's node |
| `lib/` | forge-std and OpenZeppelin Contracts, as git submodules pinned to release tags |

Generated from here: `backend/app/platform/abi/<Name>.json`, the ABI the backend reads the chain with
(`scripts/export-abi.sh`).

## Setting up

Install the Foundry release in `.foundry-version` straight from its GitHub release, checked against
its SHA-256. `foundry.paradigm.xyz` would run whatever installer it serves that day, outside the 7-day
rule. From the repository root:

```bash
v="$(cat contracts/.foundry-version)"; p=linux_amd64     # or linux_arm64, darwin_arm64, darwin_amd64
base="https://github.com/foundry-rs/foundry/releases/download/$v"
curl -fsSLO "$base/foundry_${v}_$p.tar.gz" && curl -fsSLO "$base/foundry_${v}_$p.sha256"
sha256sum -c "foundry_${v}_$p.sha256"                    # macOS: shasum -a 256 -c
mkdir -p ~/.foundry/bin && tar -xzf "foundry_${v}_$p.tar.gz" -C ~/.foundry/bin
rm "foundry_${v}_$p.tar.gz" "foundry_${v}_$p.sha256"
export PATH="$HOME/.foundry/bin:$PATH"                   # add to your shell profile
git submodule update --init                              # the libraries, if the clone did not fetch them
```

The libraries' own submodules are not needed.

## Checks

From `contracts/`:

```bash
forge fmt --check && forge build --sizes && forge test
../scripts/export-abi.sh        # after an interface change; commit the result
```

## Arc

Arc runs the Osaka hard fork, so `evm_version` is `osaka`. USDC is the gas token: its ERC-20 interface
has 6 decimals, and the native balance, the same funds, has 18. Forge tests run on Foundry's own EVM,
not Arc's rules. PolicyVault moves USDC only through the ERC-20 interface, has no payable function
and sends no native value, so its logic does not depend on them; what does (real USDC, the chain's
id in a signature) is covered by the smoke run:

```bash
ETH_RPC_URL=… USDC_ADDRESS=… SMOKE_PRIVATE_KEY=… contracts/script/smoke.sh   # about 0.2 test USDC
```

Circle's fork, [`arc-foundry`](https://github.com/circlefin/arc-foundry), runs forge tests under
Arc's rules if one ever depends on them.

The vault Tamga pays from is deployed by `backend/scripts/deploy_policy_vault.py`, from Tamga's
treasury wallet through Circle's contracts API. On Arc testnet it is
[`0xbc651378b33c25e263cc17c3668288a62facb5f9`](https://testnet.arcscan.app/address/0xbc651378b33c25e263cc17c3668288a62facb5f9), and its
source is [verified on Sourcify](https://repo.sourcify.dev/5042002/0xbc651378b33c25e263cc17c3668288a62facb5f9).
