# ES-TOOL-5 — OpenComputer-inspired rootless session evaluation

**Date:** 2026-11-24 KST. **Decision:** NOT_ADOPTED_WITH_EVIDENCE as a general or unattended agent runner. The isolated test was run as the ordinary `aella` account on nonproduction DEV DR Atlas. No OpenComputer service, scheduler, Podman daemon, additional credentials or privileged tool grant was installed.

## Actual bounded experiment

The reference implementation, diggerhq/opencomputer, uses session-specific computers and rootless Podman. We tested the relevant *principle*, not an unapproved product deployment. Actual Ubuntu 24.04 host operations used `unshare --user --map-root-user --mount --pid --net --fork`, a fresh 48 MiB tmpfs root, read-only bind-mounted `/usr`, a private `/work`, no `/home` bind, and `setpriv --bounding-set=-all --no-new-privs` in a chroot child.

Two separate rootless sessions each started with `FRESH_SESSION=YES`, found no previous session file, denied access to host `/home/aella`, and rejected writes into `/usr` with `Read-only file system`. This confirms narrowly tested file separation under these user namespace settings; it does **not** certify resistance to all container escapes or prove process egress is fully firewalled.

A separate 22-second child was cancelled after about 1.5 seconds. The child printed `CANCEL_SESSION_STARTED`, then `Terminated`, and no `sleep 22` remained. A new session started clean. **Negative evidence:** abrupt termination bypassed a shell `trap`, leaving one *empty, unmounted* temporary root directory. After verifying it contained no mounted filesystem or files, it was explicitly removed; a later scan found zero remaining test root directories. An unmanaged long-running session could accumulate such debris.

On dev-drcontrol, the identical ordinary-account rootless User Namespace check failed: `unshare: write failed /proc/self/uid_map: Operation not permitted`. There is no safe cross-host fallback to unrestricted shell. Both observed host environments lacked usable `/dev/kvm`; the test was not a VM.

Separately, authenticated GitHub API readback matched Engineering System PR #318 source HEAD `ad897a520939aac7f43d8ac888df9be2dfbd226c` and PR #327 HEAD `9b06e3e1db6ad15f99dbf58573d82515f2d15211` to local Git commit objects. This **does not attest the actual isolated script to either PR** because the GitHub read was performed on a separate host, outside the sandbox. Do not call it complete PR-to-execution provenance.

## Decision rationale

The existing owner-authorized Chat/DRLink development mechanism already exists. A full new OpenComputer-style durable agent worker would duplicate process orchestration, Git/worktree ownership, cancellation handling and credentials, with no demonstrated product-user E2E gain. Cross-host namespace restrictions and real cancellation cleanup failure prevent adoption. This is a completed *non-adoption decision* under the owner-approved candidate rule, not an unreleased implementation.

Reconsider only after a supervisor guarantees child-group cancellation and cleanup even under SIGKILL, all selected hosts enforce equivalent rootless isolation or fail closed, source/PR provenance is bound to the actual executed checkout, and repeated private-file/network/proc/CPU tests pass. Do not weaken Engineering System Work Packet, release or selected-runtime authority.

Durable GitHub record: https://github.com/datarelay-labs/engineering-system/issues/317#issuecomment-7114651087. No customer secrets, product source or production state changed.
