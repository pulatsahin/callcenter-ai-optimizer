"""CallCenter AI Optimizer terminal arayuzu.

Normal kullanim: python agent.py
Teknik izleme:  python agent.py --debug
Sohbete devam:  python agent.py --thread-id <onceki-id>
"""

import argparse
import asyncio
import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from agents.graph import MEMORY_DB, create_callcenter_agent

ISTANBUL = ZoneInfo("Europe/Istanbul")
FINISH_TIME_TOOLS = {
    "calculate_campaign_finish_time",
    "simulate_campaign_finish_time",
}


def extract_text(content):
    """LangChain mesaj icerigini okunabilir metne donusturur."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts).strip()
    return ""


def parse_tool_result(content):
    """MCP sonucunu mumkunse JSON sozluge donusturur."""
    text = extract_text(content)
    if not text:
        return None
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return result if isinstance(result, dict) else None


def format_verified_finish_time(tool_result):
    """MCP'nin ISO tarihini saniye hassasiyetiyle dogrular ve formatlar."""
    if not isinstance(tool_result, dict):
        return None
    schedule = tool_result.get("schedule")
    if not isinstance(schedule, dict):
        return None
    estimated_finish = schedule.get("estimated_finish")
    if not estimated_finish:
        return None
    try:
        finish_datetime = datetime.fromisoformat(estimated_finish)
    except (ValueError, TypeError):
        return None
    if finish_datetime.tzinfo is None:
        return None
    finish_datetime = finish_datetime.astimezone(ISTANBUL)
    return {
        "campaign_id": tool_result.get("campaign_id"),
        "formatted_finish": finish_datetime.strftime("%d.%m.%Y %H:%M:%S"),
        "timezone": "Europe/Istanbul",
        "calculation_type": tool_result.get("calculation_type", "UNKNOWN"),
        "actual_ports": tool_result.get("actual_ports"),
        "calculation_ports": tool_result.get("calculation_ports"),
        "source_iso": estimated_finish,
    }


def print_verified_finish_time(result, debug=False):
    """LLM metninden bagimsiz, MCP ile dogrulanmis sonucu gosterir."""
    if not result:
        return
    print("\n[Dogrulanmis MCP bitis hesabi]")
    print("Kampanya:", result["campaign_id"])
    print("Hesaplama tipi:", result["calculation_type"])
    print("Gercek port:", result["actual_ports"])
    print("Hesaplamada kullanilan port:", result["calculation_ports"])
    print("Tahmini bitis:", result["formatted_finish"], "(Europe/Istanbul)")
    if debug:
        print("Kaynak ISO:", result["source_iso"])


def build_parser():
    parser = argparse.ArgumentParser(
        description="CallCenter AI Optimizer - terminal AI asistani"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="LangGraph node, tool call ve tool result izlerini goster",
    )
    parser.add_argument(
        "--thread-id", metavar="ID",
        help="Kayitli bir sohbetin thread_id degeriyle devam et",
    )
    return parser


async def main(debug=False, thread_id=None):
    thread_id = thread_id or f"chat-{uuid4().hex[:8]}"
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSqliteSaver.from_conn_string(str(MEMORY_DB)) as checkpointer:
        await checkpointer.setup()
        agent, tools = await create_callcenter_agent(checkpointer)
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 30,
        }

        print("\n=== CALLCENTER AI OPTIMIZER v1.0 ===")
        print("AI Asistan hazir.")
        print("Sohbet ID:", thread_id)
        print("Onceki sohbete donmek icin: python agent.py --thread-id", thread_id)
        print("Cikmak icin 'exit' yazin. Yardim: python agent.py --help")
        if debug:
            print("\n[DEBUG] Teknik izleme acik")
            print("[DEBUG] Memory DB:", MEMORY_DB)
            print("[DEBUG] Yuklenen araclar:")
            for tool in tools:
                print("  -", tool.name)

        while True:
            try:
                question = input("\nSorunuz: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGorusmek uzere.")
                break

            if question.lower() in ("exit", "quit"):
                print("Gorusmek uzere.")
                break
            if not question:
                continue

            answer_received = False
            verified_results = []
            pending_tool_calls = {}
            if debug:
                print("[DEBUG] Agent baslatildi...")

            try:
                async for event in agent.astream(
                    {"messages": [{"role": "user", "content": question}]},
                    config=config,
                    stream_mode="updates",
                ):
                    for node_name, update in event.items():
                        if debug:
                            print(f"[DEBUG] Node: {node_name}", flush=True)
                        if not isinstance(update, dict):
                            continue
                        for message in update.get("messages", []):
                            tool_calls = getattr(message, "tool_calls", None)
                            if tool_calls:
                                for call in tool_calls:
                                    tool_name = call.get("name")
                                    tool_call_id = call.get("id")
                                    if tool_call_id:
                                        pending_tool_calls[tool_call_id] = tool_name
                                    if debug:
                                        print("[TOOL CALL]", tool_name, call.get("args"), flush=True)

                            if getattr(message, "type", None) == "tool":
                                if debug:
                                    print("[TOOL RESULT]", extract_text(message.content)[:3000], flush=True)
                                tool_call_id = getattr(message, "tool_call_id", None)
                                tool_name = pending_tool_calls.get(tool_call_id)
                                if tool_name not in FINISH_TIME_TOOLS:
                                    continue
                                if getattr(message, "status", "success") == "error":
                                    continue
                                parsed_result = parse_tool_result(message.content)
                                verified = format_verified_finish_time(parsed_result)
                                if verified:
                                    verified_results.append(verified)
                                continue

                            if getattr(message, "type", None) != "ai":
                                continue
                            metadata = getattr(message, "response_metadata", {}) or {}
                            done_reason = metadata.get("done_reason")
                            if debug:
                                print("[DEBUG] done_reason:", done_reason)
                            if tool_calls:
                                continue
                            answer = extract_text(message.content)
                            if done_reason == "length":
                                print("[UYARI] Model yanit uzunlugu sinirina ulasti.")
                                if answer and debug:
                                    print("[KISMI YANIT]\n", answer)
                                continue
                            if answer:
                                print("\nAI:", answer, flush=True)
                                answer_received = True

                if not answer_received:
                    print("[UYARI] Gecerli nihai yanit uretilemedi.")
                for result in verified_results:
                    print_verified_finish_time(result, debug=debug)
            except Exception as exc:
                print("[HATA]", type(exc).__name__, str(exc), flush=True)
                if debug:
                    import traceback
                    traceback.print_exc()
            if debug:
                print("-" * 55)


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    asyncio.run(main(debug=arguments.debug, thread_id=arguments.thread_id))
