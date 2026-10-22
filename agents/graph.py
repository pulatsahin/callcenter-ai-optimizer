
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_ollama import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient

from agents.prompts import SYSTEM_PROMPT
from rag.retriever import search_campaign_policies


load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
MEMORY_DB = ROOT / "data" / "agent_memory.db"


@tool
def search_policy_documents(query: str) -> str:
    """
    Outbound kampanya retry, port artirimi,
    calisma saatleri ve operasyon politikalarini
    RAG dokumanlarindan arar.
    """
    return search_campaign_policies(query)


async def create_callcenter_agent(checkpointer):

    client = MultiServerMCPClient(
        {
            "callcenter": {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "mcp_server.server"],
                "cwd": str(ROOT),
            }
        }
    )

    mcp_tools = await client.get_tools()

    if not mcp_tools:
        raise RuntimeError(
            "MCP araclari bulunamadi."
        )

    model = ChatOllama(
        model=os.getenv("OLLAMA_MODEL", "qwen3.5:4b"),
        base_url=os.getenv(
            "OLLAMA_BASE_URL",
            "http://localhost:11434",
        ),
        temperature=0,
        reasoning=False,
        num_ctx=8192,
        num_predict=1024,
    )

    all_tools = [
        *mcp_tools,
        search_policy_documents,
    ]

    agent = create_agent(
        model=model,
        tools=all_tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
    )

    return agent, all_tools
