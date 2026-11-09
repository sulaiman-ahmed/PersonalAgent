from typing import Protocol

from .errors import ModelError, ModelErrorCategory
from .types import ModelCapabilities, ModelRequest, ModelResponse


class ModelProvider(Protocol):
    name: str

    def capabilities(self, model: str) -> ModelCapabilities: ...

    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def validate_request(provider: ModelProvider, request: ModelRequest) -> None:
    # Reject requests that need capabilities the model lacks.
    caps = provider.capabilities(request.model)
    if request.tools and not caps.tool_calling:
        raise ModelError(
            ModelErrorCategory.UNSUPPORTED_CAPABILITY,
            f"{provider.name}/{request.model} does not support native tool calling",
        )
