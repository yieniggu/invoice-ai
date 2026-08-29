// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {EvidenceRootAnchor} from "../src/EvidenceRootAnchor.sol";

interface Vm {
    function expectEmit(bool checkTopic1, bool checkTopic2, bool checkTopic3, bool checkData) external;
}

contract EvidenceRootAnchorTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    EvidenceRootAnchor private anchor;
    bytes32 private constant ROOT = keccak256("invoiceops-root");

    event RootRegistered(bytes32 indexed rootHash, address indexed signer);

    function setUp() public {
        anchor = new EvidenceRootAnchor(address(this));
    }

    function testRegisterAndQueryRootEmitsEvent() public {
        vm.expectEmit(true, true, false, false);
        emit RootRegistered(ROOT, address(this));
        anchor.registerRoot(ROOT);

        require(anchor.isRootRegistered(ROOT), "root was not registered");
    }

    function testRejectsDuplicateRoots() public {
        anchor.registerRoot(ROOT);
        (bool success,) = address(anchor).call(abi.encodeCall(anchor.registerRoot, (ROOT)));

        require(!success, "duplicate root was accepted");
    }

    function testRejectsUnauthorizedSigner() public {
        UnauthorizedCaller caller = new UnauthorizedCaller();
        (bool success,) = caller.register(address(anchor), ROOT);

        require(!success, "unauthorized signer was accepted");
    }
}

contract UnauthorizedCaller {
    function register(address anchor, bytes32 root) external returns (bool, bytes memory) {
        return anchor.call(abi.encodeWithSignature("registerRoot(bytes32)", root));
    }
}
