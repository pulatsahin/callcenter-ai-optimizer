
import asyncio
import json
import hashlib
import tempfile
import os
import sqlite3

from datetime import datetime, date, time, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.graph import MEMORY_DB, create_callcenter_agent
from database.init_db import DB_PATH
from services.capacity_engine import calculate_capacity
from services.observed_capacity import forecast_observed_capacity
from services.performance_variance import analyze_hourly_variance
from services.operations_brief import build_operations_brief, build_agent_question
from services.port_optimizer import optimize_port_allocation
from services.retry_capacity import analyze_retry_capacity
from services.retry_policy import simulate_retry_policy
from services.retry_dispositions import analyze_retry_dispositions
from services.outbound_importer import (
    load_outbound_csv, summarize_outbound, OutboundImportError, MAX_FILE_BYTES,
)
from services.schedule_planner import (
    estimate_finish_datetime,
    load_holiday_calendar,
)


ROOT = Path(__file__).resolve().parent
HOLIDAY_DIR = ROOT / "data"
ISTANBUL = ZoneInfo("Europe/Istanbul")

FINISH_TOOLS = {
    "calculate_campaign_finish_time",
    "simulate_campaign_finish_time",
}

st.set_page_config(
    page_title="CallCenter AI Optimizer",
    page_icon="📞",
    layout="wide",
)

st.title("📞 CallCenter AI Optimizer")
st.caption(
    "Genesys Engage | LangGraph | MCP | "
    "RAG | Ollama | Çok Yıllı Tatil Takvimi"
)


# ==================================================
# DATABASE AND CAPACITY
# ==================================================

def load_campaigns():
    if not DB_PATH.is_file():
        raise FileNotFoundError(
            f"Veritabani bulunamadi: {DB_PATH}"
        )

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY id"
        ).fetchall()

    return [dict(row) for row in rows]


def get_capacity(campaign, ports=None):
    selected_ports = (
        campaign["ports"]
        if ports is None
        else int(ports)
    )

    remaining = (
        campaign["total_records"]
        - campaign["completed_records"]
    )

    return calculate_capacity(
        remaining_records=remaining,
        ports=selected_ports,
        answer_rate=campaign["answer_rate"],
        avg_ivr_seconds=campaign["avg_ivr_seconds"],
        avg_no_answer_seconds=campaign["avg_no_answer_seconds"],
        overhead_seconds=campaign["overhead_seconds"],
    )


def campaign_signature(campaign):
    return (
        campaign["id"],
        campaign["name"],
        campaign["total_records"],
        campaign["completed_records"],
        campaign["ports"],
        campaign["answer_rate"],
        campaign["avg_ivr_seconds"],
        campaign["avg_no_answer_seconds"],
        campaign["overhead_seconds"],
    )


# ==================================================
# HOLIDAY CALENDARS
# ==================================================

def available_calendar_years():
    """
    Data klasorundeki yillik takvimleri bulur.
    Yalnizca gecerli takvimleri kullanir.
    """
    years = []
    invalid = []

    for path in sorted(
        HOLIDAY_DIR.glob("holidays_tr_*.json")
    ):
        try:
            year = int(path.stem.rsplit("_", 1)[-1])
            load_holiday_calendar(year)
            years.append(year)
        except (ValueError, TypeError, OSError,
                json.JSONDecodeError) as exc:
            invalid.append(
                f"{path.name}: {exc}"
            )

    return sorted(set(years)), invalid


def calendar_files_signature():
    """
    Takvim dosyalari degisirse eski simülasyon
    sonucunu gecersiz kilar.
    """
    signature = []

    for path in sorted(
        HOLIDAY_DIR.glob("holidays_tr_*.json")
    ):
        stat = path.stat()
        signature.append(
            (
                path.name,
                stat.st_mtime_ns,
                stat.st_size,
            )
        )

    return tuple(signature)


def format_datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)

    if value.tzinfo is None:
        raise ValueError(
            "Tarih saat dilimi icermeli."
        )

    return value.astimezone(ISTANBUL).strftime(
        "%d.%m.%Y %H:%M:%S"
    )


def combine_istanbul(selected_date, selected_time):
    return datetime.combine(
        selected_date,
        selected_time,
        tzinfo=ISTANBUL,
    )


def estimate_without_holidays(
    completion_hours,
    start_datetime,
):
    """
    Karsilastirma modeli:
    Hafta ici 09:00-18:00.
    Resmi tatiller yok.
    """
    if completion_hours < 0:
        raise ValueError(
            "Sure negatif olamaz."
        )

    current = start_datetime.astimezone(ISTANBUL)
    remaining = timedelta(hours=completion_hours)

    if remaining == timedelta(0):
        return current

    while remaining > timedelta(0):

        if current.weekday() >= 5:
            current = datetime.combine(
                current.date() + timedelta(days=1),
                time(9, 0),
                tzinfo=ISTANBUL,
            )
            continue

        day_start = datetime.combine(
            current.date(),
            time(9, 0),
            tzinfo=ISTANBUL,
        )

        day_end = datetime.combine(
            current.date(),
            time(18, 0),
            tzinfo=ISTANBUL,
        )

        if current < day_start:
            current = day_start

        if current >= day_end:
            current = datetime.combine(
                current.date() + timedelta(days=1),
                time(9, 0),
                tzinfo=ISTANBUL,
            )
            continue

        consumed = min(
            day_end - current,
            remaining,
        )

        current += consumed
        remaining -= consumed

    return current


def run_schedule_simulation(
    campaign,
    selected_ports,
    start_datetime,
):
    capacity = get_capacity(
        campaign,
        ports=selected_ports,
    )

    schedule = estimate_finish_datetime(
        completion_hours=capacity["completion_hours"],
        start_datetime=start_datetime,
    )

    without_holidays = estimate_without_holidays(
        completion_hours=capacity["completion_hours"],
        start_datetime=start_datetime,
    )

    with_holidays = datetime.fromisoformat(
        schedule["estimated_finish"]
    )

    difference = with_holidays - without_holidays

    return {
        "campaign_id": campaign["id"],
        "campaign_name": campaign["name"],
        "start_datetime": start_datetime.astimezone(ISTANBUL).isoformat(),
        "actual_ports": campaign["ports"],
        "calculation_ports": selected_ports,
        "calculation_type": (
            "ACTUAL"
            if selected_ports == campaign["ports"]
            else "SIMULATION"
        ),
        "capacity": capacity,
        "schedule": schedule,
        "without_holidays": without_holidays,
        "with_holidays": with_holidays,
        "difference_hours": round(
            difference.total_seconds() / 3600,
            2,
        ),
    }


# ==================================================
# STATE INVALIDATION
# ==================================================

def clear_schedule():
    st.session_state.pop(
        "schedule_result", None
    )
    st.session_state.pop(
        "schedule_result_signature", None
    )


def clear_retry():
    st.session_state.pop("retry_result", None)
    st.session_state.pop("retry_result_signature", None)


def retry_campaign_changed():
    clear_retry()
    cid = st.session_state.get("retry_campaign")
    campaign = st.session_state.get("campaign_lookup", {}).get(cid)
    if campaign is not None:
        st.session_state.retry_ports = int(campaign["ports"])


def clear_optimization():
    st.session_state.pop(
        "optimization_result", None
    )
    st.session_state.pop(
        "optimization_result_signature", None
    )


def simulation_campaign_changed():
    clear_schedule()

    campaign_id = st.session_state.get(
        "simulation_campaign"
    )

    if campaign_id is not None:
        campaign = st.session_state.get(
            "campaign_lookup", {}
        ).get(campaign_id)

        if campaign:
            st.session_state.simulation_ports = int(
                campaign["ports"]
            )


def schedule_signature(
    campaign,
    selected_date,
    selected_time,
    ports,
):
    return (
        campaign_signature(campaign),
        selected_date.isoformat(),
        selected_time.isoformat(),
        int(ports),
        calendar_files_signature(),
    )


def optimization_signature(
    campaigns,
    total_ports,
):
    return (
        tuple(
            campaign_signature(c)
            for c in campaigns
        ),
        int(total_ports),
    )


# ==================================================
# LANGGRAPH / MCP
# ==================================================

def extract_text(content):
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []

        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))

        return "\n".join(parts).strip()

    return ""


def parse_tool_result(content):
    text = extract_text(content)

    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        return None

    return result if isinstance(result, dict) else None


