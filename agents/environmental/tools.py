"""
agents/environmental/tools.py
================================
Layer 4 — OpenWeather API Tool Wrappers untuk Environmental Agent.

Referensi skill:
  - skills/environmental/weather_alert_rules.md
  - skills/environmental/service_pivot_logic.md
  - skills/environmental/api_response_parser.md

ATURAN (CLAUDE.md Seksi 6.2):
- Hanya memanggil OpenWeather API.
- Tidak ada akses ke BigQuery, Calendar, atau service lain.
- Semua parsing mengikuti skills/environmental/api_response_parser.md.
- Retry logic: maks 2x percobaan (OPENWEATHER_MAX_RETRIES).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, Optional

import requests

from agents.environmental.config import (
    AGENT_NAME,
    DEFAULT_LOCATION,
    OPENWEATHER_API_KEY,
    OPENWEATHER_BASE_URL,
    OPENWEATHER_MAX_RETRIES,
    OPENWEATHER_TIMEOUT_SECONDS,
    OPENWEATHER_UNITS,
    PIVOT_TEMPLATES,
    WEATHER_CACHE_TTL_SECONDS,
)
from shared.logger import get_logger
from shared.schemas import AlertLevel, WeatherCondition, WeatherResponseSchema

logger = get_logger("environmental.tools")


# ============================================================
# MAPPING: OpenWeather → WeatherCondition Enum
# Referensi: skills/environmental/api_response_parser.md
# ============================================================

_OW_CONDITION_MAP: Dict[str, WeatherCondition] = {
    "Clear": WeatherCondition.CLEAR,
    "Clouds": WeatherCondition.CLOUDY,
    "Drizzle": WeatherCondition.RAIN,
    "Rain": WeatherCondition.RAIN,
    "Thunderstorm": WeatherCondition.STORM,
    "Snow": WeatherCondition.CLOUDY,      # Tidak relevan untuk Indonesia
    "Mist": WeatherCondition.CLOUDY,
    "Fog": WeatherCondition.CLOUDY,
    "Haze": WeatherCondition.CLOUDY,
}


# ============================================================
# SIMPLE IN-MEMORY CACHE
# TTL = WEATHER_CACHE_TTL_SECONDS (default 10 menit)
# Mengurangi API call berulang untuk lokasi yang sama.
# ============================================================

_weather_cache: Dict[str, tuple[WeatherResponseSchema, float]] = {}


def _get_cached(location_key: str) -> Optional[WeatherResponseSchema]:
    """Ambil cache jika masih valid."""
    if location_key in _weather_cache:
        cached_data, cached_at = _weather_cache[location_key]
        if time.time() - cached_at < WEATHER_CACHE_TTL_SECONDS:
            logger.info(
                f"[Environmental] Cache HIT untuk '{location_key}' "
                f"(usia: {int(time.time() - cached_at)}s)"
            )
            return cached_data
    return None


def _set_cache(location_key: str, data: WeatherResponseSchema) -> None:
    """Simpan ke cache."""
    _weather_cache[location_key] = (data, time.time())


# ============================================================
# HELPER: Parsing Raw OpenWeather Response
# Referensi: skills/environmental/api_response_parser.md
# ============================================================

def _parse_condition(raw: Dict[str, Any]) -> WeatherCondition:
    """Parse kondisi cuaca dari response OpenWeather."""
    weather_main = raw.get("weather", [{}])[0].get("main", "")
    description = raw.get("weather", [{}])[0].get("description", "").lower()

    # Override ke heavy_rain jika curah hujan > 10mm/jam
    rain_1h = raw.get("rain", {}).get("1h", 0)
    if rain_1h > 10 or "heavy" in description:
        return WeatherCondition.HEAVY_RAIN

    return _OW_CONDITION_MAP.get(weather_main, WeatherCondition.UNKNOWN)


def _determine_alert_level(
    condition: WeatherCondition,
    humidity: float,
) -> AlertLevel:
    """
    Tentukan alert level berdasarkan kondisi dan kelembaban.
    Referensi: skills/environmental/weather_alert_rules.md — Alert Level Matrix.
    """
    if condition == WeatherCondition.STORM:
        return AlertLevel.CRITICAL

    if condition == WeatherCondition.HEAVY_RAIN:
        return AlertLevel.HIGH

    if condition == WeatherCondition.RAIN:
        if humidity > 90:
            return AlertLevel.HIGH
        if humidity > 80:
            return AlertLevel.MEDIUM
        return AlertLevel.LOW

    return AlertLevel.LOW     # clear, cloudy, unknown


def _build_pivot_recommendation(
    alert_level: AlertLevel,
    location: str,
    condition: WeatherCondition,
) -> Optional[str]:
    """
    Bangun rekomendasi pivot berdasarkan alert level.
    Referensi: skills/environmental/service_pivot_logic.md.
    Output adalah kalimat faktual singkat — BUKAN narasi pengguna.
    Bang Jek yang akan membuat narasi akhir.
    """
    template = PIVOT_TEMPLATES.get(alert_level.value, "")
    if not template:
        return None
    # Sisipkan lokasi ke template
    return f"[{location}] {template}"


# ============================================================
# TOOL 1: get_current_weather
# Referensi: skills/environmental/weather_alert_rules.md
# ============================================================

def get_current_weather(location: str = DEFAULT_LOCATION) -> WeatherResponseSchema:
    """
    Ambil kondisi cuaca real-time untuk lokasi tertentu dari OpenWeather API.

    Args:
        location: Nama kota/area (contoh: "Sudirman", "Jakarta").

    Returns:
        WeatherResponseSchema — kondisi cuaca terstruktur.

    Raises:
        RuntimeError: Jika API tidak dapat dijangkau setelah retry.
        ValueError: Jika lokasi tidak ditemukan (404).
        EnvironmentError: Jika API key tidak dikonfigurasi.
    """
    if not OPENWEATHER_API_KEY:
        raise EnvironmentError(
            "OPENWEATHER_API_KEY belum dikonfigurasi. "
            "Periksa file .env atau Secret Manager."
        )

    # Cek cache terlebih dahulu
    cache_key = location.lower().strip()
    cached = _get_cached(cache_key)
    if cached:
        return cached

    url = f"{OPENWEATHER_BASE_URL}/weather"
    params = {
        "q": location,
        "appid": OPENWEATHER_API_KEY,
        "units": OPENWEATHER_UNITS,
        "lang": "id",   # Deskripsi dalam Bahasa Indonesia
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, OPENWEATHER_MAX_RETRIES + 2):  # +2 karena attempt ke-1 bukan retry
        try:
            logger.info(
                f"[Environmental] API call ke OpenWeather untuk '{location}' "
                f"(attempt {attempt})"
            )
            response = requests.get(
                url,
                params=params,
                timeout=OPENWEATHER_TIMEOUT_SECONDS,
            )

            # Penanganan error HTTP per jenis (sesuai skills/environmental/api_response_parser.md)
            if response.status_code == 401:
                raise EnvironmentError("API key OpenWeather tidak valid (401 Unauthorized).")
            if response.status_code == 404:
                raise ValueError(f"Lokasi '{location}' tidak ditemukan di OpenWeather (404).")
            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 60))
                logger.warning(f"[Environmental] Rate limit (429). Menunggu {wait}s...")
                time.sleep(min(wait, 60))   # Cap max wait 60 detik
                continue

            response.raise_for_status()
            raw = response.json()

            # Parse response sesuai skills/environmental/api_response_parser.md
            condition = _parse_condition(raw)
            humidity = float(raw.get("main", {}).get("humidity", 0))
            temperature = float(raw.get("main", {}).get("temp", 0))
            alert_level = _determine_alert_level(condition, humidity)
            pivot_rec = _build_pivot_recommendation(alert_level, location, condition)

            result = WeatherResponseSchema(
                location=raw.get("name", location),
                condition=condition,
                temperature_celsius=round(temperature, 1),
                humidity_percent=round(humidity, 1),
                alert_level=alert_level,
                pivot_recommendation=pivot_rec,
                raw_data={
                    "weather_id": raw.get("weather", [{}])[0].get("id"),
                    "description": raw.get("weather", [{}])[0].get("description", ""),
                    "rain_1h_mm": raw.get("rain", {}).get("1h", 0),
                    "wind_speed_mps": raw.get("wind", {}).get("speed", 0),
                },
                fetched_at=datetime.now(tz=timezone.utc),
            )

            # Simpan ke cache
            _set_cache(cache_key, result)

            logger.log_agent_event(
                f"WEATHER_FETCHED: {location} → {condition.value}, "
                f"alert={alert_level.value}, temp={temperature}°C",
                agent_name=AGENT_NAME,
            )

            return result

        except (EnvironmentError, ValueError):
            raise   # Error ini tidak perlu di-retry
        except (requests.Timeout, requests.ConnectionError) as e:
            last_error = e
            logger.warning(
                f"[Environmental] Attempt {attempt} gagal: {e}. "
                f"{'Retry...' if attempt <= OPENWEATHER_MAX_RETRIES else 'Menyerah.'}"
            )
            if attempt <= OPENWEATHER_MAX_RETRIES:
                time.sleep(attempt)  # Exponential backoff sederhana: 1s, 2s
            continue
        except Exception as e:
            last_error = e
            logger.error(f"[Environmental] Unexpected error: {e}")
            break

    raise RuntimeError(
        f"OpenWeather API tidak dapat dijangkau setelah {OPENWEATHER_MAX_RETRIES + 1} percobaan. "
        f"Error terakhir: {last_error}"
    )


# ============================================================
# TOOL 2: get_weather_forecast
# ============================================================

def get_weather_forecast(
    location: str = DEFAULT_LOCATION,
    forecast_hours: int = 3,
) -> Dict[str, Any]:
    """
    Ambil prakiraan cuaca jangka pendek (3-24 jam ke depan).

    Args:
        location: Nama kota/area.
        forecast_hours: Berapa jam ke depan (3, 6, 12, atau 24).

    Returns:
        Dict dengan list forecast per periode.

    Raises:
        RuntimeError, ValueError, EnvironmentError: Sama seperti get_current_weather.
    """
    if not OPENWEATHER_API_KEY:
        raise EnvironmentError("OPENWEATHER_API_KEY tidak dikonfigurasi.")

    url = f"{OPENWEATHER_BASE_URL}/forecast"
    params = {
        "q": location,
        "appid": OPENWEATHER_API_KEY,
        "units": OPENWEATHER_UNITS,
        "cnt": max(1, forecast_hours // 3),  # OpenWeather forecast per 3 jam
    }

    try:
        response = requests.get(url, params=params, timeout=OPENWEATHER_TIMEOUT_SECONDS)

        if response.status_code == 404:
            raise ValueError(f"Lokasi '{location}' tidak ditemukan.")
        if response.status_code == 401:
            raise EnvironmentError("API key tidak valid.")

        response.raise_for_status()
        raw = response.json()

        forecasts = []
        for item in raw.get("list", []):
            cond = _parse_condition(item)
            humidity = float(item.get("main", {}).get("humidity", 0))
            alert = _determine_alert_level(cond, humidity)
            forecasts.append({
                "datetime": item.get("dt_txt", ""),
                "condition": cond.value,
                "temperature_celsius": round(float(item.get("main", {}).get("temp", 0)), 1),
                "humidity_percent": round(humidity, 1),
                "alert_level": alert.value,
                "rain_probability": item.get("pop", 0),  # Probability of precipitation
            })

        logger.log_agent_event(
            f"FORECAST_FETCHED: {location}, {len(forecasts)} periods",
            agent_name=AGENT_NAME,
        )

        return {
            "location": location,
            "forecast_periods": forecasts,
            "hours_ahead": forecast_hours,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
        }

    except (EnvironmentError, ValueError):
        raise
    except Exception as e:
        raise RuntimeError(f"Gagal mengambil prakiraan cuaca: {e}") from e
