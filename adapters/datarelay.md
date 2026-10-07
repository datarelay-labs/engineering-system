# DataRelay optional integrations

These mappings are optional DataRelay organization adapters. They are not universal Engineering System requirements and are not installed by default managed adoption.

## Derived context

DataRelay Atlas may implement the optional derived-context role described by `standards/KNOWLEDGE.md`. Canonical Git/GitHub state remains authoritative; Atlas retrieval/write-back is bounded, non-authoritative, and unavailable Atlas service must not block ordinary engineering.

### Normal development usage (optional)

For DataRelay continuation, prior decisions/lessons, or an explicitly scoped
cross-project question, use Atlas proactively **when current canonical/local
context leaves a meaningful gap**. Resolve the owner-selected repository and
relevant current Git/GitHub facts first. No special "use Atlas" prompt is needed.
Reuse sufficient context; no mandatory startup, per-turn, per-commit, or duplicate
bootstrap query. Query only the missing project/workstream context and useful
provenance. Unknown project IDs must be resolved, not guessed; ambiguous
repository matches must not retarget the task.

Canonical facts win over derived results. Atlas unavailable, timed out, stale or
`UNKNOWN` means continue ordinary engineering from canonical/local context, not
wait/retry, invent a completion/permission result, or switch runtime/mode.
An explicit MCP-only data request must report a failed native read honestly,
not silently substitute SSH/CLI or cached text. Continue the actual task after
retrieval rather than stopping at an Atlas status report.

The maintained tool-level instructions and package live in the Atlas repository:
[integrations/chatgpt-plugin/skills/use-atlas/SKILL.md](https://github.com/datarelay-labs/datarelay-atlas/blob/main/integrations/chatgpt-plugin/skills/use-atlas/SKILL.md).
Use that installed skill with the existing authenticated Atlas connection;
link to it rather than copying it into every repository's `AGENTS.md` or the
universal adoption template. Installation/availability is not proof that every
chat loaded the skill or that every relevant request activates it.

This guidance adds no background writer or automatic transcript retention.
Read-tool calls do not themselves prove memory-effectiveness observations;
measurements and any authorized derived write-back are separate workflows.
No production, OAuth, client, credential or permission changes are implied.

The legacy `tools/atlas-context-contract.py` and `tools/atlas-workflow.py` remain DataRelay adapter/reference tooling. Universal adoption does not copy them.

## Owner notification

DataRelay hosts may implement `/usr/lib/engineering-system/owner-notify` with Telegram or another approved transport. The universal managed tools know only the fixed helper contract:

- arguments: `<INFO|COMPLETE|ERROR> <bounded message>`
- success marker: `OWNER_NOTIFY=PASS`
- durable receipt marker when required: `OWNER_NOTIFY_RECEIPT=<opaque receipt>`

Transport credentials and destination selection stay outside adopted repositories and outside caller-controlled Work Packet data. Engineering completion remains separate from notification delivery.
