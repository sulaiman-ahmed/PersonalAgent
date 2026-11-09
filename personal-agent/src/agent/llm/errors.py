from enum import StrEnum


class ModelErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    CONTEXT_LIMIT = "context_limit"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    MALFORMED_RESPONSE = "malformed_response"
    OTHER = "other"


_RETRYABLE = {
    ModelErrorCategory.RATE_LIMIT,
    ModelErrorCategory.TIMEOUT,
    ModelErrorCategory.UNAVAILABLE,
}


class ModelError(Exception):
    def __init__(self, category: ModelErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.category in _RETRYABLE

    def __str__(self) -> str:
        return f"[{self.category.value}] {self.message}"
