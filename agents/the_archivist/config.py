"""
agents/the_archivist/config.py
================================
Layer 4 — Konfigurasi Sub-Agent: The Archivist.

ATURAN (CLAUDE.md Seksi 4.3 & 6.4):
- AGENT_NAME WAJIB persis "The Archivist".
- Tools diizinkan: MCP Google Notes / Keep.
- Tools dilarang: BigQuery, OpenWeather, Calendar.
- Output: JSON terstruktur saja — BUKAN narasi bahasa natural.
"""

import os

# ============================================================
# KONSTANTA AGEN — IMMUTABLE
# ============================================================

AGENT_NAME: str = "The Archivist"

# MCP Server connection (shared dengan The Planner — satu MCP server)
MCP_HOST: str = os.getenv("MCP_SERVER_HOST", "localhost")
MCP_PORT: int = int(os.getenv("MCP_SERVER_PORT", "8080"))
MCP_BASE_URL: str = f"http://{MCP_HOST}:{MCP_PORT}"
MCP_TIMEOUT_SECONDS: int = 5
MCP_MAX_RETRIES: int = 2

# Google Keep / Notes config
GOOGLE_KEEP_TOKEN: str = os.getenv("GOOGLE_KEEP_TOKEN", "")

# Search constraints (sesuai skills/the_archivist/semantic_search.md)
MAX_SEARCH_RESULTS: int = 10
SEARCH_TIMEOUT_SECONDS: int = 3
SEARCH_CACHE_TTL_SECONDS: int = 300    # 5 menit cache hasil pencarian identik

# Duplikasi check window (sesuai skills/the_archivist/note_indexing.md)
DUPLICATE_CHECK_DAYS: int = 7
DUPLICATE_SIMILARITY_THRESHOLD: float = 0.85

# Tools yang diizinkan (untuk PreToolUseHook)
ALLOWED_TOOLS: tuple[str, ...] = (
    "save_note",
    "search_notes",
    "list_notes",
)

# Taksonomi tag resmi — sesuai skills/the_archivist/note_indexing.md
VALID_TAGS: frozenset = frozenset({
    "#operasional", "#keuangan", "#kendaraan",
    "#pelanggan", "#jadwal", "#info-platform", "#personal",
    "#penting", "#sementara", "#to-follow-up", "#arsip",
})

# Auto-tagging keyword map (sesuai skills/the_archivist/note_indexing.md)
AUTO_TAG_RULES: dict[str, list[str]] = {
    "#keuangan": [
        "pendapatan", "income", "bayar", "transfer",
        "rp", "rupiah", "uang", "pemasukan",
    ],
    "#kendaraan": [
        "oli", "ban", "servis", "bengkel", "sparepart",
        "motor", "kendaraan", "mesin",
    ],
    "#operasional": [
        "zona", "hotspot", "area", "orderan", "pickup",
        "ngetem", "lapangan", "trip",
    ],
    "#jadwal": [
        "jadwal", "besok", "shift", "libur", "jam",
        "rencana", "agenda",
    ],
}

# Prefix format judul standar (sesuai skills/the_archivist/note_indexing.md)
NOTE_TITLE_PREFIXES: dict[str, str] = {
    "#operasional": "[OPERASIONAL]",
    "#keuangan": "[KEUANGAN]",
    "#kendaraan": "[KENDARAAN]",
    "#jadwal": "[JADWAL]",
    "#pelanggan": "[PELANGGAN]",
    "#info-platform": "[INFO]",
    "#personal": "[PERSONAL]",
}

# ============================================================
# SYSTEM PROMPT — PERAN KNOWLEDGE BASE
# ============================================================

SYSTEM_PROMPT: str = """
Kamu adalah "The Archivist" — sub-agen Knowledge Base dalam sistem OjolBoost MAMS.

=== PERAN & TANGGUNG JAWAB ===
Kamu bertugas menyimpan dan mencari informasi penting untuk pengemudi
melalui Google Keep/Notes via MCP server.
Tujuan utamamu: memastikan setiap informasi berharga tersimpan rapi dan mudah ditemukan kembali.

=== ATURAN ABSOLUT ===
1. Kamu HANYA menerima delegasi tugas dari Bang Jek — tidak ada sumber lain.
2. Kamu HANYA boleh mengakses: MCP Google Notes / Google Keep.
3. Kamu DILARANG mengakses: BigQuery, OpenWeather API, Google Calendar, atau service lain.
4. Kamu TIDAK MEMILIKI operasi DELETE ke Google Keep — penghapusan hanya manual oleh pengguna.
5. Sebelum menyimpan catatan baru, selalu cek duplikasi dalam 7 hari terakhir.
6. Output kamu HARUS berupa JSON terstruktur — BUKAN narasi bahasa natural.

=== AUTO-TAGGING WAJIB ===
Setiap catatan WAJIB memiliki minimal 1 tag dari taksonomi resmi:
#operasional, #keuangan, #kendaraan, #pelanggan, #jadwal, #info-platform, #personal

=== FORMAT OUTPUT WAJIB ===
{
  "note_id": string,
  "title": string,
  "content": string,
  "tags": [string, ...],
  "created_at": ISO8601 string,
  "sync_status": "synced" | "pending"
}
"""
