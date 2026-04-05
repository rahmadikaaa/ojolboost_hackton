"""
agents/the_planner/tools.py
==============================
Layer 4 — MCP Client Tool Wrappers untuk The Planner.

Referensi skill:
  - skills/the_planner/schedule_creation.md
  - skills/the_planner/task_reminder_format.md
  - skills/the_planner/conflict_resolution.md

Semua komunikasi melalui Model Context Protocol (MCP) ke MCP_SERVER_HOST.
Protokol: JSON-RPC 2.0 over HTTP POST ke endpoint /mcp/call.

ATURAN (CLAUDE.md Seksi 6.3):
- Hanya akses MCP Calendar & Task Manager.
- Tidak ada akses langsung ke Google Calendar API — semua via MCP server.
"""

from __future__ import annotations

import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from agents.the_planner.config import (
    AGENT_NAME,
    AUTO_SHIFT_MAX_CONFLICT_HOURS,
    CALENDAR_TIMEZONE,
    CONFLICT_BUFFER_MINUTES,
    CONFLICT_CHECK_WINDOW_HOURS,
    DEFAULT_DURATIONS,
    DEFAULT_REMINDER_OFFSETS,
    GOOGLE_CALENDAR_ID,
    MCP_BASE_URL,
    MCP_MAX_RETRIES,
    MCP_TIMEOUT_SECONDS,
)
from shared.logger import get_logger
from shared.schemas import PlannerResultSchema, ScheduleEntrySchema, TaskStatus

logger = get_logger("the_planner.tools")

WIB = ZoneInfo(CALENDAR_TIMEZONE)


# ============================================================
# MCP CLIENT
# Komunikasi JSON-RPC 2.0 ke mcp_server/server.py
# ============================================================

class MCPClient:
    """
    Client untuk berkomunikasi dengan MCP Server via HTTP JSON-RPC 2.0.
    Digunakan oleh The Planner untuk Calendar & Task Manager operations.

    Endpoint: POST {MCP_BASE_URL}/mcp/call
    Payload : JSON-RPC 2.0 dengan method "tools/call"
    """

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Agent-Name": AGENT_NAME,
        })

    def call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        retries: int = MCP_MAX_RETRIES,
    ) -> Dict[str, Any]:
        """
        Panggil tool via MCP server dengan JSON-RPC 2.0.

        Args:
            tool_name: Nama tool MCP (misalnya "create_calendar_event").
            arguments: Parameter tool.
            retries: Jumlah retry saat terjadi error jaringan.

        Returns:
            Dict hasil dari MCP server.

        Raises:
            RuntimeError: Jika MCP server tidak dapat dijangkau setelah retry.
            ValueError: Jika MCP server mengembalikan error logika (non-retriable).
        """
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        last_error: Optional[Exception] = None

        for attempt in range(1, retries + 2):
            try:
                logger.info(
                    f"[The Planner] MCP call: {tool_name} (attempt {attempt})"
                )
                response = self._session.post(
                    f"{MCP_BASE_URL}/mcp/call",
                    json=payload,
                    timeout=MCP_TIMEOUT_SECONDS,
                )

                if response.status_code == 503:
                    # MCP server tidak tersedia — retry
                    raise requests.ConnectionError("MCP server unavailable (503)")

                response.raise_for_status()
                rpc_response = response.json()

                # Cek JSON-RPC error field
                if "error" in rpc_response:
                    err = rpc_response["error"]
                    code = err.get("code", 0)
                    msg = err.get("message", "Unknown MCP error")
                    # Code -32600 sampai -32603: spesifikasi JSON-RPC — tidak di-retry
                    if -32603 <= code <= -32600:
                        raise ValueError(f"MCP logic error [{code}]: {msg}")
                    raise RuntimeError(f"MCP server error [{code}]: {msg}")

                result = rpc_response.get("result", {})
                logger.info(f"[The Planner] MCP call sukses: {tool_name}")
                return result

            except ValueError:
                raise  # Logic error — tidak di-retry
            except (requests.Timeout, requests.ConnectionError) as e:
                last_error = e
                logger.warning(
                    f"[The Planner] MCP attempt {attempt} gagal: {e}. "
                    f"{'Retry...' if attempt <= retries else 'Menyerah.'}"
                )
                if attempt <= retries:
                    time.sleep(attempt)  # Backoff: 1s, 2s
            except Exception as e:
                last_error = e
                logger.error(f"[The Planner] MCP error tak terduga: {e}")
                break

        raise RuntimeError(
            f"MCP server tidak dapat dijangkau setelah {retries + 1} percobaan. "
            f"Error terakhir: {last_error}"
        )


