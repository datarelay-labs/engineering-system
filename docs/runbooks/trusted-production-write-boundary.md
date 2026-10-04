# Trusted production-write boundary

This boundary is installed and operated by a human/root administrator. Coding runtimes must not create, read, replace, or export its private trust anchor.

## Authority modes

The production coordinator supports two distinct authority bases.

1. **Authenticated collaborator mode** — a normal operator invokes the coordinator through `sudo`; fixed `/usr/bin/gh` proves current `write`, `maintain`, or `admin` permission.
2. **Root-only production approver mode** — for production hosts intentionally operated through direct root access without GitHub credentials. This mode is limited to `production_write` + `shell.production_write`. It requires a separately installed root-owned exact approval record.

Never fake `SUDO_UID`, invoke the signer directly, or copy a GitHub token to production merely to satisfy this boundary.

## Install

Use a clean immutable checkout of the intended Engineering System baseline and run as root:

    sudo python3 tools/trusted_boundary_admin.py install --source "$PWD"
    sudo python3 tools/trusted_boundary_admin.py verify

The bootstrap creates fixed root-owned paths under `/etc/engineering-system` and `/usr/lib/engineering-system`, an Ed25519 private key mode `0600`, public anchor mode `0644`, and replay-state directory mode `0700`.

## Root-only exact production approval

A root-only production approval is not a repository-wide allowlist. Every record binds one exact effect:

```json
{
  "schema_version": 1,
  "kind": "trusted_production_approver_policy",
  "approvals": [
    {
      "repository": "datarelay-labs/datarelay-atlas",
      "issue_id": 307,
      "workstream": "prod-web-recovery-rollout-7e9ff06",
      "branch": "ops/prod-web-recovery-rollout-7e9ff06",
      "subject_head": "<40-char candidate SHA>",
      "intent_revision": 1,
      "session_id": "rollout-307-stage",
      "dispatch_id": "rollout-307-stage",
      "packet_sha256": "<sha256 of the exact current Work Packet body>",
      "request_sha256": "<canonical sha256 of the exact production request JSON>",
      "approved_by": "RickLee-kr"
    }
  ]
}
```

The policy is installed as `/etc/engineering-system/production-approvers.json`, root-owned mode `0600`. The security boundary is the root-admin installation plus the exact packet/request/session/dispatch binding; the mutable Issue creator field is not production authority.

## Upgrade an existing boundary

Do not rotate an existing trust anchor merely to update trusted boundary code:

    sudo python3 tools/trusted_boundary_admin.py upgrade \
      --source "$PWD" \
      --production-approver-policy /root/production-approvers.json

`upgrade` validates and canonicalizes the policy before any mutation, preserves the existing private/public trust anchor and replay-state directory, and atomically replaces trusted tools/import modules.

Then verify:

    sudo python3 tools/trusted_boundary_admin.py verify \
      --require-production-approvers

## Production assertion issuance

A production Work Packet must be open `[AI Work]`, `ACTIVE`, `HIGH`/`CRITICAL`, exact repository/workstream/branch/HEAD/intent bound, and bound to the current managed execution profile.

### Authenticated collaborator mode

The coordinator authenticates the Issue, collaborator permission, branch HEAD, subject profile, and immutable Engineering System execution-profile bytes through fixed `/usr/bin/gh`. Current `write`/`maintain`/`admin` permission is required.

### Root-only mode

The coordinator:

- requires the root-owned mode-`0600` exact approval policy;
- requires the target repository to be public;
- reads only fixed `https://api.github.com` repository/Issue/commit/content endpoints with bounded responses;
- builds TLS trust only from root-owned, non-group/world-writable `/etc/ssl/certs/ca-certificates.crt` and ignores caller trust-store environment overrides;
- hashes the current Work Packet body and exact request JSON and requires an exact policy match including session and dispatch IDs;
- requires exact Work Packet repository/workstream/branch/HEAD/intent/risk/profile binding;
- requires the named branch to resolve to the exact subject HEAD and that commit to exist;
- revalidates target/canonical execution-profile bytes at the exact subject/baseline revisions;
- mints a signed `authority_basis=production_approver_policy` / `authority_permission=production_approver` binding only after every check passes.

A changed packet, request, session, dispatch, branch, HEAD, profile, policy, or public GitHub result fails closed before signing. Pinning `dispatch_id` makes the exact approval one-shot in combination with the existing replay-state consume contract.

The coordinator/signer do not execute the production effect. Before execution require:

    sudo /usr/bin/python3 /usr/lib/engineering-system/skills-contract.py authorize \
      --root <exact-production-worktree> \
      --binding-assertion <binding.json> \
      --dispatch-assertion <dispatch.json> \
      --request-json <exact-request.json>

Proceed only on `ALLOW`.

## Remove / rollback

Removal destroys the host trust anchor, replay state, and installed approval policy:

    sudo python3 tools/trusted_boundary_admin.py remove

After removal all trusted high-risk authorization fails closed.

## Atlas continuation

After this boundary is merged, installed/upgraded, and independently verified on the required host, resume the current DataRelay Atlas production Work Packet. Generate fresh exact approval records for the approved production effects; do not reuse an old dispatch or bypass the boundary.
