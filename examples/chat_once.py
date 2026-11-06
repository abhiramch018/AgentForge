"""Send one system message and one user message to the configured LLM.

Run from the project root:

    python -m examples.chat_once
"""

from app.core.config import load_settings
from app.core.messages import ChatMessage, MessageRole
from app.llm import create_llm_client


def main() -> None:
    settings = load_settings()
    client = create_llm_client(settings)
    messages = [
        ChatMessage(
            role=MessageRole.SYSTEM,
            content="You answer in one short sentence.",
        ),
        ChatMessage(
            role=MessageRole.USER,
            content="What is an LLM client?",
        ),
    ]
    response = client.generate(messages)
    print(response.content)
    print(f"model={response.model}")
    if response.usage is None:
        print("usage=unavailable")
        return
    print(
        "usage="
        f"prompt_tokens={response.usage.prompt_tokens} "
        f"completion_tokens={response.usage.completion_tokens} "
        f"total_tokens={response.usage.total_tokens}"
    )


if __name__ == "__main__":
    main()
