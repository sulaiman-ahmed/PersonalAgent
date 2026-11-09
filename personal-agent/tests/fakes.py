from agent.llm import (
    ModelCapabilities,
    ModelError,
    ModelRequest,
    ModelResponse,
    validate_request,
)


class FakeProvider:
    # Deterministic scripted provider: returns (or raises) script items in order
    # and records every request it receives.
    name = "fake"

    def __init__(
        self,
        script: list[ModelResponse | ModelError],
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        self._script = list(script)
        self._capabilities = capabilities or ModelCapabilities(
            tool_calling=True, parallel_tool_calls=True
        )
        self.requests: list[ModelRequest] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return self._capabilities

    async def generate(self, request: ModelRequest) -> ModelResponse:
        validate_request(self, request)
        self.requests.append(request)
        if not self._script:
            raise AssertionError("FakeProvider script exhausted")
        item = self._script.pop(0)
        if isinstance(item, ModelError):
            raise item
        return item
