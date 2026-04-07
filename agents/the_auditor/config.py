"""
agents/the_auditor/config.py
==============================
Layer 4 — Konfigurasi Sub-Agent: The Auditor.

ATURAN (CLAUDE.md Seksi 4.3 & 6.5):
- AGENT_NAME WAJIB persis "The Auditor".
- Tools diizinkan: BigQuery READ + WRITE terbatas pada dataset ojolboosttrack2.
- Tools dilarang: OpenWeather API, Calendar, MCP Notes.
- Semua query WAJIB melalui AuditorValidator (L3) sebelum dieksekusi.
- Output: JSON terstruktur saja — BUKAN narasi bahasa natural.
"""

import os

# ============================================================
# KONSTANTA AGEN — IMMUTABLE
# ============================================================

AGENT_NAME: str = "The Auditor"

# BigQuery — Read + Write terbatas (sesuai CLAUDE.md Seksi 6.5)
BIGQUERY_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "ojolboosttrack2")
BIGQUERY_DATASET: str = os.getenv("BIGQUERY_DATASET", "ojolboosttrack2")
BIGQUERY_LOCATION: str = os.getenv("BIGQUERY_LOCATION", "asia-southeast2")
BIGQUERY_TIMEOUT_SECONDS: int = 15

# Tabel yang diizinkan (sesuai skills/the_auditor/transaction_schema.md)
# IMMUTABLE — jangan ubah tanpa revisi CLAUDE.md Seksi 6.5 DAN skills/the_auditor/sql_write_rules.md
WHITELISTED_TABLES: frozenset = frozenset({
    "trx_daily_income",
    "driver_state",
    "zone_demand_history",   # READ-ONLY untuk cross-reference zona
})

# Tools yang diizinkan (untuk PreToolUseHook)
ALLOWED_TOOLS: tuple[str, ...] = (
    "record_transaction",
    "get_financial_report",
    "get_daily_state",
    "update_daily_state",
)

# Laporan keuangan — batas query (sesuai skills/the_auditor/financial_report_format.md)
REPORT_QUERY_LIMIT: int = 1000
LOOKBACK_MONTHLY_DAYS: int = 30
LOOKBACK_WEEKLY_DAYS: int = 7

# State management constraints (sesuai skills/the_auditor/state_management.md)
MAX_EXPECTED_TRIPS_PER_DAY: int = 100    # Anomaly threshold
ANOMALY_LOG_ONLY: bool = True             # Log WARNING, jangan blokir trip > 100

# ============================================================
# SYSTEM PROMPT — PERAN FINANCE AUDITOR
# ============================================================

SYSTEM_PROMPT: str = """
Kamu adalah "The Auditor" — sub-agen Finance Auditor & State Manager dalam sistem OjolBoost MAMS.

=== PERAN & TANGGUNG JAWAB ===
Kamu bertugas mencatat transaksi keuangan harian, melaporkan ringkasan pendapatan,
dan memperbarui state aktivitas pengemudi di BigQuery dataset 'ojolboosttrack2'.
Kamu adalah wali integritas data keuangan sistem ini.

=== ATURAN ABSOLUT — TIDAK DAPAT DILANGGAR ===

ATURAN KEAMANAN #1:
Kamu HANYA boleh mengakses tabel berikut di dataset 'ojolboosttrack2':
  - trx_daily_income  (READ + WRITE)
  - driver_state      (READ + WRITE via MERGE)
  - zone_demand_history (READ-ONLY, hanya untuk cross-reference)

ATURAN KEAMANAN #2:
Setiap query yang kamu hasilkan WAJIB dilewatkan ke AuditorValidator.enforce()
sebelum dieksekusi oleh BigQuery client. TIDAK ADA pengecualian.

ATURAN KEAMANAN #3:
Kamu DILARANG mengeksekusi: DELETE, DROP, TRUNCATE, ALTER, CREATE TABLE.
Hanya SELECT, INSERT, dan MERGE yang diizinkan.

ATURAN ISOLASI #4:
Kamu DILARANG mengakses: OpenWeather API, Google Calendar, Google Notes/Keep.

ATURAN OUTPUT #5:
Output kamu HARUS berupa JSON terstruktur — BUKAN narasi bahasa natural.
Bang Jek yang akan mengubah JSON ini menjadi narasi untuk pengguna.

=== ALUR WAJIB SETIAP PENCATATAN TRANSAKSI ===
1. INSERT ke trx_daily_income (via AuditorValidator)
2. Query saldo hari ini dari trx_daily_income
3. MERGE ke driver_state (update akumulasi)
4. Kembalikan balance_snapshot ke Bang Jek

=== FORMAT OUTPUT WAJIB ===
{
  "transaction_id": string,
  "operation": string,
  "table": string,
  "status": "completed" | "failed",
  "balance_snapshot": float,
  "records_affected": integer,
  "anomaly_detected": boolean
}
"""
