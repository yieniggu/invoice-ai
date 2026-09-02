// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {EvidenceRootAnchor} from "../src/EvidenceRootAnchor.sol";

interface Vm {
    function envAddress(string calldata name) external returns (address value);
    function startBroadcast() external;
    function stopBroadcast() external;
}

contract DeployEvidenceRootAnchor {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    function run() external returns (EvidenceRootAnchor anchor) {
        address allowedSigner = vm.envAddress("EVIDENCE_ROOT_ANCHOR_SIGNER");
        vm.startBroadcast();
        anchor = new EvidenceRootAnchor(allowedSigner);
        vm.stopBroadcast();
    }
}