def verified_finish(result):
    if not isinstance(result, dict):
        return None

    schedule = result.get("schedule")

    if not isinstance(schedule, dict):
        return None

    iso_value = schedule.get("estimated_finish")

    if not iso_value:
        return None

    try:
        finish = format_datetime(iso_value)
    except (ValueError, TypeError):
        return None

    return {
        "campaign_id": result.get("campaign_id"),
        "actual_ports": result.get("actual_ports"),
        "calculation_ports": result.get(
            "calculation_ports"
        ),
        "calculation_type": result.get(
            "calculation_type"
        ),
        "finish": finish,
        "holiday_calendars": schedule.get(
            "holiday_calendars", []
        ),
        "skipped_holidays": schedule.get(
            "skipped_holidays", []
        ),
        "partial_holidays": schedule.get(
            "partial_holidays", []
        ),
    }


async def ask_agent_async(question, thread_id):
    MEMORY_DB.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    answer = ""
    verified_results = []
    used_tools = []
    warnings = []
    pending_calls = {}

    async with AsyncSqliteSaver.from_conn_string(
        str(MEMORY_DB)
    ) as checkpointer:

        await checkpointer.setup()

        agent, _ = await create_callcenter_agent(
            checkpointer
        )

        config = {
            "configurable": {
                "thread_id": thread_id,
            },
            "recursion_limit": 30,
        }

        async for event in agent.astream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": question,
                    }
                ]
            },
            config=config,
            stream_mode="updates",
        ):

            for _, update in event.items():
                if not isinstance(update, dict):
                    continue

                for message in update.get("messages", []):

                    calls = getattr(
                        message,
                        "tool_calls",
                        None,
                    )

                    if calls:
                        for call in calls:
                            name = call.get("name")
                            call_id = call.get("id")

                            if name:
                                used_tools.append(name)

                            if call_id:
                                pending_calls[call_id] = name

                    if message.type == "tool":
                        call_id = getattr(
                            message,
                            "tool_call_id",
                            None,
                        )

                        name = pending_calls.get(call_id)

                        if name not in FINISH_TOOLS:
                            continue

                        if getattr(
                            message,
                            "status",
                            "success",
                        ) == "error":
                            warnings.append(
                                f"{name} araci hata dondurdu."
                            )
                            continue

                        parsed = parse_tool_result(
                            message.content
                        )

                        verified = verified_finish(parsed)

                        if verified:
                            verified_results.append(
                                verified
                            )

                    if message.type != "ai" or calls:
                        continue

                    metadata = getattr(
                        message,
                        "response_metadata",
                        {},
                    )

                    text = extract_text(message.content)

                    if metadata.get("done_reason") == "length":
                        warnings.append(
                            "Model yaniti uzunluk sinirinda kesildi."
                        )
                        continue

                    if text:
                        answer = text

    if not answer:
        answer = (
            "Agent tamamlanmis bir yanit uretemedi. "
            "Arac sonuclarini kontrol edin."
        )

    return {
        "answer": answer,
        "verified_results": verified_results,
        "tools": used_tools,
        "warnings": warnings,
    }


def run_agent(question, thread_id):
    return asyncio.run(
        ask_agent_async(question, thread_id)
    )


def show_verified_result(result):
    with st.container(border=True):
        st.subheader("Doğrulanmış MCP sonucu")

        st.metric(
            "Tahmini bitiş",
            result["finish"],
        )

        a, b, c = st.columns(3)

        a.metric(
            "Gerçek port",
            result["actual_ports"],
        )
        b.metric(
            "Hesaplama portu",
            result["calculation_ports"],
        )
        c.metric(
            "Hesaplama tipi",
            result["calculation_type"],
        )

        calendars = result.get(
            "holiday_calendars", []
        )

        st.caption(
            "Europe/Istanbul | "
            "Takvimler: "
            + (", ".join(calendars) or "MCP sonucu")
        )

        skipped = result.get(
            "skipped_holidays", []
        )

        if skipped:
            st.info(
                "Atlanan tatiller: "
                + ", ".join(
                    f"{h['date']} ({h['name']})"
                    for h in skipped
                )
            )


# ==================================================
# SESSION STATE
# ==================================================

if "thread_id" not in st.session_state:
    st.session_state.thread_id = (
        f"dashboard-{uuid4().hex}"
    )

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ==================================================
# SIDEBAR
# ==================================================

calendar_years, calendar_errors = (
    available_calendar_years()
)

with st.sidebar:
    st.header("⚙️ Sistem")

    st.write("LLM: Qwen3.5:4b")
    st.write("Orkestrasyon: LangGraph")
    st.write("Araçlar: MCP")
    st.write("Veritabanı: SQLite")

    st.write(
        "Yüklü takvimler:",
        ", ".join(map(str, calendar_years))
        or "Yok",
    )

    for error in calendar_errors:
        st.warning(error)

    if st.button(
        "Yeni AI sohbeti",
        use_container_width=True,
    ):
        st.session_state.thread_id = (
            f"dashboard-{uuid4().hex}"
        )
        st.session_state.chat_history = []
        st.rerun()

    st.caption(
        "Simülasyonlar veritabanını değiştirmez."
    )


# ==================================================
# LOAD CAMPAIGNS
# ==================================================

try:
    campaigns = load_campaigns()
except Exception as exc:
    st.error(str(exc))
    st.stop()

if not campaigns:
    st.warning("Kampanya bulunamadi.")
    st.stop()

campaign_by_id = {
    campaign["id"]: campaign
    for campaign in campaigns
}

st.session_state.campaign_lookup = campaign_by_id

campaign_options = list(campaign_by_id.keys())


# ==================================================
# KPI
# ==================================================

total_records = sum(
    c["total_records"] for c in campaigns
)

completed = sum(
    c["completed_records"] for c in campaigns
)

remaining = total_records - completed

total_ports = sum(
    c["ports"] for c in campaigns
)

k1, k2, k3, k4 = st.columns(4)

k1.metric("Kampanya", len(campaigns))
k2.metric("Toplam kayıt", f"{total_records:,}")
k3.metric("Kalan kayıt", f"{remaining:,}")
k4.metric("Mevcut port", total_ports)


# ==================================================
# FOUR MAIN SECTIONS
# ==================================================

overview_tab, analysis_tab, optimization_tab, ai_tab = st.tabs(
    [
        "🏠 Genel Bakış",
        "📊 Kampanya Analizi",
        "⚙️ Optimizasyon",
        "🤖 AI Asistan",
    ]
)

# Dört ana bölüm altında mevcut sekiz analiz korunur.
# Veriler ve hesaplama servisleri değiştirilmemiştir.


# ==================================================
# TAB 1 - CAMPAIGNS
# ==================================================