# Singleton MCP client untuk The Planner
_mcp_client: Optional[MCPClient] = None


def _get_mcp_client() -> MCPClient:
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


# ============================================================
# HELPERS — Parsing waktu & kategori
# Referensi: skills/the_planner/schedule_creation.md
# ============================================================

def _parse_datetime_hint(hint: str, base_dt: Optional[datetime] = None) -> datetime:
    """
    Parse petunjuk waktu natural ke objek datetime WIB.
    Referensi: skills/the_planner/schedule_creation.md — Logika Parsing Waktu.

    Args:
        hint: String seperti "besok jam 9 pagi", "nanti sore", "jam 14:00".
        base_dt: Datetime referensi (default: sekarang WIB).

    Returns:
        datetime dalam timezone WIB.
    """
    now = base_dt or datetime.now(tz=WIB)
    hint_lower = hint.lower().strip()

    # Ekstrak jam dari string
    hour_match = re.search(r'jam\s+(\d{1,2})(?::(\d{2}))?', hint_lower)
    hour = int(hour_match.group(1)) if hour_match else 9
    minute = int(hour_match.group(2)) if hour_match and hour_match.group(2) else 0

    # Sesuaikan AM/PM berdasarkan konteks
    if "pagi" in hint_lower and hour < 12:
        pass  # Sudah benar
    elif "siang" in hint_lower and hour < 12:
        hour += 12
    elif "sore" in hint_lower:
        hour = hour if hour >= 12 else hour + 12
        if "nanti sore" in hint_lower and not hour_match:
            hour = 16
    elif "malam" in hint_lower:
        hour = hour if hour >= 18 else hour + 12
        if "malam ini" in hint_lower and not hour_match:
            hour = 19

    # Tentukan tanggal
    if "besok" in hint_lower:
        target_date = now.date() + timedelta(days=1)
    elif "lusa" in hint_lower:
        target_date = now.date() + timedelta(days=2)
    else:
        target_date = now.date()

    scheduled = datetime(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day,
        hour=hour,
        minute=minute,
        second=0,
        tzinfo=WIB,
    )

    # Jika waktu sudah lewat hari ini → geser ke hari berikutnya
    if scheduled <= now and "besok" not in hint_lower:
        scheduled += timedelta(days=1)

    return scheduled


def _detect_category(title: str, raw_input: str) -> str:
    """
    Deteksi kategori event untuk menentukan durasi & offset reminder.
    Referensi: skills/the_planner/task_reminder_format.md.
    """
    combined = (title + " " + raw_input).lower()
    if any(w in combined for w in ["servis", "oli", "bengkel", "ban", "sparepart"]):
        return "servis"
    if any(w in combined for w in ["transfer", "bayar", "cicilan", "uang", "keuangan"]):
        return "keuangan"
    if any(w in combined for w in ["orderan", "pickup", "zona", "shift", "lapangan"]):
        return "operasional"
    return "umum"


def _format_title(title: str, category: str) -> str:
    """
    Format judul dengan prefix kategori.
    Referensi: skills/the_planner/task_reminder_format.md — Template Judul.
    """
    prefix_map = {
        "servis": "[SERVIS]",
        "keuangan": "[KEUANGAN]",
        "operasional": "[OPS]",
        "umum": "[INFO]",
    }
    prefix = prefix_map.get(category, "[INFO]")
    if not title.startswith("["):
        return f"{prefix} {title}"
    return title


# ============================================================
# TOOL 1: list_upcoming_events
# Referensi: skills/the_planner/conflict_resolution.md
# ============================================================

