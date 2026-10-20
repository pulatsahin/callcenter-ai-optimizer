
import os
import sys

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver

from agents.prompts import SYSTEM_PROMPT


load_dotenv()


async def create_callcenter_agent():
    """
    MCP araclarini kesfeder ve LangGraph tabanli
    hafizali agent olusturur.
    """

    client = MultiServerMCPClient({
        "callcenter": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
        }
    })

    tools = await client.get_tools()

    if not tools:
        raise RuntimeError("MCP araclari bulunamadi.")

    model = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
        base_url=os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ),
        temperature=0,
    )

    # Her thread_id icin ayri konusma gecmisi.
    checkpointer = InMemorySaver()

    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    return agent, tools
