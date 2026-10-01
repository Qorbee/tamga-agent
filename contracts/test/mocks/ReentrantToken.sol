// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.37;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {PolicyVault} from "../../src/PolicyVault.sol";

/// @dev A token that, while the vault pays out, calls the vault's `release` again for the same
/// payment. It is the vault's executor in the test, so only the vault's own state can stop it.
contract ReentrantToken is ERC20 {
    PolicyVault public vault;
    bytes32 public company;
    bytes32 public paymentKey;
    bool public reentered;
    bytes public innerRevert;

    constructor() ERC20("Reentrant", "RE") {}

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }

    function aim(PolicyVault vault_, bytes32 company_, bytes32 paymentKey_) external {
        vault = vault_;
        company = company_;
        paymentKey = paymentKey_;
    }

    function pay(address payee, uint256 amount) external {
        vault.release(company, paymentKey, payee, amount);
    }

    function transfer(address to, uint256 amount) public override returns (bool) {
        if (msg.sender == address(vault) && !reentered) {
            reentered = true;
            try vault.release(company, paymentKey, to, amount) {}
            catch (bytes memory reason) {
                innerRevert = reason;
            }
        }
        return super.transfer(to, amount);
    }
}
