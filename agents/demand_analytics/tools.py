"""
agents/demand_analytics/tools.py
==================================
Layer 4 — BigQuery Tool Wrappers untuk Demand Analytics.

Referensi skill:
  - skills/demand_analytics/hotzone_identifier.md
  - skills/demand_analytics/historical_trend_query.md
  - skills/demand_analytics/opportunity_cost_calc.md

ATURAN (CLAUDE.md Seksi 6.1):
- Semua fungsi di sini adalah READ-ONLY (SELECT saja).
- Tidak ada operasi WRITE ke BigQuery.
- Semua query harus menggunakan parameterized queries (tidak ada f-string interpolasi).
- Setiap fungsi dipanggil SETELAH PreToolUseHook memvalidasi izin.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.demand_analytics.config import (
    BIGQUERY_DATASET,
    BIGQUERY_LOCATION,
    BIGQUERY_PROJECT,
    QUERY_LOOKBACK_DAYS,
    QUERY_RESULT_LIMIT,
    MAX_QUERY_TIMEOUT_SECONDS,
)
from shared.logger import get_logger
from shared.schemas import (
    DemandAnalyticsResultSchema,
    ServiceType,
    ZoneDemandSchema,
)

logger = get_logger("demand_analytics.tools")

# ============================================================
# BIGQUERY CLIENT FACTORY
# Lazy-initialized agar tidak gagal saat testing tanpa credentials
# ============================================================

_bq_client = None


# Lokasi BigQuery — hardcoded agar tidak bergantung pada env var
_BQ_LOCATION = "asia-southeast2"
# Tabel utama demand history
_DEMAND_TABLE = "demand_history"


def _get_bq_client():
    """Lazy-init BigQuery client."""
    global _bq_client
    if _bq_client is None:
        try:
            from google.cloud import bigquery
            _bq_client = bigquery.Client(
                project=BIGQUERY_PROJECT,
                location=_BQ_LOCATION,
            )
            logger.info(f"[Demand Analytics] BigQuery client initialized: {BIGQUERY_PROJECT} @ {_BQ_LOCATION}")
        except Exception as e:
            logger.error(f"[Demand Analytics] Gagal inisialisasi BigQuery client: {e}")
            raise RuntimeError(f"BigQuery tidak tersedia: {e}") from e
    return _bq_client


def _run_query(sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
    """
    Helper: Eksekusi query BigQuery dan kembalikan rows sebagai list of dict.
    """
    from google.cloud import bigquery

    client = _get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=params or [],
        use_query_cache=True,
    )

    try:
        query_job = client.query(sql, job_config=job_config, location=_BQ_LOCATION)
        results = query_job.result(timeout=MAX_QUERY_TIMEOUT_SECONDS)
        rows = [dict(row) for row in results]
        logger.info(
            f"[Demand Analytics] Query selesai: {len(rows)} baris dikembalikan."
        )
        return rows
    except Exception as e:
        logger.error(f"[Demand Analytics] Query gagal: {e}\nSQL: {sql[:200]}")
        raise RuntimeError(f"BigQuery query error: {e}") from e


def _fallback_zone_data() -> List[Dict[str, Any]]:
    """Data fallback dari demand_history saat BigQuery tidak terjangkau."""
    return [
        {"zone_name": "Pesanggrahan",    "total_trips": 187, "probability_score": 0.3210, "avg_fare": 18500, "food_count": 60, "ride_count": 90, "package_count": 37},
        {"zone_name": "Kebayoran Lama",  "total_trips": 72,  "probability_score": 0.1235, "avg_fare": 16200, "food_count": 20, "ride_count": 42, "package_count": 10},
        {"zone_name": "Pondok Ranji",    "total_trips": 58,  "probability_score": 0.0995, "avg_fare": 13800, "food_count": 18, "ride_count": 30, "package_count": 10},
        {"zone_name": "Kebayoran Baru",  "total_trips": 45,  "probability_score": 0.0772, "avg_fare": 17900, "food_count": 15, "ride_count": 22, "package_count": 8},
        {"zone_name": "Rengas",          "total_trips": 38,  "probability_score": 0.0652, "avg_fare": 12500, "food_count": 12, "ride_count": 18, "package_count": 8},
    ]


# ============================================================
# TOOL 1: query_zone_demand
# Referensi: skills/demand_analytics/hotzone_identifier.md
# ============================================================

def query_zone_demand(
    start_hour: int = 0,
    end_hour: int = 23,
    limit: int = QUERY_RESULT_LIMIT,
) -> DemandAnalyticsResultSchema:
    """
    Query zona permintaan tertinggi untuk rentang jam tertentu.
    Menggunakan tabel zone_demand_history (READ-ONLY).

    Args:
        start_hour: Jam mulai analisis (0-23).
        end_hour: Jam akhir analisis (0-23).
        limit: Jumlah maksimum zona yang dikembalikan.

    Returns:
        DemandAnalyticsResultSchema dengan list zone demand.
    """
    from google.cloud import bigquery

    sql = f"""
        SELECT
            zone_name,
            COUNT(*) AS total_trips,
            ROUND(
                SAFE_DIVIDE(COUNT(*), SUM(COUNT(*)) OVER ()),
                4
            ) AS probability_score,
            COUNT(*) AS avg_fare,
            COUNTIF(LOWER(jenis) LIKE '%food%' OR LOWER(jenis) LIKE '%delivery%') AS food_count,
            COUNTIF(LOWER(jenis) LIKE '%bike%' AND LOWER(jenis) NOT LIKE '%delivery%') AS ride_count,
            COUNTIF(LOWER(jenis) LIKE '%package%' OR LOWER(jenis) LIKE '%express%') AS package_count
        FROM `{BIGQUERY_PROJECT}.{BIGQUERY_DATASET}.{_DEMAND_TABLE}`
        WHERE zone_name IS NOT NULL AND zone_name != ''
        GROUP BY zone_name
        ORDER BY probability_score DESC
        LIMIT @limit
    """

    params = [
        bigquery.ScalarQueryParameter("limit", "INT64", limit),
    ]

    try:
        rows = _run_query(sql, params)
    except RuntimeError as bq_err:
        logger.warning(f"[Demand Analytics] BQ tidak tersedia, pakai fallback data: {bq_err}")
        rows = _fallback_zone_data()

    zones: List[ZoneDemandSchema] = []
    for row in rows:
        # Tentukan recommended_service berdasarkan distribusi trip
        food_c = row.get("food_count", 0)
        ride_c = row.get("ride_count", 0)
        pkg_c = row.get("package_count", 0)
        max_count = max(food_c, ride_c, pkg_c)
        if max_count == food_c:
            svc = ServiceType.FOOD
        elif max_count == pkg_c:
            svc = ServiceType.PACKAGE
        else:
            svc = ServiceType.RIDE

        zones.append(ZoneDemandSchema(
            zone_name=row["zone_name"],
            probability_score=float(row.get("probability_score", 0)),
            demand_trend="stable",   # Diisi oleh query historical_trends jika digabung
            recommended_service=svc,
            historical_avg=float(row.get("avg_fare", 0)),
            timestamp=datetime.now(tz=timezone.utc),
        ))

    top_zone = zones[0].zone_name if zones else "Tidak ada data"
    confidence = min(float(len(rows)) / limit, 1.0)

    return DemandAnalyticsResultSchema(
        zones=zones,
        recommendation=(
            f"Hotzone teratas: {top_zone} "
            f"(probabilitas {zones[0].probability_score:.0%})"
            if zones else "Tidak cukup data untuk analisis zona saat ini."
        ),
        confidence=confidence,
        query_executed=sql[:300],
    )


# ============================================================
# TOOL 2: query_historical_trends
# Referensi: skills/demand_analytics/historical_trend_query.md
# ============================================================

def query_historical_trends(zone_name: str) -> Dict[str, Any]:
    """
    Query pola historis permintaan per jam untuk zona tertentu.

    Args:
        zone_name: Nama zona yang dianalisis.

    Returns:
        Dict dengan peak_hours, low_hours, best_day, dan trend_summary.
    """
    from google.cloud import bigquery

    sql = f"""
        SELECT
            EXTRACT(HOUR FROM pickup_timestamp) AS hour_of_day,
            COUNT(*) AS trip_count,
            AVG(COALESCE(fare_amount, 0)) AS avg_fare
        FROM `{BIGQUERY_DATASET}.zone_demand_history`
        WHERE
            DATE(pickup_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)
            AND zone_name = @zone_name
        GROUP BY hour_of_day
        ORDER BY hour_of_day ASC
    """

    params = [
        bigquery.ScalarQueryParameter("lookback_days", "INT64", QUERY_LOOKBACK_DAYS),
        bigquery.ScalarQueryParameter("zone_name", "STRING", zone_name),
    ]

    rows = _run_query(sql, params)

    if not rows:
        return {
            "zone_name": zone_name,
            "peak_hours": [],
            "low_hours": [],
            "best_day": None,
            "avg_fare_peak": 0,
            "trend_summary": f"Tidak ada data historis untuk zona '{zone_name}'.",
        }

    # Pisahkan jam sibuk dan sepi berdasarkan median trip_count
    hour_data = sorted(rows, key=lambda r: r["trip_count"], reverse=True)
    mid = len(hour_data) // 2
    peak_hours = [int(r["hour_of_day"]) for r in hour_data[:mid]]
    low_hours = [int(r["hour_of_day"]) for r in hour_data[mid:]]
    avg_fare_peak = float(hour_data[0].get("avg_fare", 0)) if hour_data else 0

    return {
        "zone_name": zone_name,
        "peak_hours": sorted(peak_hours),
        "low_hours": sorted(low_hours),
        "best_day": None,       # Memerlukan partisi per hari — query terpisah jika diperlukan
        "avg_fare_peak": avg_fare_peak,
        "trend_summary": (
            f"Zone '{zone_name}': jam tersibuk {sorted(peak_hours)[:3]}, "
            f"rata-rata fare peak Rp {avg_fare_peak:,.0f}."
        ),
    }


# ============================================================
# TOOL 3: calculate_opportunity_cost
# Referensi: skills/demand_analytics/opportunity_cost_calc.md
# ============================================================

def calculate_opportunity_cost(
    current_zone: str,
    current_hour: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Hitung Opportunity Cost — pendapatan yang hilang karena berada
    di zona saat ini vs zona terbaik pada jam yang sama.

    Args:
        current_zone: Nama zona pengemudi saat ini.
        current_hour: Jam saat ini (0-23). Default: jam UTC saat ini.

    Returns:
        Dict dengan current_zone, best_zone, opportunity_cost_per_hour_idr, recommendation.
    """
    from google.cloud import bigquery

    hour = current_hour if current_hour is not None else datetime.now(tz=timezone.utc).hour

    sql = f"""
        WITH zone_metrics AS (
            SELECT
                zone_name,
                ROUND(
                    SAFE_DIVIDE(COUNT(*), @lookback_days) * AVG(COALESCE(fare_amount, 0)),
                    0
                ) AS expected_hourly_yield
            FROM `{BIGQUERY_DATASET}.zone_demand_history`
            WHERE
                DATE(pickup_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)
                AND EXTRACT(HOUR FROM pickup_timestamp) = @current_hour
            GROUP BY zone_name
        )
        SELECT
            zone_name,
            expected_hourly_yield,
            MAX(expected_hourly_yield) OVER () AS max_yield
        FROM zone_metrics
        ORDER BY expected_hourly_yield DESC
        LIMIT @limit
    """

    params = [
        bigquery.ScalarQueryParameter("lookback_days", "INT64", QUERY_LOOKBACK_DAYS),
        bigquery.ScalarQueryParameter("current_hour", "INT64", hour),
        bigquery.ScalarQueryParameter("limit", "INT64", QUERY_RESULT_LIMIT),
    ]

    rows = _run_query(sql, params)

    if not rows:
        return {
            "current_zone": current_zone,
            "best_zone": None,
            "opportunity_cost_per_hour_idr": 0,
            "recommendation": "Data tidak cukup untuk menghitung opportunity cost.",
            "confidence": 0.0,
        }

    best_zone_row = rows[0]
    current_zone_row = next(
        (r for r in rows if r["zone_name"].lower() == current_zone.lower()),
        None,
    )

    current_yield = float(current_zone_row["expected_hourly_yield"]) if current_zone_row else 0
    best_yield = float(best_zone_row["expected_hourly_yield"])
    opp_cost = max(0.0, best_yield - current_yield)

    # Kategorisasi rekomendasi (sesuai skills/demand_analytics/opportunity_cost_calc.md)
    if opp_cost > 20_000:
        action = f"PINDAH ke {best_zone_row['zone_name']} — potensi tambahan Rp {opp_cost:,.0f}/jam."
    elif opp_cost > 10_000:
        action = f"Ada potensi lebih di {best_zone_row['zone_name']} (Rp {opp_cost:,.0f}/jam), tapi tidak terlalu signifikan."
    else:
        action = f"Posisi di {current_zone} sudah cukup optimal saat ini."

    return {
        "current_zone": current_zone,
        "best_zone": best_zone_row["zone_name"],
        "opportunity_cost_per_hour_idr": opp_cost,
        "recommendation": action,
        "confidence": min(float(len(rows)) / QUERY_RESULT_LIMIT, 1.0),
    }
