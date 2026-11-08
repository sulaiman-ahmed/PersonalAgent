# PAP-0000: Personal Agent Proposal Process

| Field  | Value    |
| ------ | -------- |
| Status | Accepted |
| Type   | Process  |

## Summary

Personal Agent Proposals (PAPs) record significant design decisions for the personal multi-agent system. Each PAP explains **what** is being decided, **why**, and the most important tradeoffs. PAPs are concise, version-controlled, and immutable once accepted.

## When a PAP is required

Write a PAP for changes that materially affect system architecture, agent behavior, public interfaces, security boundaries, persistence, or development processes. Routine bug fixes, refactors that preserve behavior, and documentation corrections do not require one.

## PAP format

Each proposal should include:

1. **Summary** — The decision in a few sentences.
2. **Motivation** — The problem and relevant constraints.
3. **Proposed design** — The approach and essential interfaces or flows.
4. **Alternatives and tradeoffs** — Important options considered and why they were not chosen.
5. **Consequences** — What this decision enables, costs, or leaves unresolved.

Keep most PAPs to **1–2 pages**. Link to supporting diagrams or deeper technical documentation rather than reproducing it.

## Process

1. Create a branch `pap/<topic>` and a draft at `docs/pap/NNNN-<topic>/README.md`.
2. Discuss and revise the draft until the design is accepted or rejected.
3. On acceptance, assign the next available PAP number and merge the proposal.
4. **Freeze the accepted PAP.** Do not edit, rename, or delete it afterward.
5. Track implementation separately. A PAP may correspond to zero, one, or multiple implementation changes.

Drafts may be edited during review. Rejected or withdrawn drafts can be tracked in the mutable PAP index without becoming accepted records.

## Changes after acceptance

An accepted PAP is a historical snapshot, **not living documentation**. A change to an accepted decision requires a new PAP that cites the earlier one and states what it replaces. Never edit the earlier PAP, even to update its status.

The mutable `docs/pap/README.md` index tracks proposal status and supersession relationships. The living `docs/architecture/` documentation describes the current intended system design and links to the PAPs that justify it. Implementation documentation separately describes what is actually running.
