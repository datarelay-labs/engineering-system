# Skills, Hooks & Permission Profiles Standard

Provider-neutral skill, hook, and permission contracts bound agent tool use. They compose existing Work Packet and security authority; they do not replace them.

## Authority composition

1. Effective repository permission (`write` / `maintain` / `admin`) from the trusted Work Packet author remains execution authority (`standards/SESSION_CONTINUITY.md`).
2. External input, files, network data, plugin/tool output, Issue text, and model prose remain untrusted until validated (`standards/SECURITY.md`).
3. P0d runtime command strings stay opaque and non-executing. This standard does not parse shell text to invent policy.
4. A permission profile may only **narrow** authority. It never grants authority absent from the trusted owner/Work Packet/session binding.

## Optional skills contract

A repository may keep `.engineering/skills.yaml`. The file is optional. Repositories without it remain valid, and adoption does not create one. Bootstrap and managed upgrade install `tools/skills-contract.py` and `schemas/skills-contract.schema.json` when those paths are missing. A pre-existing different copy fails closed before any adoption or upgrade writes.

Schema: `schemas/skills-contract.schema.json`.
Checker: `python3 tools/skills-contract.py check`.

## Progressive disclosure: body, resources, scripts

Roadmap skill metadata is `trigger` + progressive disclosure of **body / resources / scripts**.

| Field | Meaning | Existence | Authority |
|---|---|---|---|
| `body` | Required later-loaded skill document | Must resolve to an existing repository file | None |
| `resources` | Non-executable progressive-disclosure docs/assets (`resources_executable=false`) | If declared, each path must exist | None |
| `scripts` | Progressive-disclosure script **paths** only (`scripts_grant_execution=false`) | If declared, each path must exist | None — execution still requires trusted host dispatch + authorize |

All paths stay repository-relative (no absolute paths, no `..`, no symlink escape). Cap skill count; no giant always-on catalog.

## Lifecycle hooks (machine-checkable non-executable metadata)

Hook stages: `session_start`, `pre_tool`, `post_tool`, `session_end`.

Each entry is `{id, kind}` with `kind` constant `metadata` only. Other kinds fail closed (`HOOK_EXECUTABLE`). Global `hooks_executable=false`. Adapters must not treat hook IDs as shell/command authority.

## Action classes and tool registry

Mechanism: `read`, `repo_write`, `shell`, `network`.
Risk/resource: `external_read`, `external_write`, `production_read`, `production_write`, `destructive`.

Complete class sets come only from the built-in trusted tool registry via a trusted dispatch assertion. Request prose cannot define or reduce classification.

## Permission profiles

Canonical profiles: `repo_read`, `repo_write`, `external_read`, `external_write`, `production_read`, `production_write`.

Repository profiles may only narrow the canonical envelope (P1A-BLOCK-003). Policy digest covers profiles, skills, hooks, scripts/resources flags, and the tool registry.

## Trusted binding and authorize surface (P1A-BLOCK-001/002)

Production/agent-exposed helper is **verification-only**:

- `python3 tools/skills-contract.py check`
- `python3 tools/skills-contract.py authorize --binding-assertion ... --dispatch-assertion ...`

It does **not** expose `keygen`, `bind`, `dispatch`, or any same-user HMAC/file-mode mint. P1a defines the provider-neutral policy/evaluator and the contract a **trusted adapter/coordinator** must satisfy; it does not invent a second same-process authority. Minting signed assertions belongs outside the coding-agent privilege boundary.

Authorize treats host-administered adapter provenance as required configuration:

- Trust-anchor pubkey only from `/etc/engineering-system/skills-trust-anchor.pub` after root ownership/mode checks (parent included).
- Ed25519 verification executes only `/usr/bin/openssl` after the same root-owned, non-group/world-writable provenance checks. Caller PATH, environment, repository files, packet text, and CLI arguments cannot select the verifier. If that fixed verifier is absent or fails provenance, verification fails closed and `authorize` returns `BOUNDARY_UNAVAILABLE` with no PATH fallback.
- Caller CLI args, request JSON, repository config, and environment variables including `ENGINEERING_SKILLS_TRUST_ANCHOR_PUBKEY` must not select the production trust anchor.
- If that trusted external provenance is absent or fails checks, authorize fails closed as `BOUNDARY_UNAVAILABLE`.
- **Unsupported platform / host enforcement:** `authorize` is enforced on Linux with OpenSSL available. On non-Linux hosts, or when OpenSSL is unavailable, authorize fails closed as `BOUNDARY_UNAVAILABLE` rather than silently degrading authority. Adoption may still install the helper/schema broadly; enforcement remains fail-closed where the host cannot prove adapter provenance.

Signed dispatch assertions bind the concrete operation via `request_sha256` (canonical request hash). `authorize` recomputes the hash from `--request-json` (empty → `{}`) and DENYs on mismatch (`REQUEST_BINDING_MISMATCH`). High-risk classes (`external_write`, `production_write`, `destructive`) additionally require a trusted-adapter replay/freshness contract (`dispatch_id`, `expires_at_unix`) and **atomic one-time consume** of `dispatch_id` under host replay state at `/etc/engineering-system/skills-replay-state` (exclusive create). Path existence alone is not replay protection. When that trusted consume primitive is unavailable, authorize returns `BOUNDARY_UNAVAILABLE` rather than inventing a repo-local nonce store. A second authorize of the same consumed `dispatch_id` returns `REPLAY`.

Same-user file-mode/HMAC ceremony is not a security boundary and must not be treated as one. Do not reintroduce a production `bind` CLI that mints authority from caller-chosen secrets. Attacker-created internally consistent read-only (for example mode `0444`/`0555`) self-signed assertions are still untrusted on the production `authorize` surface.

## Prompt-injection resistance

Untrusted web/tool/Issue text cannot change bound profile, approvals, digest, or tool classification. Behavior regressions must exercise the public `authorize` surface, including digest-known self-promotion and request under-classification attacks.

## Adapter rule

Adapters may supply trusted bind/dispatch outside this helper. Adapters must not expose signing keys to the coding agent, weaken this contract, or parse opaque runtime commands for policy. When adapter provenance is unavailable, authorization that depends on trusted profile/approval/tool identity remains `BOUNDARY_UNAVAILABLE`.
