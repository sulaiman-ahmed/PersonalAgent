# PAP-0003: Model Provider Interface and Execution Policy

| Field | Value |
|---|---|
| Type | Architecture |
| Scope | Provider-neutral model inference for the core runtime and future subagents |

## Summary

Introduce a **model-provider boundary** between our custom agent runtime (PAP-0001) and external or local LLM inference.

The runtime sends a canonical model request and receives a canonical response. Provider adapters handle API-specific serialization, authentication, errors, usage, and continuation state.

The same agent loop and tool gateway (PAP-0002) must operate regardless of the selected provider.

## Motivation

PAP-0001 initially references OpenAI's model API directly. This creates unnecessary coupling between the harness and a specific provider.

We want to experiment with different hosted and local models, compare their performance and costs, and avoid dependence on a single provider.

The design must preserve the project's underlying philosophy: **models should control their reasoning, tool choices, and stopping decisions, while the runtime independently enforces tool permissions and execution boundaries.**

## Decision

**1. Own a provider-neutral interface.**

Implement an asynchronous `ModelProvider.generate(request) -> ModelResponse` interface.

Provider-specific SDK types must not appear in the agent loop, tool gateway, or session management logic.

**2. Normalize model inputs and outputs.**

A canonical request includes:

- Ordered conversation messages and instructions
- Tool definitions from the shared registry
- Selected model and provider
- Optional generation parameters

A canonical response includes:

- Assistant text and proposed tool calls
- Tool-call identifiers, names, and arguments
- Completion reason
- Optional token usage
- Adapter-owned continuation metadata

Responses may contain multiple tool calls and text together.

**3. Support model-specific capabilities.**

Maintain capability metadata for each configured model, including native tool calling, structured output, parallel tool calling, streaming, context limits, and multimodal input.

The runtime must verify required capabilities before inference. Unsupported capabilities must be rejected or explicitly disabled rather than silently approximated.

A model without native tool calling may support text-only tasks, but must not be treated as a fully capable tool-using agent through unvalidated text parsing.

**4. Preserve independent tool execution.**

Provider adapters translate tool schemas into provider-native formats and return normalized tool requests.

They do not execute local tools. All execution proceeds through PAP-0002's shared tool gateway, including argument validation, authorization, execution, and error reporting.

Provider-hosted agent runners and tool execution remain outside this abstraction.

**5. Configure model selection per run.**

A run selects a provider, model identifier, endpoint where applicable, and credentials through configuration.

The model selection remains fixed for that run.

Future subagents may use different explicitly configured models, but automatic routing and mid-run switching are not implemented here.

**6. Normalize errors, metrics, and usage.**

Provider adapters return consistent categories for authentication errors, rate limits, timeouts, unavailable providers, context-limit failures, unsupported capabilities, and malformed responses.

Transient inference failures can be retried under a bounded policy before a model response has been accepted. Previously executed tool side effects must never be replayed as part of model retry handling.

Record provider, model, latency, token usage when available, and estimated cost when reliable pricing information is configured. Unknown usage or cost is never treated as zero.

**7. Validate the design across real providers.**

Proposed initial providers:

- **Groq:** first accessible hosted inference integration
- **Gemini:** second integration to validate different API semantics
- **OpenAI:** additional provider adapter when API access is available
- **Ollama:** future local inference adapter

Also implement a deterministic fake model provider for repeatable unit tests.

The goal is to understand provider translation directly rather than outsource the core abstraction to a framework.

## Proposed interface

`ModelProvider.generate(request: ModelRequest) -> ModelResponse`

Supporting types:

- `ModelMessage` — provider-neutral conversation messages and tool results
- `ModelRequest` — messages, instructions, tools, selected model, options
- `ModelResponse` — assistant message, proposed tool calls, stop reason, usage
- `ModelCapabilities` — supported features and known limitations
- `ModelError` — normalized failures and retryability

This represents a minimum common vocabulary, not a requirement to remove advanced provider-specific capabilities.

## Execution flow

1. The run loads its configured provider and model.
2. The runtime validates required model capabilities.
3. The agent loop constructs a canonical request.
4. The adapter translates the request and invokes its model API.
5. The adapter normalizes the response.
6. The tool gateway validates and executes requested tools.
7. Tool results return to the shared conversation, and the loop continues until completion or an execution limit.

## Alternatives considered

**Direct OpenAI or OpenAI-compatible API integration:** Faster initially, but introduces hidden dependencies on one API's request and response semantics.

**A universal model gateway such as LiteLLM:** Simplifies integrations but obscures translation details that we want to understand and control. It could be adopted later behind our interface.

**Separate agent loops per provider:** Avoids adapters but duplicates orchestration and authorization logic.

**Immediate intelligent model routing:** Potentially valuable for optimizing cost and capability, but introduces additional policy and reliability concerns before the foundational runtime is established.

## Consequences

We own the complexity of provider translation, feature support, continuation state, errors, and conformance tests.

Provider portability does not guarantee identical model behavior. Different models may produce different plans or tool calls for the same request.

The guaranteed invariant is that the **agent runtime's execution contract remains unchanged**, regardless of provider.

Provider-specific continuation metadata remains within the adapter; automatic preservation of hidden model state when switching providers is not guaranteed.

## Acceptance criteria

1. The agent loop contains no provider-specific SDK dependencies.
2. A deterministic fake provider passes tests for text, tool calls, results, errors, and termination.
3. Two providers using distinct API semantics successfully complete the same text and tool-use smoke workflows.
4. Switching providers requires configuration changes only, not agent-loop changes.
5. Unsupported model capabilities fail clearly, and invalid tool proposals cannot bypass the shared gateway.
6. Model identity, failures, and available usage data are captured in structured traces.
7. No automatic fallback, hidden model switching, or replay of previously executed side effects occurs.

## Out of scope

Intelligent model routing, automatic failover, model-performance benchmarking, parallel multi-model inference, mid-run cross-provider handoff, provider-hosted tool execution, multimodal normalization, and streaming UI protocols.

These remain possible future extensions.

## Dependencies and compatibility

**PAP-0001:** Its direct OpenAI dependency must be replaced with the `ModelProvider` interface. If PAP-0001 has already been accepted and frozen, this proposal supersedes that decision rather than modifying the accepted record.

**PAP-0002:** Its tool gateway remains model-independent. Tool schemas are translated by provider adapters, while validation and execution remain centralized.

**Future subagent PAP:** Every spawned agent uses the same model-provider contract, regardless of which model it is assigned.