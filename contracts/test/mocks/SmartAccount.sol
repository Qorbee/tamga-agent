// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.37;

import {ECDSA} from "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import {IERC1271} from "@openzeppelin/contracts/interfaces/IERC1271.sol";

/// @dev The smallest ERC-1271 account: valid when its one signer signed.
contract SmartAccount is IERC1271 {
    address public immutable signer;

    constructor(address signer_) {
        signer = signer_;
    }

    function isValidSignature(bytes32 hash, bytes calldata signature) external view returns (bytes4) {
        return ECDSA.recover(hash, signature) == signer ? IERC1271.isValidSignature.selector : bytes4(0xffffffff);
    }
}
