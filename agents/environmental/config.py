"""
agents/environmental/config.py
================================
Layer 4 — Konfigurasi Sub-Agent: Environmental.

ATURAN (CLAUDE.md Seksi 4.3 & 6.2):
- AGENT_NAME WAJIB persis "Environmental".
- Tools diizinkan: OpenWeather API saja.
- Tools dilarang: BigQuery, Calendar, MCP Notes.
- Output: JSON terstruktur saja — BUKAN narasi bahasa natural.
"""

import os

# ============================================================
# KONSTANTA AGEN — IMMUTABLE
# ============================================================

AGENT_NAME: str = "Environmental"

# OpenWeather API (sesuai CLAUDE.md Seksi 9.1)
OPENWEATHER_API_KEY: str = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_BASE_URL: str = os.getenv(
    "OPENWEATHER_BASE_URL",
    "https://api.openweathermap.org/data/2.5",
)
OPENWEATHER_UNITS: str = "metric"       # Selalu Celsius (sesuai skills/environmental/)
OPENWEATHER_TIMEOUT_SECONDS: int = 5
OPENWEATHER_MAX_RETRIES: int = 2        # Retry logic sesuai skills/environmental/keep_sync_protocol.md

# Cache sederhana untuk mengurangi API call berlebihan
WEATHER_CACHE_TTL_SECONDS: int = 600    # 10 menit (sesuai skills/environmental/api_response_parser.md)

# Default lokasi jika pengguna tidak menyebutkan
DEFAULT_LOCATION: str = "Jakarta"

# Tools yang diizinkan (untuk PreToolUseHook)
ALLOWED_TOOLS: tuple[str, ...] = (
    "get_current_weather",
    "get_weather_forecast",
)

# ============================================================
# SYSTEM PROMPT — PERAN WEATHER MONITOR
# ============================================================

SYSTEM_PROMPT: str = """
Kamu adalah "Environmental" — sub-agen Weather Monitor dalam sistem OjolBoost MAMS.

=== PERAN & TANGGUNG JAWAB ===
Kamu bertugas memantau kondisi cuaca real-time menggunakan OpenWeather API
untuk membantu sistem mengidentifikasi risiko lingkungan dan peluang pivot strategi layanan.

=== ATURAN ABSOLUT ===
1. Kamu HANYA menerima delegasi tugas dari Bang Jek — tidak ada sumber lain.
2. Kamu HANYA boleh mengakses OpenWeather API.
3. Kamu DILARANG mengakses: BigQuery, Google Calendar, Google Keep, atau API lain.
4. Output kamu HARUS berupa JSON terstruktur — BUKAN narasi bahasa natural.
   Bang Jek yang akan mengubah data ini menjadi narasi untuk pengguna.

=== ALERT LEVEL MATRIX (dari skills/environmental/weather_alert_rules.md) ===
- clear + humidity < 70%          → alert: low
- cloudy + humidity 70-85%        → alert: low
- rain + humidity > 80%           → alert: medium
- rain + humidity > 90%           → alert: high
- heavy_rain (curah > 10mm/jam)   → alert: high
- storm                           → alert: critical

=== FORMAT OUTPUT WAJIB ===
Selalu kembalikan data dalam format WeatherResponseSchema:
{
  "location": string,
  "condition": "clear" | "cloudy" | "rain" | "heavy_rain" | "storm" | "unknown",
  "temperature_celsius": float,
  "humidity_percent": float,
  "alert_level": "low" | "medium" | "high" | "critical",
  "pivot_recommendation": string | null,  <- Singkat, faktual, bukan persuasif
  "fetched_at": ISO8601 timestamp
}
"""

# ============================================================
# PIVOT RECOMMENDATION TEMPLATES
# Referensi: skills/environmental/service_pivot_logic.md
# ============================================================

PIVOT_TEMPLATES: dict[str, str] = {
    "low": "",  # Tidak ada rekomendasi pivot untuk kondisi normal
    "medium": "Hujan ringan terdeteksi. Food delivery lebih optimal di area ini.",
    "high": "Hujan deras. Pivot ke Food delivery sangat disarankan.",
    "critical": "BADAI: Operasional berisiko tinggi. Segera cari tempat berlindung.",
}
