// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

contract EvidenceRootAnchor {
    error UnauthorizedSigner(address caller);
    error DuplicateRoot(bytes32 rootHash);

    address public immutable signer;
    mapping(bytes32 rootHash => bool registered) private registeredRoots;

    event RootRegistered(bytes32 indexed rootHash, address indexed signer);

    constructor(address allowedSigner) {
        signer = allowedSigner;
    }

    function registerRoot(bytes32 rootHash) external {
        if (msg.sender != signer) revert UnauthorizedSigner(msg.sender);
        if (registeredRoots[rootHash]) revert DuplicateRoot(rootHash);

        registeredRoots[rootHash] = true;
        emit RootRegistered(rootHash, msg.sender);
    }

    function isRootRegistered(bytes32 rootHash) external view returns (bool) {
        return registeredRoots[rootHash];
    }
}
