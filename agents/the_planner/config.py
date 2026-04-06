"""
agents/the_planner/config.py
==============================
Layer 4 — Konfigurasi Sub-Agent: The Planner.

ATURAN (CLAUDE.md Seksi 4.3 & 6.3):
- AGENT_NAME WAJIB persis "The Planner".
- Tools diizinkan: MCP Calendar & MCP Task Manager.
- Tools dilarang: BigQuery, OpenWeather, MCP Notes/Keep.
- Output: JSON terstruktur saja — BUKAN narasi bahasa natural.
"""

import os

# ============================================================
# KONSTANTA AGEN — IMMUTABLE
# ============================================================

AGENT_NAME: str = "The Planner"

# MCP Server connection (sesuai CLAUDE.md Seksi 9.1)
MCP_HOST: str = os.getenv("MCP_SERVER_HOST", "localhost")
MCP_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8080"))

# Auto-detect protokol: HTTPS jika host adalah URL remote (seperti Cloud Run)
if MCP_HOST in ("localhost", "127.0.0.1"):
    MCP_BASE_URL: str = f"http://{MCP_HOST}:{MCP_PORT}"
else:
    # Karena API ter-deploy di GCP, port 443 dan HTTPS adalah default
    MCP_BASE_URL: str = f"https://{MCP_HOST}"
    
MCP_TIMEOUT_SECONDS: int = 10
MCP_MAX_RETRIES: int = 2

# Google Calendar config
GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
CALENDAR_TIMEZONE: str = "Asia/Jakarta"      # WIB — sesuai skills/the_planner/schedule_creation.md

# Tools yang diizinkan (untuk PreToolUseHook)
ALLOWED_TOOLS: tuple[str, ...] = (
    "create_calendar_event",
    "create_task_reminder",
    "list_upcoming_events",
)

# Conflict resolution constraints (sesuai skills/the_planner/conflict_resolution.md)
CONFLICT_BUFFER_MINUTES: int = 15           # Buffer minimum antar event
CONFLICT_CHECK_WINDOW_HOURS: int = 4       # Jendela cek konflik ke depan
MAX_EVENT_DURATION_MINUTES: int = 480      # 8 jam max per event
AUTO_SHIFT_MAX_CONFLICT_HOURS: int = 2     # Auto-shift hanya jika konflik ≤ 2 jam

# Default duration per kategori (sesuai skills/the_planner/task_reminder_format.md)
DEFAULT_DURATIONS: dict[str, int] = {
    "servis": 60,
    "keuangan": 15,
    "operasional": 30,
    "umum": 30,
}
DEFAULT_REMINDER_OFFSETS: dict[str, int] = {
    "servis": 30,
    "keuangan": 15,
    "operasional": 10,
    "umum": 15,
}

# ============================================================
# SYSTEM PROMPT — PERAN OPERATIONS MANAGER
# ============================================================

SYSTEM_PROMPT: str = """
Kamu adalah "The Planner" — sub-agen Operations Manager dalam sistem OjolBoost MAMS.

=== PERAN & TANGGUNG JAWAB ===
Kamu bertugas mengelola jadwal, reservasi tugas harian, dan pengingat untuk pengemudi
melalui Google Calendar dan Task Manager via MCP server.
Tujuan utamamu: memastikan setiap komitmen waktu pengemudi tercatat dan tidak bentrok.

=== ATURAN ABSOLUT ===
1. Kamu HANYA menerima delegasi tugas dari Bang Jek — tidak ada sumber lain.
2. Kamu HANYA boleh mengakses: MCP Calendar dan MCP Task Manager.
3. Kamu DILARANG mengakses: BigQuery, OpenWeather API, Google Notes/Keep, atau service lain.
4. SETIAP KALI membuat event baru, kamu WAJIB cek konflik terlebih dahulu (list_upcoming_events).
5. Output kamu HARUS berupa JSON terstruktur — BUKAN narasi bahasa natural.

=== ALUR WAJIB SEBELUM CREATE EVENT ===
1. list_upcoming_events() untuk cek slot yang ada
2. Periksa konflik (overlap ≥ 15 menit buffer)
3. Jika konflik: auto-shift atau kembalikan info konflik ke Bang Jek
4. Baru kemudian: create_calendar_event() atau create_task_reminder()

=== FORMAT OUTPUT WAJIB ===
{
  "event_id": string,
  "status": "completed" | "failed" | "conflict_detected",
  "scheduled_at": ISO8601 string,
  "title": string,
  "reminder_set": boolean,
  "conflict_resolved": boolean,
  "original_time": ISO8601 string | null,
  "shift_reason": string | null
}
"""
