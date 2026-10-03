# Trusted production-write boundary

This boundary is installed and operated by a human/root administrator. Coding runtimes must not create, read, replace, or export its private trust anchor.

## Install

Use a clean immutable checkout of the intended Engineering System baseline and run as root:

    sudo python3 tools/trusted_boundary_admin.py install --source "$PWD"
    sudo python3 tools/trusted_boundary_admin.py verify

The bootstrap creates fixed root-owned paths under /etc/engineering-system and /usr/lib/engineering-system, an Ed25519 private key mode 0600, public anchor mode 0644, and replay-state directory mode 0700. The private key is never printed.

## Verify

    sudo python3 tools/trusted_boundary_admin.py verify

Verification performs no production mutation.

## Production assertion issuance

A production Work Packet must be ACTIVE, HIGH/CRITICAL, exact branch/HEAD/intent bound, and authored by a GitHub collaborator with write/maintain/admin permission. The root coordinator authenticates those facts with fixed /usr/bin/gh, then invokes the fixed root signer.

The request JSON describes the exact opaque production effect. The coordinator/signer do not execute that effect. The resulting assertion is bound to the canonical request hash and is consumed once by skills-contract.py authorize.

Use /usr/lib/engineering-system/trusted-production-write-coordinator with the exact repository, Work Packet issue, worktree, request JSON, workstream, branch, subject HEAD, intent revision, session/dispatch IDs and private output paths. Keep TTL short.

## Remove / rollback

Removal destroys the host trust anchor and replay state, so it is an explicit root-admin action:

    sudo python3 tools/trusted_boundary_admin.py remove

After removal all trusted high-risk authorization fails closed as BOUNDARY_UNAVAILABLE.

## Atlas continuation

After this boundary is installed and independently verified on the required host, resume DataRelay Atlas Work Packet #268. Obtain a fresh exact-effect production assertion; do not reuse an old dispatch or bypass the boundary with direct deployment.
