# Trusted production-write boundary

This boundary is installed and operated by a human/root administrator. Coding runtimes must not create, read, replace, or export its private trust anchor.

## Authority modes

The production coordinator supports two distinct authority bases.

1. **Authenticated collaborator mode** — preferred when a normal operator account invokes the coordinator through `sudo`. The coordinator binds `/usr/bin/gh` authentication to the real `SUDO_UID` user and requires current GitHub `write`, `maintain`, or `admin` permission.
2. **Root-only production approver mode** — for production hosts intentionally operated through direct root access and without GitHub credentials. This mode is production-write-only. It requires a separately installed root-owned approver policy plus public GitHub evidence for an owner-authored Work Packet and exact public repository/branch/commit state. It does not grant repository implementation or ordinary GitHub mutation authority.

Never fake `SUDO_UID`, call the signer directly, copy a GitHub token to production merely to satisfy this boundary, or represent the production approver basis as collaborator permission.

## Install

Use a clean immutable checkout of the intended Engineering System baseline and run as root:

    sudo python3 tools/trusted_boundary_admin.py install --source "$PWD"
    sudo python3 tools/trusted_boundary_admin.py verify

The bootstrap creates fixed root-owned paths under `/etc/engineering-system` and `/usr/lib/engineering-system`, an Ed25519 private key mode `0600`, public anchor mode `0644`, and replay-state directory mode `0700`. The private key is never printed.

### Root-only production approver policy

For a root-only production host, a human/root administrator prepares a non-secret policy file outside worker-writable repositories:

```json
{
  "schema_version": 1,
  "kind": "trusted_production_approver_policy",
  "repositories": {
    "datarelay-labs/datarelay-atlas": ["RickLee-kr"]
  }
}
```

Install it together with a new boundary, or during a boundary tool upgrade:

    sudo python3 tools/trusted_boundary_admin.py install \
      --source "$PWD" \
      --production-approver-policy /root/production-approvers.json

The installed policy is `/etc/engineering-system/production-approvers.json`, root-owned mode `0600`. It is an explicit host-admin authorization allowlist, not a GitHub credential and not a repository permission cache.

## Upgrade an existing boundary

Do not remove or rotate an existing trust anchor merely to update trusted boundary code. From a clean immutable checkout of the newly qualified Engineering System baseline:

    sudo python3 tools/trusted_boundary_admin.py upgrade \
      --source "$PWD" \
      --production-approver-policy /root/production-approvers.json

`upgrade` preserves the existing private/public trust anchor and replay-state directory, verifies their provenance first, atomically replaces the installed trusted tools/import modules, and installs/replaces the canonicalized approver policy when supplied.

Then require the policy explicitly:

    sudo python3 tools/trusted_boundary_admin.py verify \
      --require-production-approvers

## Verify

    sudo python3 tools/trusted_boundary_admin.py verify

Verification performs no production mutation. When `--require-production-approvers` is used it also requires a valid root-owned `0600` approver policy.

## Production assertion issuance

A production Work Packet must be open `[AI Work]`, `ACTIVE`, `HIGH`/`CRITICAL`, exact repository/workstream/branch/HEAD/intent bound, and bound to the current managed execution profile.

### Authenticated collaborator mode

When invoked as root through `sudo` from a normal operator account, the coordinator authenticates the Issue, collaborator permission, branch HEAD, subject profile, and immutable Engineering System execution-profile bytes through fixed `/usr/bin/gh`. Current `write`/`maintain`/`admin` permission is required.

### Root-only production approver mode

When the coordinator is invoked directly as root with no non-root `SUDO_UID`, it does not use or accept caller GitHub credentials. It:

- requires `/etc/engineering-system/production-approvers.json` with root ownership, mode `0600`, and secure parent provenance;
- requires the target repository to be public;
- reads only fixed `https://api.github.com` repository/Issue/commit/content endpoints with the standard-library HTTPS client, bounded response sizes, no arbitrary caller URL, and no redirect-following behavior;
- requires the Work Packet author login to be allowlisted for that exact repository;
- requires exact Work Packet repository/workstream/branch/HEAD/intent/risk/profile binding;
- requires the named branch to resolve to the exact `LAST_VERIFIED_HEAD`;
- requires that exact commit to exist in the public repository;
- revalidates target/canonical execution-profile bytes at the exact subject/baseline revisions;
- asks the fixed production signer to mint `authority_basis=production_approver_policy` with `authority_permission=production_approver`.

Any missing policy, provenance defect, public GitHub failure/rate limit, malformed response, private repository, wrong author, stale packet, branch/HEAD mismatch, subject commit mismatch, or execution-profile mismatch fails closed before signing.

The request JSON describes the exact opaque production effect. The coordinator/signer do not execute that effect. The resulting assertion is bound to the canonical request hash and is consumed once by `skills-contract.py authorize`.

Use `/usr/lib/engineering-system/trusted-production-write-coordinator` with the exact repository, Work Packet issue, worktree, request JSON, workstream, branch, subject HEAD, intent revision, session/dispatch IDs and private output paths. Keep TTL short.

Before the production effect itself, require:

    sudo /usr/bin/python3 /usr/lib/engineering-system/skills-contract.py authorize \
      --root <exact-production-worktree> \
      --binding-assertion <binding.json> \
      --dispatch-assertion <dispatch.json> \
      --request-json <exact-request.json>

Proceed only on `ALLOW`. A replayed dispatch is rejected.

## Remove / rollback

Removal destroys the host trust anchor, replay state, and installed approver policy, so it is an explicit root-admin action:

    sudo python3 tools/trusted_boundary_admin.py remove

After removal all trusted high-risk authorization fails closed as `BOUNDARY_UNAVAILABLE`.

## Atlas continuation

After this boundary is installed/upgraded and independently verified on the required host, resume the current DataRelay Atlas production Work Packet. Obtain a fresh exact-effect production assertion; do not reuse an old dispatch or bypass the boundary with direct deployment.