with overview_tab:
    st.subheader("Kampanya Genel Durumu")

    rows = []

    for campaign in campaigns:
        capacity = get_capacity(campaign)

        rows.append({
            "ID": campaign["id"],
            "Kampanya": campaign["name"],
            "Toplam": campaign["total_records"],
            "Tamamlanan": campaign["completed_records"],
            "Kalan": capacity["remaining_records"],
            "Port": campaign["ports"],
            "Cevaplanma (%)": campaign["answer_rate"],
            "Arama/Saat": capacity["calls_per_hour"],
            "Tahmini Süre (saat)": capacity["completion_hours"],
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
    )

    fig = px.bar(
        df,
        x="Kampanya",
        y="Kalan",
        text="Kalan",
        title="Kampanyalara Göre Kalan Kayıt",
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
    )

    st.caption(
        "Kapasite tahminleri retry yukunu "
        "otomatik olarak icermez."
    )


# ==================================================
# TAB 2 - CAMPAIGN DETAILS
# ==================================================

with analysis_tab:
    st.subheader("Kampanya Detay Analizi")

    selected_id = st.selectbox(
        "Kampanya seç",
        options=campaign_options,
        format_func=lambda cid: (
            f"{cid} - {campaign_by_id[cid]['name']}"
        ),
        key="detail_campaign",
    )

    campaign = campaign_by_id[selected_id]
    capacity = get_capacity(campaign)

    remaining_campaign = capacity["remaining_records"]

    completion_percent = (
        campaign["completed_records"]
        / campaign["total_records"] * 100
        if campaign["total_records"] > 0
        else 100
    )

    st.progress(
        min(1.0, max(0.0, completion_percent / 100)),
        text=f"Tamamlanma: %{completion_percent:.1f}",
    )

    d1, d2, d3, d4 = st.columns(4)

    d1.metric("Kalan kayıt", remaining_campaign)
    d2.metric("Mevcut port", campaign["ports"])
    d3.metric(
        "Cevaplanma",
        f"%{campaign['answer_rate']}",
    )
    d4.metric(
        "Arama / saat",
        capacity["calls_per_hour"],
    )

    left, right = st.columns(2)

    with left:
        pie_df = pd.DataFrame(
            [
                {
                    "Durum": "Tamamlanan",
                    "Kayıt": campaign["completed_records"],
                },
                {
                    "Durum": "Kalan",
                    "Kayıt": remaining_campaign,
                },
            ]
        )

        fig = px.pie(
            pie_df,
            names="Durum",
            values="Kayıt",
            title="Kayıt Dağılımı",
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )

    with right:
        st.write("**Kapasite Parametreleri**")

        st.json({
            "avg_ivr_seconds": campaign[
                "avg_ivr_seconds"
            ],
            "avg_no_answer_seconds": campaign[
                "avg_no_answer_seconds"
            ],
            "overhead_seconds": campaign[
                "overhead_seconds"
            ],
            "avg_port_seconds": capacity[
                "avg_port_seconds"
            ],
            "completion_hours": capacity[
                "completion_hours"
            ],
            "required_ports_for_4h": capacity[
                "required_ports"
            ],
        })

    st.info(
        "Bu analizde mevcut portlar kullanılır. "
        "Alternatif portları Bitiş Simülatörü "
        "sekmesinde deneyebilirsin."
    )


# ==================================================
# TAB 3 - PORT OPTIMIZATION
# ==================================================

with optimization_tab:
    st.subheader("Port Dağılımı Optimizasyonu")

    active_campaigns = [
        c for c in campaigns
        if c["total_records"] > c["completed_records"]
    ]

    if not active_campaigns:
        st.info("Aktif kampanya bulunmuyor.")

    else:
        requested_ports = st.number_input(
            "Dağıtılacak toplam port",
            min_value=len(active_campaigns),
            max_value=500,
            value=max(30, len(active_campaigns)),
            step=1,
            key="requested_optimization_ports",
            on_change=clear_optimization,
        )

        current_signature = optimization_signature(
            active_campaigns,
            requested_ports,
        )

        if (
            st.session_state.get(
                "optimization_result_signature"
            ) != current_signature
        ):
            clear_optimization()

        if st.button(
            "Portları optimize et",
            type="primary",
        ):
            try:
                result = optimize_port_allocation(
                    campaigns=active_campaigns,
                    total_ports=int(requested_ports),
                )

                st.session_state.optimization_result = result
                st.session_state.optimization_result_signature = (
                    current_signature
                )

            except Exception as exc:
                clear_optimization()
                st.error(str(exc))

        optimization = st.session_state.get(
            "optimization_result"
        )

        if optimization:
            allocation = optimization.get(
                "allocation", []
            )

            if allocation:
                opt_df = pd.DataFrame(allocation)

                opt_df = opt_df.rename(
                    columns={
                        "campaign_name": "Kampanya",
                        "original_ports": "Mevcut",
                        "recommended_ports": "Önerilen",
                        "estimated_hours": "Süre (saat)",
                    }
                )

                fig = px.bar(
                    opt_df,
                    x="Kampanya",
                    y=["Mevcut", "Önerilen"],
                    barmode="group",
                    title="Port Karşılaştırması",
                )

                st.plotly_chart(
                    fig,
                    use_container_width=True,
                )

                st.dataframe(
                    opt_df,
                    hide_index=True,
                    use_container_width=True,
                )

                st.metric(
                    "Tahmini en geç tamamlanma",
                    (
                        f"{optimization['estimated_finish_hours']}"
                        " saat"
                    ),
                )

                st.warning(
                    "Öneriler simülasyondur. "
                    "Veritabanındaki portlar değişmez."
                )

        else:
            st.info(
                "Güncel parametrelerle optimizasyon "
                "yapmak için butona bas."
            )


# ==================================================
# TAB 4 - MULTIYEAR FINISH SIMULATOR
# ==================================================

with optimization_tab:
    st.subheader("📅 Kampanya Bitiş Simülatörü")

    st.caption(
        "2026 ve 2027 dahil, yüklenmiş takvimlerin "
        "desteklediği yıllarda hesaplama yapılabilir."
    )

    if not calendar_years:
        st.error(
            "Geçerli Türkiye tatil takvimi bulunamadı."
        )

    else:
        selected_id = st.selectbox(
            "Kampanya",
            options=campaign_options,
            format_func=lambda cid: (
                f"{cid} - {campaign_by_id[cid]['name']}"
            ),
            key="simulation_campaign",
            on_change=simulation_campaign_changed,
        )

        campaign = campaign_by_id[selected_id]

        default_date = (
            date(2026, 10, 26)
            if 2026 in calendar_years
            else date(calendar_years[0], 1, 1)
        )

        c1, c2, c3 = st.columns(3)

        with c1:
            start_date = st.date_input(
                "Başlangıç tarihi",
                value=default_date,
                min_value=date(
                    calendar_years[0], 1, 1
                ),
                max_value=date(
                    calendar_years[-1], 12, 31
                ),
                key="simulation_date",
                on_change=clear_schedule,
            )

        with c2:
            start_time = st.time_input(
                "Başlangıç saati",
                value=time(16, 0),
                step=1,
                key="simulation_time",
                on_change=clear_schedule,
            )

        with c3:
            scenario_ports = st.number_input(
                "Hesaplama port sayısı",
                min_value=1,
                max_value=500,
                value=int(campaign["ports"]),
                step=1,
                key="simulation_ports",
                on_change=clear_schedule,
            )

        st.caption(
            f"SQLite'daki gerçek port: "
            f"{campaign['ports']}. "
            "Simülasyon veritabanını değiştirmez."
        )

        # Date/time input degerlerini dogrudan goster.
        # Tarayicida secilen tarih ile Python'a ulasan
        # tarih farkliysa hesaplamadan once gorulur.
        selected_start = combine_istanbul(
            start_date,
            start_time,
        )
        st.caption(
            "Seçili başlangıç: "
            + format_datetime(selected_start)
        )

        if start_date.year not in calendar_years:
            st.warning(
                f"{start_date.year} yılına ait geçerli "
                "takvim dosyası bulunmuyor."
            )

        current_signature = schedule_signature(
            campaign,
            start_date,
            start_time,
            scenario_ports,
        )

        if (
            st.session_state.get(
                "schedule_result_signature"
            ) != current_signature
        ):
            clear_schedule()

        if st.button(
            "Bitiş zamanını hesapla",
            type="primary",
        ):
            try:
                # Sonuc, ayni rerun'da okunan widget
                # degerlerinden hesaplanir.
                result = run_schedule_simulation(
                    campaign,
                    int(scenario_ports),
                    selected_start,
                )

                st.session_state.schedule_result = result
                st.session_state.schedule_result_signature = (
                    current_signature
                )

            except FileNotFoundError as exc:
                clear_schedule()
                st.error(
                    "Eksik resmî tatil takvimi. "
                    "Hesaplama tamamlanamadı."
                )
                st.code(str(exc))

            except Exception as exc:
                clear_schedule()
                st.error(
                    f"Simülasyon hatası: {exc}"
                )

        simulation = st.session_state.get(
            "schedule_result"
        )

        # Widget callback'lerine ek son guvenlik kontrolu.
        # Sonucun gercek hesaplama parametreleri de
        # ekrandaki secimlerle ayni olmak zorunda.
        if simulation is not None:
            matches_inputs = (
                st.session_state.get(
                    "schedule_result_signature"
                ) == current_signature
                and simulation.get("campaign_id") == campaign["id"]
                and simulation.get("calculation_ports") == int(scenario_ports)
                and simulation.get("start_datetime") == selected_start.isoformat()
            )
            if not matches_inputs:
                clear_schedule()
                simulation = None

        if simulation:
            st.divider()

            st.write(
                f"**Kampanya:** "
                f"{simulation['campaign_id']} - "
                f"{simulation['campaign_name']}"
            )
            st.write(
                "**Hesaplamada kullanılan başlangıç:** "
                + format_datetime(simulation["start_datetime"])
            )

            s1, s2, s3 = st.columns(3)

            s1.metric(
                "Hesaplama portu",
                simulation["calculation_ports"],
            )

            s2.metric(
                "Çalışma süresi",
                (
                    f"{simulation['capacity']['completion_hours']}"
                    " saat"
                ),
            )

            s3.metric(
                "Arama / saat",
                simulation["capacity"]["calls_per_hour"],
            )

            st.subheader("Resmî Tatil Etkisi")

            a, b = st.columns(2)

            with a:
                st.metric(
                    "Resmî tatiller olmadan",
                    format_datetime(
                        simulation["without_holidays"]
                    ),
                )

            with b:
                st.metric(
                    "Türkiye tatilleri dahil",
                    format_datetime(
                        simulation["with_holidays"]
                    ),
                )

            st.metric(
                "Takvimsel gecikme",
                (
                    f"{simulation['difference_hours']}"
                    " saat"
                ),
            )

            schedule = simulation["schedule"]

            st.write(
                "**Kullanılan tatil takvimleri:** "
                + ", ".join(
                    schedule.get(
                        "holiday_calendars", []
                    )
                )
            )

            skipped = schedule.get(
                "skipped_holidays", []
            )

            partial = schedule.get(
                "partial_holidays", []
            )

            if skipped:
                st.info(
                    "Atlanan resmî tatiller: "
                    + ", ".join(
                        f"{h['date']} ({h['name']})"
                        for h in skipped
                    )
                )

            if partial:
                st.info(
                    "Yarım gün tatiller: "
                    + ", ".join(
                        str(h) for h in partial
                    )
                )

            if not skipped and not partial:
                st.success(
                    "Bu hesaplamayı etkileyen "
                    "resmî tatil bulunmuyor."
                )

            st.caption(
                "Takvimsel gecikme iki tarih arasındaki "
                "geçen süredir; ek çalışma süresi değildir."
            )

            st.warning(
                "Tahminler mevcut kapasite varsayımlarına "
                "dayanır. Retry, operasyonel kesintiler "
                "ve kurum özelinde arama kısıtları "
                "ayrıca modellenmelidir."
            )

        else:
            st.info(
                "Güncel parametrelerle yeni hesaplama yap."
            )


# ==================================================
# TAB 5 - AI ASSISTANT
# ==================================================

with ai_tab:
    st.subheader("🤖 AI Operasyon Asistanı")

    st.caption(
        "LangGraph + MCP + RAG. "
        "Doğrulanmış bitiş zamanları saniyeli gösterilir."
    )

    for item in st.session_state.chat_history:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

            if item["role"] == "assistant":
                for verified in item.get(
                    "verified_results", []
                ):
                    show_verified_result(verified)

                if item.get("tools"):
                    with st.expander("Kullanılan araçlar"):
                        st.write(item["tools"])

                for warning in item.get(
                    "warnings", []
                ):
                    st.warning(warning)

    question = st.chat_input(
        "Kampanyalar hakkında soru sor..."
    )

    if question:
        st.session_state.chat_history.append({
            "role": "user",
            "content": question,
        })

        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant"):
            with st.spinner("Agent analiz yapıyor..."):
                try:
                    result = run_agent(
                        question,
                        st.session_state.thread_id,
                    )

                    st.markdown(result["answer"])

                    for verified in result[
                        "verified_results"
                    ]:
                        show_verified_result(verified)

                    if result["tools"]:
                        with st.expander(
                            "Kullanılan araçlar"
                        ):
                            st.write(result["tools"])

                    for warning in result["warnings"]:
                        st.warning(warning)

                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": result["answer"],
                        "verified_results": result[
                            "verified_results"
                        ],
                        "tools": result["tools"],
                        "warnings": result["warnings"],
                    })

                except Exception as exc:
                    error_message = (
                        f"{type(exc).__name__}: {exc}"
                    )

                    st.error(error_message)

                    st.session_state.chat_history.append({
                        "role": "assistant",
                        "content": (
                            "Agent çalıştırılırken hata oluştu."
                        ),
                        "verified_results": [],
                        "tools": [],
                        "warnings": [error_message],
                    })


