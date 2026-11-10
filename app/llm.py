# The only file that talks to the AI model. Everything else calls ask(),
# so switching provider means editing .env, not the app.

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
    api_key=os.environ["LLM_API_KEY"],
    max_retries=4,   # on a 429 the library waits and tries again
    timeout=30,
)
MODEL = os.getenv("LLM_MODEL", "openai/gpt-oss-120b")


# "system" is the standing instructions, "user" is the task this time.
def ask(system: str, user: str, temperature: float = 0.7,
        model: str | None = None, max_tokens: int | None = None) -> str:
    name = model or MODEL
    extra = {}
    if name.startswith("qwen/"):
        extra["extra_body"] = {"reasoning_effort": "none"}
        if max_tokens:
            extra["max_completion_tokens"] = max_tokens

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=name,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        **extra,
    )
    print(f"[llm] {name}: {time.perf_counter() - start:.1f}s")
    return (response.choices[0].message.content or "").strip()