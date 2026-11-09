# PAP-0002: Tool Architecture and Execution Boundaries

| Field | Value |
|---|---|
| Status | Draft |
| Type | Architecture |
| Scope | Tool interface, authorization, and initial toolset |

## Summary

Provide a **small, extensible tool interface** through which any agent can request actions. All tool calls pass through a shared gateway that validates inputs, enforces permissions, executes the operation, and returns a bounded observation. The agent chooses *which* tools to use; trusted code controls *whether and how* they run.

## Motivation

The agent needs to inspect and modify its environment without hardcoded workflows or specialist roles. A terminal alone is flexible but makes structured errors, auditing, permissions, and safe file modification difficult. Tool-specific logic should not be embedded in the agent loop.

## Decision

- **Common contract:** Each registered tool has a unique name, model-facing description, input schema, and execution handler. The gateway returns a consistent result containing success/failure, bounded output or error details, and an invocation ID.
- **Model-directed selection:** The runtime exposes only tools available to the current run. The model may call them in any order; no mandatory plan or tasklist is required.
- **Per-invocation enforcement:** The gateway validates arguments and checks authorization before executing every call. Permissions include allowed actions **and resource scopes**, not just tool names. Tool descriptions and prompts are not security controls.
- **Initial tools:** `file.read`, `file.search`, `file.edit`, and `terminal.exec`. File edits must detect stale/conflicting content. Terminal commands must have time/output limits and execute only with explicit user approval or inside an enforceably isolated environment.
- **No bypasses:** The terminal's filesystem, network, and credential access must not exceed the permissions granted to the run. A configured working directory alone is not isolation.
- **Observable failures:** Invalid arguments, denied operations, nonzero exits, timeouts, and execution errors become structured tool observations, not silent failures. Record authorization decisions and outcomes, while redacting secrets.
- **Extension point:** Additional tools, including future `agent.spawn` and MCP integrations, use the same gateway. Tool registration does not itself grant permission to invoke a tool.

## Alternatives considered

- **Terminal-only:** Minimal API, but poor structured results and hard-to-enforce granular permissions.
- **Many specialized tools:** Easier individual integrations, but increases model-facing complexity and maintenance.
- **Tool execution inside the agent loop:** Simpler initially, but tightly couples reasoning with authorization and side effects.

## Consequences

The gateway adds implementation overhead but gives us one place for validation, auditing, and authorization. The first release may require interactive command approval until genuine execution isolation exists. We deliberately defer MCP discovery, delegated tool subsets, concurrent tool calls, and sophisticated sandbox implementation choices.

## Acceptance criteria

1. A new tool can be registered without modifying the agent loop.
2. The agent can read, search, and safely edit a permitted workspace and run an approved or isolated command.
3. Malformed, unauthorized, timed-out, and failed calls return consistent observations and appear in the trace.
4. Shell execution cannot silently bypass the configured security boundary; without enforceable isolation, host command execution requires approval.