# ==================================================
# TAB 6 - DAY 7 RETRY CAPACITY ANALYSIS
# ==================================================

with optimization_tab:
    st.divider()
    st.subheader("🔁 Retry Destekli Kampanya Analizi")
    st.caption(
        "Yalnızca cevaplanmayan kayıtlar tekrar aranır. "
        "Sonuçlar beklenen değerlerdir; veritabanı değişmez."
    )

    retry_id = st.selectbox(
        "Kampanya seç",
        options=campaign_options,
        format_func=lambda cid: f"{cid} - {campaign_by_id[cid]['name']}",
        key="retry_campaign",
        on_change=retry_campaign_changed,
    )
    retry_campaign = campaign_by_id[retry_id]
    remaining_retry_records = max(
        0, retry_campaign["total_records"] - retry_campaign["completed_records"]
    )

    rc1, rc2, rc3 = st.columns(3)
    with rc1:
        retry_ports = st.number_input(
            "Simülasyon port sayısı", min_value=1, max_value=500,
            value=int(retry_campaign["ports"]), step=1,
            key="retry_ports", on_change=clear_retry,
        )
    with rc2:
        retry_max_attempts = st.number_input(
            "Maksimum arama denemesi", min_value=1, max_value=20,
            value=3, step=1, key="retry_max_attempts",
            on_change=clear_retry,
        )
    with rc3:
        retry_percent = st.slider(
            "Tekrar aranacak cevapsızlar (%)",
            min_value=0, max_value=100, value=100, step=5,
            key="retry_percent", on_change=clear_retry,
        )

    retry_wait_hours = st.number_input(
        "Retry bekleme süresi (saat)",
        min_value=0.0, max_value=720.0, value=24.0, step=1.0,
        key="retry_wait_hours", on_change=clear_retry,
        help=("Bir sonraki tur, önceki turun tamamlanmasından "
              "bu kadar gerçek saat sonra başlayabilir. "
              "Çalışma takvimi ayrıca uygulanır."),
    )
    st.caption(
        "Politika: Sıralı turlar; bir tur bitmeden sonraki tur başlamaz. "
        "Kayıt bazlı Genesys retry zamanlaması değildir."
    )

    if calendar_years:
        retry_default_date = (
            date(2026, 10, 26) if 2026 in calendar_years
            else date(calendar_years[0], 1, 1)
        )
        rd1, rd2 = st.columns(2)
        with rd1:
            retry_date = st.date_input(
                "Başlangıç tarihi", value=retry_default_date,
                min_value=date(calendar_years[0], 1, 1),
                max_value=date(calendar_years[-1], 12, 31),
                key="retry_date", on_change=clear_retry,
            )
        with rd2:
            retry_time = st.time_input(
                "Başlangıç saati", value=time(16, 0), step=1,
                key="retry_time", on_change=clear_retry,
            )
        retry_start = combine_istanbul(retry_date, retry_time)
        st.caption("Seçili başlangıç: " + format_datetime(retry_start))
    else:
        retry_start = None
        st.warning("Bitiş tarihi için tatil takvimi gerekli.")

    st.caption(
        f"Kalan benzersiz kayıt: {remaining_retry_records:,} | "
        f"Gerçek port: {retry_campaign['ports']} | "
        f"Cevaplanma oranı: %{retry_campaign['answer_rate']}"
    )

    retry_sig = (
        campaign_signature(retry_campaign), int(retry_ports),
        int(retry_max_attempts), int(retry_percent),
        float(retry_wait_hours),
        retry_start.isoformat() if retry_start else None,
        calendar_files_signature(),
    )
    if st.session_state.get("retry_result_signature") != retry_sig:
        clear_retry()

    if st.button("Retry simülasyonunu hesapla", type="primary"):
        try:
            analysis = analyze_retry_capacity(
                records=remaining_retry_records,
                ports=int(retry_ports),
                answer_rate=retry_campaign["answer_rate"],
                avg_ivr_seconds=retry_campaign["avg_ivr_seconds"],
                avg_no_answer_seconds=retry_campaign["avg_no_answer_seconds"],
                overhead_seconds=retry_campaign["overhead_seconds"],
                max_attempts=int(retry_max_attempts),
                retry_fraction=retry_percent / 100.0,
            )
            schedules = None
            policy = None
            if retry_start is not None:
                policy = simulate_retry_policy(
                    records=remaining_retry_records,
                    ports=int(retry_ports),
                    answer_rate=retry_campaign["answer_rate"],
                    avg_ivr_seconds=retry_campaign["avg_ivr_seconds"],
                    avg_no_answer_seconds=retry_campaign["avg_no_answer_seconds"],
                    overhead_seconds=retry_campaign["overhead_seconds"],
                    max_attempts=int(retry_max_attempts),
                    retry_fraction=retry_percent / 100.0,
                    retry_wait_hours=float(retry_wait_hours),
                    start_datetime=retry_start,
                )
                # Referans: Bekleme olmadan tüm retry yükünün
                # tek bir kesintisiz iş kuyruğu gibi işlenmesi.
                base_schedule = estimate_finish_datetime(
                    analysis["baseline_completion_hours"], retry_start
                )
                no_wait_schedule = estimate_finish_datetime(
                    analysis["retry_completion_hours"], retry_start
                )
                schedules = {
                    "baseline": base_schedule,
                    "retry": no_wait_schedule,
                }
            st.session_state.retry_result = {
                "analysis": analysis,
                "policy": policy,
                "schedules": schedules,
                "campaign_id": retry_id,
                "start": retry_start.isoformat() if retry_start else None,
            }
            st.session_state.retry_result_signature = retry_sig
        except (ValueError, FileNotFoundError, TypeError) as exc:
            clear_retry()
            st.error(f"Retry simülasyonu hesaplanamadı: {exc}")
        except Exception as exc:
            clear_retry()
            st.error(f"Beklenmeyen simülasyon hatası: {exc}")

    retry_result = st.session_state.get("retry_result")
    if retry_result and st.session_state.get("retry_result_signature") == retry_sig:
        analysis = retry_result["analysis"]
        st.divider()
        st.write(
            "**Hesaplamada kullanılan başlangıç:** "
            + (format_datetime(retry_result["start"]) if retry_result["start"] else "Takvim yok")
        )
        ra, rb, rc, rd = st.columns(4)
        ra.metric("İlk arama yükü", f"{analysis['baseline_attempts']:,.0f}")
        rb.metric("Retry dahil deneme", f"{analysis['expected_total_attempts']:,.0f}")
        rc.metric("Ek deneme", f"{analysis['expected_extra_attempts']:,.0f}")
        rd.metric(
            "Beklenen ulaşılan müşteri",
            f"{analysis['expected_answered_customers']:,.0f}",
        )

        rt1, rt2, rt3 = st.columns(3)
        rt1.metric("Retry olmadan", f"{analysis['baseline_completion_hours']:.2f} saat")
        rt2.metric("Retry dahil", f"{analysis['retry_completion_hours']:.2f} saat")
        rt3.metric("Ek aktif çalışma", f"{analysis['extra_completion_hours']:.2f} saat")

        breakdown = pd.DataFrame(analysis["attempt_breakdown"])
        breakdown["Deneme"] = breakdown["attempt_number"].map(
            lambda n: f"{n}. deneme"
        )
        fig_retry = px.bar(
            breakdown, x="Deneme", y="expected_attempts",
            text="expected_attempts",
            title="Deneme Bazında Beklenen Arama Yükü",
            labels={"expected_attempts": "Beklenen arama adedi"},
        )
        fig_retry.update_traces(texttemplate="%{text:,.0f}", textposition="outside")
        st.plotly_chart(fig_retry, use_container_width=True)
        st.dataframe(
            breakdown.rename(columns={
                "expected_attempts": "Beklenen deneme",
                "expected_answers": "Beklenen cevap",
                "expected_unanswered": "Beklenen cevapsız",
            })[["Deneme", "Beklenen deneme", "Beklenen cevap", "Beklenen cevapsız"]],
            use_container_width=True, hide_index=True,
        )

        schedules = retry_result["schedules"]
        if schedules:
            st.subheader("📅 Retry Etkisiyle Bitiş Zamanı")
            end_base = datetime.fromisoformat(schedules["baseline"]["estimated_finish"])
            end_retry = datetime.fromisoformat(schedules["retry"]["estimated_finish"])
            sd1, sd2 = st.columns(2)
            sd1.metric("Retry olmadan bitiş", format_datetime(end_base))
            sd2.metric("Beklemesiz toplu retry bitişi", format_datetime(end_retry))
            st.metric(
                "Takvimsel bitiş farkı",
                f"{(end_retry - end_base).total_seconds() / 3600:.2f} saat",
            )
            st.caption(
                "Kullanılan takvimler: " + ", ".join(
                    schedules["retry"].get("holiday_calendars", [])
                )
            )
            holidays = schedules["retry"].get("skipped_holidays", [])
            if holidays:
                st.info(
                    "Atlanan resmî tatiller: " + ", ".join(
                        f"{h['date']} ({h['name']})" for h in holidays
                    )
                )
        policy = retry_result.get("policy")
        if policy:
            st.subheader("⏱️ Retry Bekleme Politikası")
            st.caption(
                "Bekleme, önceki turun bitiminden itibaren gerçek saat olarak "
                "hesaplanır. Sonraki tur yalnızca uygun çalışma zamanında başlar."
            )
            p1, p2, p3 = st.columns(3)
            p1.metric("Retry bekleme", f"{policy['retry_wait_hours']:g} saat")
            p2.metric("Sıralı turlarla bitiş", format_datetime(policy["retry_finish"]))
            p3.metric(
                "İlk aramaya göre takvimsel fark",
                f"{policy['calendar_delay_hours']:.2f} saat",
            )
            st.write(
                "**İlk tur başlangıcı (takvime uyarlanmış):** "
                + format_datetime(policy["start"])
            )
            if schedules:
                st.write(
                    "**Beklemesiz toplu retry bitişi:** "
                    + format_datetime(schedules["retry"]["estimated_finish"])
                )
                st.caption(
                    "Beklemesiz toplu hesap ile sıralı turlu hesap farklı "
                    "varsayımlara dayanır."
                )
            rounds = policy.get("rounds", [])
            if rounds:
                rounds_df = pd.DataFrame([
                    {
                        "Tur": r["attempt_number"],
                        "Beklenen arama": r["expected_attempts"],
                        "Beklenen cevap": r["expected_answers"],
                        "Aktif süre (saat)": r["active_hours"],
                        "Başlangıç": format_datetime(r["start"]),
                        "Bitiş": format_datetime(r["finish"]),
                    }
                    for r in rounds
                ])
                st.dataframe(rounds_df, use_container_width=True, hide_index=True)
                timeline_df = pd.DataFrame([
                    {
                        "Tur": f"{r['attempt_number']}. tur",
                        "Başlangıç": datetime.fromisoformat(r["start"]),
                        "Bitiş": datetime.fromisoformat(r["finish"]),
                    }
                    for r in rounds
                ])
                fig_timeline = px.timeline(
                    timeline_df,
                    x_start="Başlangıç", x_end="Bitiş", y="Tur",
                    title="Retry Turları — Takvimsel Zaman Çizelgesi",
                )
                fig_timeline.update_yaxes(autorange="reversed")
                st.plotly_chart(fig_timeline, use_container_width=True)
            st.info(policy["assumptions"])
        st.warning(
            "Sabit cevaplanma olasılığı ve sıralı tur varsayımı kullanılır. "
            "Genesys Engage pacing, retry code, DNC ve kayıt bazlı "
            "yeniden arama uygunluğu bu simülasyona dahil değildir."
        )
    else:
        st.info("Güncel parametrelerle retry simülasyonunu başlat.")


