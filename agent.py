
import asyncio
import os
import sys

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient


load_dotenv()


SYSTEM_PROMPT = """
Sen CallCenter AI Optimizer isimli bir outbound
kampanya analiz asistanisin.

Gorevlerin:
- Kampanya verilerini MCP araclariyla sorgulamak.
- Kapasite hesaplamalarini Python araclarina yaptirmak.
- Kullanici isterse alternatif port senaryolarini
  karsilastirmak.
- Sonuclari Turkce ve anlasilir sekilde aciklamak.

Kurallar:
- Kampanya verilerini uydurma.
- Sayisal hesaplamalari kendin yapma.
- Gerekli oldugunda MCP araclarini kullan.
- Hesaplama varsayimlarini belirt.
- Port degisikligi yaptigini iddia etme.
- Hata alirsan kullaniciya acikla.
"""


async def main():
    client = MultiServerMCPClient(
        {
            "callcenter": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "mcp_server.server"],
            }
        }
    )

    tools = await client.get_tools()

    print("Kullanilabilir MCP araclari:")
    for tool in tools:
        print("-", tool.name)

    model = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
        base_url=os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434"
        ),
        temperature=0,
    )

    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    print("\nCallCenter AI Optimizer hazir.")
    print("Cikmak icin 'exit' yazin.\n")

    while True:
        question = input("Soru: ").strip()

        if question.lower() in ("exit", "quit"):
            break

        if not question:
            continue

        try:
            result = await agent.ainvoke({
                "messages": [
                    {"role": "user", "content": question}
                ]
            })

            for message in result["messages"]:
                if getattr(message, "tool_calls", None):
                    for call in message.tool_calls:
                        print(
                            "\n[TOOL CALL]",
                            call["name"],
                            call["args"]
                        )

            final_message = result["messages"][-1]
            print("\nAgent:", final_message.content, "\n")

        except Exception as exc:
            print(
                "\nAgent hatasi:",
                type(exc).__name__,
                str(exc)
            )


if __name__ == "__main__":
    asyncio.run(main())
