import asyncio
import os
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv()

client = AsyncGroq()

async def main():
    response = await client.chat.completions.create(
        
        model=os.environ["GROQ_MODEL"],
        messages=[
            {
                "role": "user",
                "content": "hello! what can u do?"
            }
        ],
    )

    print(response.choices[0].message.content)

if __name__ == "__main__":
    asyncio.run(main())
