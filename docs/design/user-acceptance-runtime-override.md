# Signed owner runtime override for user-acceptance evidence — design candidate (#297)

**Status: PROPOSED / NOT ADOPTED.** The new verifier and schema are under review.
They do not establish a real owner-approval issuer, authenticated persona execution,
product quality closure, or release authority. Existing profile-default behavior
and independent real-user gates remain mandatory. No operating-system keys or
permissions are installed or changed by this proposal.

## Scope and objective

Canonical rules permit a *current, explicitly authenticated owner instruction*
to override the execution-profile primary. The user-acceptance structural
validator currently treats only the committed profile runtime as acceptable.
A self-asserted `owner_override: true`, alternate user runtime in a Work Packet,
GitHub prose, or unrestricted key path is not sufficient.

A signed receipt can be attached as `owner_runtime_override` to evidence
produced by the actual selected executor. The validator accepts the alternate
runtime only when a purpose-scoped, bounded Ed25519 receipt verifies against
the fixed root-administered Engineering System public trust anchor.

The signed statement contains the exact GitHub repository, candidate HEAD,
committed gate contract path + SHA-256, gate, run ID, chosen runtime, owner
actor/authority/authorization reference, one receipt ID, issuance/expiry and
issuer/purpose. A receipt can be used to validate the *same* run more than once
(e.g. structural check and quality-close); it cannot be transferred to a
different repo, candidate, gate, run, or runtime. Lifetime is at most one hour.

The public key and OpenSSL executable are resolved only from fixed root-owned,
non-writable paths. The repository, evidence, environment and CLI cannot select
a trust anchor. Missing anchor, invalid signature, expired or mismatched
receipt, malformed evidence, or absent owner authorization => BLOCK.

## Trusted issuance boundary — prerequisite for adoption

The current root-administered Ed25519 key is used only by independently
restricted external/production-write signers. Those signers do **not** have
authority to issue a user-gate override. A separate **purpose-specific trusted
issuer** must authenticate the human owner's current instruction and effective
authorization *before* signing, and securely audit/limit issuance to one bound
gate/run. Do not accept arbitrary caller fields such as `owner_actor` or
`owner_authority` as authenticated by themselves; they are meaningful only
when a separately trusted issuer verifies and signs them. It must prohibit
generic private-key access, arbitrary payload signing, uncontrolled key
selection, and replay across different runs.

Do not deploy, enable or merge as an effective permission-policy change
without the applicable owner/security approval and verified trusted issuance
integration. The coding worker cannot manufacture that approval. Tests may
use temporary test keys injected in-process only; no test-key path is exposed
through the production CLI.

## Preservation of non-bypassable gates

An accepted signature proves only the authentication of *runtime selection*,
not that the owner performed a user journey or that a browser/CLI product
surface works. Existing exact HEAD, committed contract digest, clean-contract,
direct persona, real public surface, ledger/coverage, and zero-blocker checks
remain unchanged. `quality-close` still emits
`TRUSTED_PERSONA_ATTESTATION=REQUIRED`,
`PRODUCT_QUALITY_CLOSURE=BLOCK`, and `AUTHORIZES_RELEASE=NO`.

## Test contract / rollout limits

Regression coverage includes profile default, two valid signed gates, no
runtime substitution, wrong key, forged signature, expired/future/overlong
receipt, wrong repo/HEAD/contract/gate/run/runtime, incorrect issuer, unknown
fields, nonexistent trust anchor, fake JSON approval and non-bypassable persona
requirements.

This proposal is intentionally not rolled out to adopted product repositories.
A reviewed/approved canonical standard update, any necessary governance epoch
migration and production trust-issuer deployment are separate gates.
