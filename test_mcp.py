
import asyncio
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient


async def main():
    client = MultiServerMCPClient({
        "callcenter": {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", "mcp_server.server"],
        }
    })

    tools = await client.get_tools()

    print("Bulunan arac sayisi:", len(tools))

    for tool in tools:
        print("Arac:", tool.name)

    capacity_tool = next(
        tool for tool in tools
        if tool.name == "calculate_campaign_capacity"
    )

    result = await capacity_tool.ainvoke({
        "campaign_id": 101,
        "target_hours": 4
    })

    print("\nMCP hesaplama sonucu:")
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
