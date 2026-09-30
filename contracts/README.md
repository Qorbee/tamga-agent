# Contracts

The Solidity package, built and tested with Foundry. It will hold PolicyVault: the contract that holds
each company's test USDC and pays only through the executor, within the company's caps and payee
allowlist. PolicyVault has its own plan; this package is the scaffold it builds on.

## Map

| Path | What is there |
|---|---|
| `foundry.toml` | Compiler (solc) and EVM target, remappings, warnings as errors |
| `.foundry-version` | The Foundry release this package is built with; CI reads it |
| `src/` | Contracts, one per file, named after it |
| `test/` | Forge tests (`*.t.sol`) |
| `script/` | Deploy scripts (`*.s.sol`) |
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
not Arc's rules. Circle's fork, [`arc-foundry`](https://github.com/circlefin/arc-foundry), runs them
under Arc's rules if a test ever depends on them.
