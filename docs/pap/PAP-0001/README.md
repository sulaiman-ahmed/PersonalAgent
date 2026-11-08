# PAP-NNNN: Core Agent Runtime

| Field | Value |
|---|---|
| Status | Accepted |
| Type | Architecture |
| Scope | Initial single-agent execution |

## Summary

Build a **generic, model-directed agent runtime** that repeatedly invokes a model, executes requested tools, returns observations, and continues until the model produces a final response or execution is interrupted. The same runtime will eventually execute root agents and dynamically spawned subagents; agent roles will not be hardcoded into it.

## Motivation

The runtime is the smallest useful foundation of the personal agent system. We need to validate autonomous tool use and capture execution behavior before introducing delegation, scheduling, or persistent memory.

## Decision

- Implement a custom, asynchronous Python agent loop using OpenAI's model API, rather than adopting a framework-managed agent runner.
- Expose tools through a common registry with schemas, execution handlers, and runtime-enforced authorization. Start with file read/search/edit and terminal execution in a controlled workspace.
- Let the model choose when to call tools, in what order, and when to finish. The runtime only enforces tool validity, permissions, termination limits, and execution correctness.
- Record each model turn, tool request, tool result, error, and final output as a structured trace. Persist enough session/run history to inspect a completed run.
- Support explicit cancellation and bounded time, model calls, tool calls, and token/cost usage. Do not automatically replay uncertain side-effecting tool calls.
- For the first implementation, execute tool calls sequentially; keep the interface suitable for later concurrency and subagent delegation.

## Alternatives considered

- **Framework-managed runner:** faster setup, but hides execution-loop details that are central to this project.
- **Terminal-only interface:** maximally flexible, but weaker structured results, auditing, and capability boundaries. Keep terminal alongside first-class file tools.
- **Build orchestration first:** premature before proving that one runtime can execute tools reliably.

## Consequences

We own the complexity of model/tool message handling, error propagation, limits, and traces. In return, the runtime remains transparent, extensible, and independent of fixed agent roles. Shell working-directory restrictions alone are **not** a security sandbox: initial shell execution requires an isolated environment or explicit user approval until isolation is implemented.

## Acceptance criteria

1. A CLI accepts a user request and runs a general-purpose agent to completion.
2. The agent can inspect files, make an authorized edit, execute a test command, and report the result without a hardcoded task plan.
3. Invalid or unauthorized tool calls fail safely and return structured observations to the agent.
4. Runs emit inspectable traces and stop cleanly on completion, cancellation, or resource limits.
5. The same runtime can be initialized with different instructions and permitted tool sets, without adding specialist-agent classes.

## Out of scope

Subagent spawning, parallel tool calls, durable crash recovery, long-term memory, MCP discovery, and unattended host-level command execution. These require later PAPs.
