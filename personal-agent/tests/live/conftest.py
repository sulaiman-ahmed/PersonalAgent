import os

import pytest
from dotenv import load_dotenv

load_dotenv()


@pytest.fixture
def groq_model() -> str:
    if not os.environ.get("GROQ_API_KEY"):
        pytest.skip("GROQ_API_KEY is not set")
    model = os.environ.get("GROQ_MODEL")
    if not model:
        pytest.skip("GROQ_MODEL is not set")
    return model