# ==================================================
# TAB 7 - DAY 7 DISPOSITION-AWARE RETRY
# ==================================================

with optimization_tab:
    st.divider()
    st.subheader("📞 Sonuç Kodlarına Göre Retry Analizi")
    st.caption(
        "Answered / No Answer / Busy / Failed sentetik sonuç sınıflarıdır. "
        "Genesys Engage sistem kodlarıyla birebir eşleşmez. "
        "Bu modelde retry bekleme süresi ve tatil takvimi hesaplanmaz."
    )

    disposition_id = st.selectbox(
        "Kampanya",
        options=campaign_options,
        format_func=lambda cid: f"{cid} - {campaign_by_id[cid]['name']}",
        key="disposition_campaign",
    )
    disposition_campaign = campaign_by_id[disposition_id]
    disposition_records = max(
        0,
        disposition_campaign["total_records"]
        - disposition_campaign["completed_records"],
    )

    dc1, dc2 = st.columns(2)
    with dc1:
        disposition_ports = st.number_input(
            "Port sayısı", min_value=1, max_value=500,
            value=int(disposition_campaign["ports"]), step=1,
            key=f"disposition_ports_{disposition_id}",
        )
    with dc2:
        disposition_attempts = st.number_input(
            "Maksimum arama denemesi", min_value=1, max_value=20,
            value=3, step=1, key="disposition_attempts",
        )

    st.markdown("**Arama sonuçlarının dağılımı (%)**")
    oc1, oc2, oc3, oc4 = st.columns(4)
    with oc1:
        answered_pct = st.number_input(
            "Answered", min_value=0.0, max_value=100.0,
            value=float(disposition_campaign["answer_rate"]),
            step=1.0, key=f"disp_answered_{disposition_id}",
        )
    with oc2:
        no_answer_pct = st.number_input(
            "No Answer", min_value=0.0, max_value=100.0,
            value=40.0, step=1.0, key="disp_no_answer",
        )
    with oc3:
        busy_pct = st.number_input(
            "Busy", min_value=0.0, max_value=100.0,
            value=15.0, step=1.0, key="disp_busy",
        )
    with oc4:
        failed_pct = st.number_input(
            "Failed", min_value=0.0, max_value=100.0,
            value=5.0, step=1.0, key="disp_failed",
        )

    total_pct = answered_pct + no_answer_pct + busy_pct + failed_pct
    valid_distribution = abs(total_pct - 100.0) < 1e-7
    if valid_distribution:
        st.caption("Dağılım toplamı: %100")
    else:
        st.error(
            f"Dağılım toplamı %{total_pct:g}. "
            "Hesaplama için toplam tam olarak %100 olmalı."
        )

    st.markdown("**Sonuç koduna göre tekrar arama oranı (%)**")
    dr1, dr2, dr3 = st.columns(3)
    with dr1:
        retry_no_answer = st.slider(
            "No Answer retry", 0, 100, 100, 5,
            key="disp_retry_no_answer",
        )
    with dr2:
        retry_busy = st.slider(
            "Busy retry", 0, 100, 50, 5,
            key="disp_retry_busy",
        )
    with dr3:
        retry_failed = st.slider(
            "Failed retry", 0, 100, 0, 5,
            key="disp_retry_failed",
        )

    st.markdown("**Çağrı süreleri (saniye)**")
    ds1, ds2, ds3, ds4 = st.columns(4)
    with ds1:
        seconds_answered = st.number_input(
            "Answered süresi", min_value=0.0, value=float(
                disposition_campaign["avg_ivr_seconds"]
            ), step=1.0, key=f"disp_seconds_answered_{disposition_id}",
        )
    with ds2:
        seconds_no_answer = st.number_input(
            "No Answer süresi", min_value=0.0, value=float(
                disposition_campaign["avg_no_answer_seconds"]
            ), step=1.0, key=f"disp_seconds_no_answer_{disposition_id}",
        )
    with ds3:
        seconds_busy = st.number_input(
            "Busy süresi", min_value=0.0, value=10.0,
            step=1.0, key="disp_seconds_busy",
        )
    with ds4:
        seconds_failed = st.number_input(
            "Failed süresi", min_value=0.0, value=5.0,
            step=1.0, key="disp_seconds_failed",
        )

    disposition_signature = (
        campaign_signature(disposition_campaign), int(disposition_ports),
        int(disposition_attempts), answered_pct, no_answer_pct,
        busy_pct, failed_pct, retry_no_answer, retry_busy, retry_failed,
        seconds_answered, seconds_no_answer, seconds_busy, seconds_failed,
    )
    if st.session_state.get("disposition_signature") != disposition_signature:
        st.session_state.pop("disposition_result", None)
        st.session_state.pop("disposition_signature", None)

    if st.button(
        "Sonuç kodlarına göre hesapla", type="primary",
        disabled=not valid_distribution, key="run_disposition",
    ):
        try:
            disposition_result = analyze_retry_dispositions(
                records=disposition_records,
                ports=int(disposition_ports),
                max_attempts=int(disposition_attempts),
                outcome_rates={
                    "answered": answered_pct / 100.0,
                    "no_answer": no_answer_pct / 100.0,
                    "busy": busy_pct / 100.0,
                    "failed": failed_pct / 100.0,
                },
                retry_rates={
                    "no_answer": retry_no_answer / 100.0,
                    "busy": retry_busy / 100.0,
                    "failed": retry_failed / 100.0,
                },
                outcome_seconds={
                    "answered": seconds_answered,
                    "no_answer": seconds_no_answer,
                    "busy": seconds_busy,
                    "failed": seconds_failed,
                },
                overhead_seconds=float(disposition_campaign["overhead_seconds"]),
            )
            st.session_state.disposition_result = disposition_result
            st.session_state.disposition_signature = disposition_signature
        except Exception as exc:
            st.session_state.pop("disposition_result", None)
            st.session_state.pop("disposition_signature", None)
            st.error(f"Sonuç kodu simülasyonu başarısız: {exc}")

    disposition_result = st.session_state.get("disposition_result")
    if (
        disposition_result is not None
        and st.session_state.get("disposition_signature") == disposition_signature
    ):
        st.divider()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("İlk arama", f"{disposition_result['records']:,.0f}")
        m2.metric(
            "Toplam beklenen deneme",
            f"{disposition_result['expected_total_attempts']:,.2f}",
        )
        m3.metric(
            "Ek retry denemesi",
            f"{disposition_result['expected_extra_attempts']:,.2f}",
        )
        m4.metric(
            "Beklenen cevaplanan müşteri",
            f"{disposition_result['expected_unique_answered']:,.2f}",
        )

        t1, t2 = st.columns(2)
        t1.metric(
            "Retry olmadan aktif süre",
            f"{disposition_result['baseline_active_hours']:.2f} saat",
        )
        t2.metric(
            "Retry dahil aktif süre",
            f"{disposition_result['retry_active_hours']:.2f} saat",
        )

        round_rows = []
        for round_data in disposition_result["rounds"]:
            row = {
                "Tur": round_data["round"],
                "Beklenen arama": round_data["attempts"],
                "Sonraki tura aday": round_data["retry_eligible_next_round"],
                "Aktif süre (saat)": round_data["active_hours"],
            }
            for outcome, count in round_data["outcomes"].items():
                row[outcome] = count
            round_rows.append(row)

        if round_rows:
            disposition_df = pd.DataFrame(round_rows)
            st.dataframe(
                disposition_df, use_container_width=True, hide_index=True,
            )
            chart = px.bar(
                disposition_df, x="Tur", y="Beklenen arama",
                text="Beklenen arama",
                title="Sonuç Kodlarına Göre Retry — Tur Bazında Arama Yükü",
            )
            chart.update_traces(
                texttemplate="%{text:,.2f}", textposition="outside",
            )
            st.plotly_chart(chart, use_container_width=True)

        outcome_df = pd.DataFrame([
            {"Sonuç": name, "Beklenen adet": count}
            for name, count in disposition_result["expected_outcome_counts"].items()
        ])
        st.plotly_chart(
            px.bar(
                outcome_df, x="Sonuç", y="Beklenen adet",
                title="Tüm Turlarda Beklenen Sonuç Dağılımı",
            ),
            use_container_width=True,
        )
        st.info(disposition_result["assumptions"])
        st.warning(
            "Bu sekmedeki aktif süre, takvimsel bitiş tarihi değildir. "
            "Bekleme ve tatil etkileri ayrı Retry Analizi sekmesindedir. "
            "İki modelin sonuçları farklı varsayımlar kullanır."
        )
    else:
        st.info("Güncel oranlarla simülasyon için hesapla butonuna bas.")


