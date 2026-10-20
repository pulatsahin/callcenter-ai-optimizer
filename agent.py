
import asyncio

from agents.graph import create_callcenter_agent


THREAD_ID = "callcenter-session-001"


async def main():
    agent, tools = await create_callcenter_agent()

    print("\n=== CALLCENTER AI OPTIMIZER ===")
    print("Model: Qwen3.5:4b")
    print("Hafiza: LangGraph InMemorySaver")
    print("Thread ID:", THREAD_ID)

    print("\nMCP Araclari:")
    for tool in tools:
        print("-", tool.name)

    print("\nCikmak icin 'exit' yazin.\n")

    config = {
        "configurable": {
            "thread_id": THREAD_ID
        },
        "recursion_limit": 20,
    }

    previous_message_count = 0

    while True:
        question = input("Soru: ").strip()

        if question.lower() in ("exit", "quit"):
            break

        if not question:
            continue

        try:
            result = await agent.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": question,
                        }
                    ]
                },
                config=config,
            )

            # Son soruya ait arac cagrilarini goster.
            # Checkpointer onceki mesajlari da dondurur.
            messages = result["messages"]
            previous_message_count = len(messages)

            for message in messages[previous_message_count:]:
                if getattr(message, "tool_calls", None):
                    for call in message.tool_calls:
                        print(
                            "[MCP TOOL CALL]",
                            call["name"],
                            call["args"],
                            )

            final_message = messages[-1]

            content = final_message.content

            if isinstance(content, str):
                print("\nAgent:", content)
            else:
                print("\nAgent:", str(content))

            print()

        except Exception as exc:
            print(
                "\nHata:",
                type(exc).__name__,
                str(exc),
                "\n",
            )


if __name__ == "__main__":
    asyncio.run(main())
