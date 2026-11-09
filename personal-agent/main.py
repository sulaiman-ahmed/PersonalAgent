import asyncio
import os

from dotenv import load_dotenv

from agent.llm import GroqProvider, ModelMessage, ModelRequest

load_dotenv()


async def main():
    provider = GroqProvider()
    response = await provider.generate(
        ModelRequest(
            provider=provider.name,
            model=os.environ["GROQ_MODEL"],
            messages=[ModelMessage(role="user", content="hello! what can u do?")],
        )
    )

    print(response.message.content)
    print(f"\nmodel={response.model} usage={response.usage} latency_ms={response.latency_ms:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