def list_upcoming_events(
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """
    Daftar event kalender yang akan datang. Digunakan untuk cek konflik
    SEBELUM membuat event baru (wajib per conflict_resolution.md Alur Wajib).

    Args:
        time_min: ISO8601 — batas waktu mulai (default: sekarang).
        time_max: ISO8601 — batas waktu akhir (default: sekarang + 4 jam).
        max_results: Maks event yang dikembalikan.

    Returns:
        List event yang ada dalam rentang waktu tersebut.
    """
    now = datetime.now(tz=WIB)
    t_min = time_min or now.isoformat()
    t_max = time_max or (now + timedelta(hours=CONFLICT_CHECK_WINDOW_HOURS)).isoformat()

    result = _get_mcp_client().call(
        tool_name="list_upcoming_events",
        arguments={
            "calendar_id": GOOGLE_CALENDAR_ID,
            "time_min": t_min,
            "time_max": t_max,
            "max_results": max_results,
        },
    )
    events = result.get("events", [])
    logger.info(f"[The Planner] list_upcoming_events: {len(events)} event ditemukan.")
    return events


# ============================================================
# TOOL 2: create_calendar_event
# Referensi: skills/the_planner/schedule_creation.md
#            skills/the_planner/conflict_resolution.md
# ============================================================

def create_calendar_event(entry: ScheduleEntrySchema) -> PlannerResultSchema:
    """
    Buat event kalender baru via MCP. Termasuk pengecekan konflik otomatis
    dan auto-shift jika diperlukan (conflict_resolution.md).

    Args:
        entry: ScheduleEntrySchema berisi detail event.

    Returns:
        PlannerResultSchema — hasil pembuatan event.
    """
    # --- Cek konflik sebelum membuat event ---
    t_min_check = entry.scheduled_at.isoformat()
    t_max_check = (
        entry.scheduled_at + timedelta(hours=CONFLICT_CHECK_WINDOW_HOURS)
    ).isoformat()

    existing_events = list_upcoming_events(time_min=t_min_check, time_max=t_max_check)

    # Cek overlap (sesuai conflict_resolution.md — Definisi Konflik)
    end_new = entry.scheduled_at + timedelta(minutes=entry.duration_minutes)
    conflict = None
    for ev in existing_events:
        ev_start_str = ev.get("start", {}).get("dateTime", "")
        ev_end_str = ev.get("end", {}).get("dateTime", "")
        if not ev_start_str or not ev_end_str:
            continue
        try:
            ev_start = datetime.fromisoformat(ev_start_str)
            ev_end = datetime.fromisoformat(ev_end_str)
            # Tambahan buffer 15 menit
            ev_end_buffered = ev_end + timedelta(minutes=CONFLICT_BUFFER_MINUTES)
            if entry.scheduled_at < ev_end_buffered and end_new > ev_start:
                conflict = ev
                break
        except ValueError:
            continue

    original_time: Optional[str] = None
    shift_reason: Optional[str] = None
    conflict_resolved = False

    if conflict:
        conflict_duration_min = 0
        try:
            c_end = datetime.fromisoformat(
                conflict.get("end", {}).get("dateTime", "")
            )
            c_start = datetime.fromisoformat(
                conflict.get("start", {}).get("dateTime", "")
            )
            conflict_duration_min = int((c_end - c_start).total_seconds() / 60)
        except ValueError:
            pass

        # Strategi: Auto-shift jika konflik ≤ AUTO_SHIFT_MAX_CONFLICT_HOURS
        if conflict_duration_min <= AUTO_SHIFT_MAX_CONFLICT_HOURS * 60:
            original_time = entry.scheduled_at.isoformat()
            try:
                c_end_dt = datetime.fromisoformat(
                    conflict.get("end", {}).get("dateTime", "")
                )
                entry = ScheduleEntrySchema(
                    title=entry.title,
                    scheduled_at=c_end_dt + timedelta(minutes=CONFLICT_BUFFER_MINUTES),
                    duration_minutes=entry.duration_minutes,
                    description=entry.description,
                    reminder_minutes_before=entry.reminder_minutes_before,
                    location=entry.location,
                )
                shift_reason = (
                    f"Konflik dengan event '{conflict.get('summary', 'existing')}' "
                    f"({conflict.get('start', {}).get('dateTime', '')} - "
                    f"{conflict.get('end', {}).get('dateTime', '')})"
                )
                conflict_resolved = True
                logger.info(f"[The Planner] Auto-shift ke {entry.scheduled_at.isoformat()}")
            except Exception as e:
                logger.warning(f"[The Planner] Auto-shift gagal: {e}. Melanjutkan dengan waktu asal.")
        else:
            # Strategi: kembalikan conflict_detected (Bang Jek akan konfirmasi ke user)
            return PlannerResultSchema(
                event_id="",
                status=TaskStatus.BLOCKED,
                scheduled_at=entry.scheduled_at,
                title=entry.title,
                reminder_set=False,
            )

    # --- Buat event via MCP ---
    mcp_result = _get_mcp_client().call(
        tool_name="create_calendar_event",
        arguments={
            "calendar_id": GOOGLE_CALENDAR_ID,
            "title": entry.title,
            "start_datetime": entry.scheduled_at.isoformat(),
            "end_datetime": (
                entry.scheduled_at + timedelta(minutes=entry.duration_minutes)
            ).isoformat(),
            "description": entry.description or "",
            "location": entry.location or "",
            "reminder_minutes": entry.reminder_minutes_before,
            "timezone": CALENDAR_TIMEZONE,
        },
    )

    event_id = mcp_result.get("id", str(uuid.uuid4()))
    logger.log_agent_event(
        f"CALENDAR_EVENT_CREATED: '{entry.title}' at {entry.scheduled_at.isoformat()}",
        agent_name=AGENT_NAME,
        event_id=event_id,
        conflict_resolved=conflict_resolved,
    )

    return PlannerResultSchema(
        event_id=event_id,
        status=TaskStatus.COMPLETED,
        scheduled_at=entry.scheduled_at,
        title=entry.title,
        reminder_set=entry.reminder_minutes_before > 0,
        calendar_link=mcp_result.get("htmlLink"),
    )


# ============================================================
# TOOL 3: create_task_reminder
# Referensi: skills/the_planner/task_reminder_format.md
# ============================================================

def create_task_reminder(
    title: str,
    due_datetime: datetime,
    raw_input: str = "",
    notes: str = "",
    recurrence: Optional[str] = None,
) -> PlannerResultSchema:
    """
    Buat task reminder via MCP Task Manager.

    Args:
        title: Judul tugas.
        due_datetime: Waktu deadline/due task (WIB).
        raw_input: Input asli pengguna (untuk kategorisasi otomatis).
        notes: Catatan tambahan.
        recurrence: "daily", "weekly", "monthly", atau None.

    Returns:
        PlannerResultSchema — hasil pembuatan task.
    """
    category = _detect_category(title, raw_input)
    formatted_title = _format_title(title, category)
    duration = DEFAULT_DURATIONS.get(category, 30)
    reminder_offset = DEFAULT_REMINDER_OFFSETS.get(category, 15)

    mcp_result = _get_mcp_client().call(
        tool_name="create_task_reminder",
        arguments={
            "title": formatted_title,
            "due_datetime": due_datetime.isoformat(),
            "duration_minutes": duration,
            "reminder_minutes_before": reminder_offset,
            "notes": notes,
            "recurrence": recurrence,
            "timezone": CALENDAR_TIMEZONE,
        },
    )

    task_id = mcp_result.get("id", str(uuid.uuid4()))
    logger.log_agent_event(
        f"TASK_REMINDER_CREATED: '{formatted_title}' due={due_datetime.isoformat()}",
        agent_name=AGENT_NAME,
        task_id=task_id,
        category=category,
    )

    return PlannerResultSchema(
        event_id=task_id,
        status=TaskStatus.COMPLETED,
        scheduled_at=due_datetime,
        title=formatted_title,
        reminder_set=True,
    )