# ==================================================
# TAB 8 - DAY 8 OUTBOUND CSV OBSERVATION
# ==================================================

with analysis_tab:
    st.divider()
    st.subheader("📥 Outbound CSV — Gerçekleşen Arama Analizi")
    st.caption(
        "Genesys Engage için proje tarafından tanımlanan CSV şeması kullanılır; "
        "doğrudan Genesys standart raporu değildir. Dosyalar veritabanına kaydedilmez."
    )
    st.info(
        "CSV kolonları: campaign_id, attempt_id, timestamp, disposition, "
        "duration_seconds, port_id. Timestamp saat dilimi içermelidir. "
        "Yalnızca anonimleştirilmiş veriler yükleyin; müşteri numarası veya kimlik bilgisi eklemeyin."
    )
    uploaded_attempts = st.file_uploader(
        "Outbound arama CSV dosyası (en fazla 10 MB)",
        type=["csv"],
        key="outbound_csv_upload",
    )
    if uploaded_attempts is None:
        st.session_state.pop("outbound_csv_result", None)
        st.session_state.pop("outbound_csv_signature", None)
        st.info("Analize başlamak için CSV dosyası yükleyin.")
    else:
        file_bytes = uploaded_attempts.getvalue()
        file_signature = hashlib.sha256(file_bytes).hexdigest()
        if st.session_state.get("outbound_csv_signature") != file_signature:
            st.session_state.pop("outbound_csv_result", None)
            st.session_state.pop("outbound_csv_signature", None)

        if len(file_bytes) > MAX_FILE_BYTES:
            st.error("Dosya 10 MB sınırını aşıyor. Daha küçük bir CSV yükleyin.")
        elif st.button("CSV verisini analiz et", type="primary", key="run_outbound_csv"):
            temporary_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", suffix=".csv", delete=False
                ) as temporary:
                    temporary.write(file_bytes)
                    temporary_path = Path(temporary.name)
                attempt_rows = load_outbound_csv(temporary_path)
                summaries = summarize_outbound(attempt_rows)
                st.session_state.outbound_csv_result = {
                    "rows": attempt_rows,
                    "summaries": summaries,
                }
                st.session_state.outbound_csv_signature = file_signature
            except (OutboundImportError, ValueError, OSError) as exc:
                st.session_state.pop("outbound_csv_result", None)
                st.session_state.pop("outbound_csv_signature", None)
                st.error(f"CSV doğrulama hatası: {exc}")
            finally:
                if temporary_path is not None:
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError:
                        pass

        csv_result = st.session_state.get("outbound_csv_result")
        if (
            csv_result is not None
            and st.session_state.get("outbound_csv_signature") == file_signature
        ):
            summaries = csv_result["summaries"]
            rows = csv_result["rows"]
            total_attempts = len(rows)
            answered_count = sum(r["disposition"] == "ANSWERED" for r in rows)
            observed_ports = len({r["port_id"] for r in rows})
            mean_duration = sum(r["duration_seconds"] for r in rows) / total_attempts

            st.divider()
            a, b, c, d = st.columns(4)
            a.metric("Arama denemesi", f"{total_attempts:,}")
            b.metric("Answer Rate", f"%{answered_count / total_attempts * 100:.2f}")
            c.metric("Ortalama süre", f"{mean_duration:.2f} sn")
            d.metric("Gözlenen port", observed_ports)
            st.caption(
                "Gözlenen port sayısı, tahsis edilmiş kapasite değildir. "
                "Answer Rate, cevaplanan denemelerin tüm denemelere oranıdır; "
                "benzersiz müşteri cevaplanma oranı değildir."
            )

            st.markdown("**Kampanya bazında gerçekleşen metrikler**")
            summary_df = pd.DataFrame(summaries).rename(columns={
                "campaign_id": "Kampanya ID",
                "attempts": "Deneme",
                "answered_attempts": "Answered",
                "answer_rate_pct": "Answer Rate (%)",
                "no_answer_attempts": "No Answer",
                "busy_attempts": "Busy",
                "failed_attempts": "Failed",
                "average_duration_seconds": "Ort. süre (sn)",
                "observed_ports": "Gözlenen port",
                "first_attempt": "İlk arama",
                "last_attempt": "Son arama",
            })
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

            campaign_ids = [s["campaign_id"] for s in summaries]
            chosen_id = st.selectbox(
                "Grafiklerde gösterilecek kampanya",
                options=campaign_ids,
                format_func=lambda cid: (
                    f"{cid} - {campaign_by_id[cid]['name']}"
                    if cid in campaign_by_id else f"{cid} - CSV kampanyası"
                ),
                key="outbound_csv_campaign",
            )
            selected_rows = [r for r in rows if r["campaign_id"] == chosen_id]
            selected_summary = next(s for s in summaries if s["campaign_id"] == chosen_id)

            from collections import Counter
            distribution = Counter(r["disposition"] for r in selected_rows)
            dist_df = pd.DataFrame([
                {"Sonuç": label, "Deneme": distribution[label]}
                for label in ("ANSWERED", "NO_ANSWER", "BUSY", "FAILED")
            ])
            chart_left, chart_right = st.columns(2)
            with chart_left:
                st.plotly_chart(
                    px.bar(
                        dist_df, x="Sonuç", y="Deneme", text="Deneme",
                        title="Gerçekleşen sonuç kodları",
                    ),
                    use_container_width=True,
                )
            with chart_right:
                duration_df = pd.DataFrame([
                    {
                        "Sonuç": label,
                        "Ortalama süre (sn)": (
                            sum(r["duration_seconds"] for r in selected_rows
                                if r["disposition"] == label) / distribution[label]
                            if distribution[label] else 0.0
                        ),
                    }
                    for label in ("ANSWERED", "NO_ANSWER", "BUSY", "FAILED")
                ])
                st.plotly_chart(
                    px.bar(
                        duration_df, x="Sonuç", y="Ortalama süre (sn)",
                        title="Sonuç koduna göre ortalama çağrı süresi",
                    ),
                    use_container_width=True,
                )

            # Normalize all aware timestamps to Istanbul before hourly bucketing.
            hourly_counts = Counter(
                r["timestamp"].astimezone(ISTANBUL).replace(
                    minute=0, second=0, microsecond=0
                )
                for r in selected_rows
            )
            hourly_df = pd.DataFrame([
                {"Saat (İstanbul)": hour, "Arama": count}
                for hour, count in sorted(hourly_counts.items())
            ])
            st.plotly_chart(
                px.bar(
                    hourly_df, x="Saat (İstanbul)", y="Arama",
                    title="Saatlik gerçekleşen arama denemeleri",
                ),
                use_container_width=True,
            )

            if chosen_id in campaign_by_id:
                campaign = campaign_by_id[chosen_id]
                st.markdown("**Sentetik kampanya varsayımıyla karşılaştırma**")
                comparison_df = pd.DataFrame([
                    {"Metrik": "Answer Rate (%)",
                     "Sentetik kampanya": campaign["answer_rate"],
                     "CSV gerçekleşen": selected_summary["answer_rate_pct"]},
                    {"Metrik": "Ortalama deneme süresi (sn)",
                     "Sentetik kampanya": round(
                         campaign["answer_rate"] / 100 * campaign["avg_ivr_seconds"]
                         + (1 - campaign["answer_rate"] / 100)
                         * campaign["avg_no_answer_seconds"]
                         + campaign["overhead_seconds"], 2
                     ),
                     "CSV gerçekleşen": selected_summary["average_duration_seconds"]},
                ])
                st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                st.warning(
                    "Karşılaştırma yalnızca gösterim amaçlıdır: CSV örneklemi ve "
                    "kampanya simülasyonunun zaman aralığı/çağrı kapsamı aynı "
                    "olmayabilir. CSV süresinin overhead içerip içermediği "
                    "kaynağa bağlıdır. Sonuçlar otomatik kalibrasyon değildir."
                )
            else:
                st.info("Bu CSV kampanyası yerel sentetik veritabanında bulunmuyor.")

            st.divider()
            st.subheader("📈 CSV verisiyle kapasite tahmini")
            st.caption(
                "Seçilen kampanyanın gerçekleşen çağrı sürelerini kullanarak gelecekteki "
                "arama denemeleri için tek-deneme kapasite tahmini oluşturur. "
                "CSV kayıtları mevcut kampanya veritabanını değiştirmez."
            )
            known_campaign = campaign_by_id.get(chosen_id)
            suggested_remaining = (
                max(0, int(known_campaign["total_records"] - known_campaign["completed_records"]))
                if known_campaign else 1000
            )
            suggested_ports = int(known_campaign["ports"]) if known_campaign else 5
            ca, cb, cc = st.columns(3)
            with ca:
                future_attempts = st.number_input(
                    "Planlanan kalan arama denemesi",
                    min_value=0, max_value=10_000_000,
                    value=min(suggested_remaining, 10_000_000), step=100,
                    key=f"observed_remaining_{file_signature}_{chosen_id}",
                )
            with cb:
                allocated_ports = st.number_input(
                    "Tahsis edilen eşzamanlı port",
                    min_value=1, max_value=10000,
                    value=min(max(1, suggested_ports), 10000), step=1,
                    key=f"observed_ports_{file_signature}_{chosen_id}",
                )
            with cc:
                extra_overhead = st.number_input(
                    "CSV süresine eklenecek overhead (sn)",
                    min_value=0.0, max_value=300.0, value=0.0, step=0.5,
                    key=f"observed_overhead_{file_signature}_{chosen_id}",
                    help="CSV duration_seconds tüm port işgalini kapsıyorsa 0 bırakın.",
                )
            st.warning(
                "Bu tahmin, CSV'deki sürelerin port işgal süresi olduğunu, "
                "örneklemin geleceği temsil ettiğini ve seçilen portların sürekli "
                "kullanılabildiğini varsayar. CSV Answer Rate benzersiz müşteri "
                "oranı değildir. Kalan değer, müşteri değil planlanan deneme sayısıdır."
            )
            try:
                observed_forecast = forecast_observed_capacity(
                    selected_rows,
                    remaining_records=int(future_attempts),
                    ports=int(allocated_ports),
                    extra_overhead_seconds=float(extra_overhead),
                )
                oa, ob, oc, od = st.columns(4)
                oa.metric("Gözlenen Answer Rate", f"%{observed_forecast['observed_answer_rate_pct']:.2f}")
                ob.metric("Ort. port işgali", f"{observed_forecast['avg_port_seconds']:.2f} sn")
                oc.metric("Teorik arama/saat", f"{observed_forecast['calls_per_hour']:,.1f}")
                od.metric("Aktif çalışma", f"{observed_forecast['completion_hours']:.2f} saat")
                st.caption(
                    f"4 saat hedefi için teorik minimum port: "
                    f"{observed_forecast['required_ports_for_4h']}. "
                    "Kuyruk, dialer pacing, hatalı deneme, retry ve kampanya kesintileri dahil değildir."
                )
                if known_campaign:
                    synthetic = get_capacity(known_campaign, ports=int(allocated_ports))
                    st.markdown("**Sentetik tahmin ile CSV tabanlı tahmin**")
                    compare = pd.DataFrame([
                        {"Model": "Sentetik varsayımlar",
                         "Ortalama port süresi (sn)": synthetic["avg_port_seconds"],
                         "Saatlik deneme": synthetic["calls_per_hour"],
                         "Aktif süre (saat)": round(
                             int(future_attempts) * synthetic["avg_port_seconds"]
                             / (int(allocated_ports) * 3600), 4),
                        },
                        {"Model": "CSV gerçekleşen ortalaması",
                         "Ortalama port süresi (sn)": observed_forecast["avg_port_seconds"],
                         "Saatlik deneme": observed_forecast["calls_per_hour"],
                         "Aktif süre (saat)": observed_forecast["completion_hours"],
                        },
                    ])
                    st.dataframe(compare, hide_index=True, use_container_width=True)
                    st.plotly_chart(
                        px.bar(compare, x="Model", y="Aktif süre (saat)",
                               text="Aktif süre (saat)",
                               title="Aynı deneme adedi ve portla kapasite karşılaştırması"),
                        use_container_width=True,
                    )
                st.markdown("**Takvime göre tahmini bitiş**")
                fa, fb = st.columns(2)
                with fa:
                    forecast_date = st.date_input(
                        "Başlangıç tarihi", value=date.today(),
                        key=f"observed_start_date_{file_signature}_{chosen_id}",
                    )
                with fb:
                    forecast_time = st.time_input(
                        "Başlangıç saati", value=time(9, 0),
                        key=f"observed_start_time_{file_signature}_{chosen_id}",
                    )
                calendar_start = combine_istanbul(forecast_date, forecast_time)
                calendar_forecast = estimate_finish_datetime(
                    observed_forecast["completion_hours"], calendar_start
                )
                st.success(
                    "Tahmini bitiş: "
                    + format_datetime(calendar_forecast["estimated_finish"])
                )
                st.caption(
                    "Takvim hesabı mevcut çalışma saatleri ve tatil kurallarını kullanır. "
                    "Bu tarih garanti değil, model tahminidir."
                )
            except (ValueError, TypeError, OSError) as exc:
                st.error(f"CSV kapasite tahmini oluşturulamadı: {exc}")

            # DAY 8 / STAGE 5: HOURLY PERFORMANCE VARIANCE
            st.divider()
            st.subheader("📊 Saatlik Gerçekleşen – Teorik Kapasite Sapması")
            st.caption(
                "CSV'deki arama başlangıçlarını saat bazında sayar ve ideal, sürekli dolu "
                "port kapasitesiyle karşılaştırır. Bu oran gerçek port kullanım ölçümü değildir."
            )
            variance_left, variance_right = st.columns(2)
            with variance_left:
                variance_ports = st.number_input(
                    "Sapma analizi için tahsis edilmiş port sayısı",
                    min_value=1, max_value=10000,
                    value=min(max(1, suggested_ports), 10000), step=1,
                    key=f"variance_ports_{file_signature}_{chosen_id}",
                    help="CSV'deki gözlenen port sayısı değil, bu kampanyaya ayrılmış eşzamanlı port sayısı.",
                )
            with variance_right:
                variance_duration_mode = st.radio(
                    "Port süresi kaynağı",
                    ["CSV ortalaması", "Manuel süre"],
                    horizontal=True,
                    key=f"variance_mode_{file_signature}_{chosen_id}",
                )
            manual_variance_seconds = None
            if variance_duration_mode == "Manuel süre":
                manual_variance_seconds = st.number_input(
                    "Ortalama port işgali (saniye)",
                    min_value=0.01, max_value=36000.0, value=20.0, step=0.5,
                    key=f"variance_seconds_{file_signature}_{chosen_id}",
                )
            try:
                variance_result = analyze_hourly_variance(
                    selected_rows,
                    ports=int(variance_ports),
                    avg_port_seconds=(
                        float(manual_variance_seconds)
                        if manual_variance_seconds is not None else None
                    ),
                )
                ideal_rate = variance_result["ideal_attempts_per_hour"]
                buckets = variance_result["hourly"]
                v1, v2, v3 = st.columns(3)
                v1.metric("Teorik kapasite / saat", f"{ideal_rate:,.1f} deneme")
                v2.metric("CSV'de görülen deneme", f"{variance_result['observed_attempts']:,}")
                v3.metric("Veri bulunan saat dilimi", str(variance_result["observed_hour_buckets"]))
                variance_df = pd.DataFrame([
                    {
                        "Saat (İstanbul)": pd.to_datetime(item["hour"]),
                        "Gerçekleşen deneme": item["attempts"],
                        "İdeal kapasite": item["ideal_attempts_per_hour"],
                        "Sapma (deneme)": item["difference_attempts"],
                        "Teoriye oran (%)": item["ratio_to_ideal_pct"],
                        "Teoriden eksik (%)": item["below_ideal_pct"],
                    }
                    for item in buckets
                ])
                variance_plot_df = variance_df.melt(
                    id_vars=["Saat (İstanbul)"],
                    value_vars=["Gerçekleşen deneme", "İdeal kapasite"],
                    var_name="Seri", value_name="Deneme",
                )
                st.plotly_chart(
                    px.bar(
                        variance_plot_df,
                        x="Saat (İstanbul)", y="Deneme", color="Seri",
                        barmode="group",
                        title="Saatlik gerçekleşen deneme ve teorik kapasite",
                    ),
                    use_container_width=True,
                )
                st.dataframe(variance_df, hide_index=True, use_container_width=True)
                st.caption(variance_result["caveat"])

                # Day 9: Only aggregated statistics are sent to the AI agent.
                # No raw CSV records, phone numbers, or port identifiers.
                st.markdown("### 🤖 AI Operasyon Değerlendirmesi")
                st.caption(
                    "AI yalnızca seçilen kampanyanın anonim özetini alır. "
                    "Yorumlar olası nedenleri belirtir, kesin arıza teşhisi koymaz."
                )
                ai_brief = build_operations_brief(selected_summary, variance_result)
                ai_signature = hashlib.sha256(
                    json.dumps(ai_brief, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                if st.session_state.get("operations_ai_signature") != ai_signature:
                    st.session_state.pop("operations_ai_result", None)
                if st.button(
                    "Bu kampanyayı AI ile değerlendir",
                    key="run_operations_ai",
                    type="primary",
                ):
                    with st.spinner("AI operasyon özeti hazırlanıyor..."):
                        try:
                            agent_response = run_agent(
                                build_agent_question(ai_brief),
                                st.session_state.thread_id,
                            )
                            st.session_state.operations_ai_result = agent_response
                            st.session_state.operations_ai_signature = ai_signature
                        except Exception as exc:
                            st.session_state.pop("operations_ai_result", None)
                            st.error(f"AI değerlendirmesi tamamlanamadı: {exc}")
                if (
                    st.session_state.get("operations_ai_signature") == ai_signature
                    and st.session_state.get("operations_ai_result") is not None
                ):
                    ai_response = st.session_state.operations_ai_result
                    st.markdown(ai_response["answer"])
                    for warning in ai_response.get("warnings", []):
                        st.warning(warning)
                    with st.expander("AI'ya gönderilen anonim özet"):
                        st.json(ai_brief)
                st.warning(
                    "CSV'de arama kaydı bulunmayan saatler gösterilmez; sıfır "
                    "arama yapıldığı varsayılmaz. İlk/son saat kısmi olabilir. "
                    "Tahsis edilen portlar saat içinde değiştiyse veya CSV eksikse "
                    "oranlar yanıltıcı olabilir. Bu analiz tek başına pacing, "
                    "port boşluğu veya arıza nedenini kanıtlamaz."
                )
            except (ValueError, TypeError, KeyError) as exc:
                st.error(f"Saatlik sapma analizi oluşturulamadı: {exc}")

            st.caption(
                "CSV analizleri oturum belleğinde tutulur; "
                "veritabanına veya kalıcı bir dosyaya aktarılmaz. "
                "Tekil müşteri ve gerçek retry zinciri analizi bu aşamanın kapsamında değildir."
            )
