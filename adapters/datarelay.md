# DataRelay optional integrations

These mappings are optional DataRelay organization adapters. They are not universal Engineering System requirements and are not installed by default managed adoption.

## Derived context

DataRelay Atlas may implement the optional derived-context role described by `standards/KNOWLEDGE.md`. Canonical Git/GitHub state remains authoritative; Atlas retrieval/write-back is bounded, non-authoritative, and unavailable Atlas service must not block ordinary engineering.

The legacy `tools/atlas-context-contract.py` and `tools/atlas-workflow.py` remain DataRelay adapter/reference tooling. Universal adoption does not copy them.

## Owner notification

DataRelay hosts may implement `/usr/lib/engineering-system/owner-notify` with Telegram or another approved transport. The universal managed tools know only the fixed helper contract:

- arguments: `<INFO|COMPLETE|ERROR> <bounded message>`
- success marker: `OWNER_NOTIFY=PASS`
- durable receipt marker when required: `OWNER_NOTIFY_RECEIPT=<opaque receipt>`

Transport credentials and destination selection stay outside adopted repositories and outside caller-controlled Work Packet data. Engineering completion remains separate from notification delivery.
