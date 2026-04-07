"""
agents/the_auditor/tools.py
==============================
Layer 4 — BigQuery Tool Wrappers untuk The Auditor.

Referensi skill:
  - skills/the_auditor/transaction_schema.md  (DDL 3 tabel)
  - skills/the_auditor/sql_write_rules.md     (template SQL yang valid)
  - skills/the_auditor/financial_report_format.md
  - skills/the_auditor/state_management.md

ATURAN MUTLAK (CLAUDE.md Seksi 6.5):
Setiap fungsi yang mengeksekusi query ke BigQuery WAJIB memanggil
verify_and_clean_query() dari validator.py SEBELUM bigquery.Client.query().
Tidak ada satu pun query yang boleh dieksekusi tanpa melewati gerbang ini.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from agents.the_auditor.config import (
    AGENT_NAME,
    ANOMALY_LOG_ONLY,
    BIGQUERY_DATASET,
    BIGQUERY_LOCATION,
    BIGQUERY_PROJECT,
    BIGQUERY_TIMEOUT_SECONDS,
    LOOKBACK_MONTHLY_DAYS,
    LOOKBACK_WEEKLY_DAYS,
    MAX_EXPECTED_TRIPS_PER_DAY,
    REPORT_QUERY_LIMIT,
)
from agents.the_auditor.validator import (
    CleanedQuery,
    validate_service_type,
    validate_transaction_amount,
    verify_and_clean_query,
)
from shared.logger import get_logger
from shared.schemas import (
    AuditorResultSchema,
    ServiceType,
    TaskStatus,
    TransactionSchema,
)

logger = get_logger("the_auditor.tools")

DS = BIGQUERY_DATASET   # Alias pendek untuk SQL templates


# ============================================================
# BIGQUERY CLIENT FACTORY — Lazy-initialized
# ============================================================

_bq_client = None

# Hardcode location agar tidak bergantung pada env var yang unreliable
_BQ_LOCATION = "asia-southeast2"


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
            logger.info(f"[The Auditor] BigQuery client initialized: {BIGQUERY_PROJECT} @ {_BQ_LOCATION}")
        except Exception as e:
            raise RuntimeError(f"[The Auditor] BigQuery tidak tersedia: {e}") from e
    return _bq_client


def _execute_validated_query(
    cleaned: CleanedQuery,
    params: Optional[list] = None,
    operation_context: str = "unknown",
) -> List[Dict[str, Any]]:
    """
    Eksekusi CleanedQuery yang SUDAH divalidasi oleh verify_and_clean_query().
    Fungsi ini tidak boleh dipanggil langsung — panggil via helper tools di bawah.

    Args:
        cleaned: CleanedQuery dari verify_and_clean_query() dengan validated=True.
        params: List BigQuery QueryParameter untuk parameterized queries.
        operation_context: Label untuk logging.

    Returns:
        List[Dict] — baris hasil query.

    Raises:
        AssertionError: Jika CleanedQuery.validated = False (programming error).
        RuntimeError: Jika eksekusi BigQuery gagal.
    """
    assert cleaned.validated, (
        "CRITICAL: Mencoba mengeksekusi query yang BELUM divalidasi. "
        "Ini adalah bug — panggil verify_and_clean_query() terlebih dahulu."
    )

    from google.cloud import bigquery

    client = _get_bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=params or [],
        use_query_cache=True,
    )

    try:
        logger.info(
            f"[The Auditor] Executing [{operation_context}]: "
            f"{cleaned.validated_sql[:120]}{'...' if len(cleaned.validated_sql) > 120 else ''}"
        )
        job = client.query(cleaned.validated_sql, job_config=job_config, location=_BQ_LOCATION)
        result = job.result(timeout=BIGQUERY_TIMEOUT_SECONDS)
        rows = [dict(row) for row in result]
        logger.info(
            f"[The Auditor] [{operation_context}] selesai: {len(rows)} baris, "
            f"job_id={job.job_id}"
        )
        return rows
    except Exception as e:
        logger.error(f"[The Auditor] BigQuery error [{operation_context}]: {e}")
        raise RuntimeError(f"BigQuery execution error: {e}") from e


# ============================================================
# TOOL 1: record_transaction
# Referensi: skills/the_auditor/sql_write_rules.md — INSERT Transaksi Harian
#            skills/the_auditor/state_management.md — Alur Setelah INSERT
# ============================================================

def record_transaction(transaction: TransactionSchema) -> AuditorResultSchema:
    """
    Catat transaksi pendapatan baru ke tabel trx_daily_income,
    lalu perbarui driver_state secara atomik (via dua operasi berurutan).

    Alur (sesuai skills/the_auditor/state_management.md):
      1. Validasi amount dan service_type
      2. verify_and_clean_query(INSERT sql)  ← L3 Gate
      3. Eksekusi INSERT ke trx_daily_income
      4. Query saldo terakumulasi hari ini
      5. verify_and_clean_query(MERGE sql)   ← L3 Gate
      6. Eksekusi MERGE ke driver_state

    Args:
        transaction: TransactionSchema yang sudah divalidasi dari shared/schemas.py.

    Returns:
        AuditorResultSchema — hasil operasi + balance_snapshot.

    Raises:
        PermissionError: Jika query diblokir oleh AuditorValidator (L3).
        ValueError: Jika data transaksi tidak valid.
        RuntimeError: Jika BigQuery gagal.
    """
    from google.cloud import bigquery

    # --- Validasi tambahan (L4 layer) ---
    validate_transaction_amount(transaction.amount, context="record_transaction")
    svc_type = validate_service_type(transaction.service_type.value
                                     if hasattr(transaction.service_type, 'value')
                                     else str(transaction.service_type))

    # Generate transaction_id jika belum ada
    txn_id = transaction.transaction_id or str(uuid.uuid4())
    driver_id = transaction.driver_id or "default_driver"
    txn_date = transaction.transaction_date or datetime.now(tz=timezone.utc)
    zone = transaction.zone or ""
    notes = transaction.notes or ""

    # --- Step 1: INSERT ke trx_daily_income ---
    insert_sql = f"""
        INSERT INTO `{DS}.trx_daily_income`
            (transaction_id, amount, transaction_date, service_type,
             zone, notes, driver_id, created_at, status)
        VALUES
            (@transaction_id, @amount, @transaction_date, @service_type,
             @zone, @notes, @driver_id, CURRENT_TIMESTAMP(), 'recorded')
    """

    # ════ L3 GATE: verify_and_clean_query ════
    cleaned_insert = verify_and_clean_query(
        sql=insert_sql,
        operation_context="record_transaction:INSERT",
    )

    insert_params = [
        bigquery.ScalarQueryParameter("transaction_id", "STRING", txn_id),
        bigquery.ScalarQueryParameter("amount", "FLOAT64", float(transaction.amount)),
        bigquery.ScalarQueryParameter("transaction_date", "TIMESTAMP",
                                      txn_date.isoformat()),
        bigquery.ScalarQueryParameter("service_type", "STRING", svc_type),
        bigquery.ScalarQueryParameter("zone", "STRING", zone),
        bigquery.ScalarQueryParameter("notes", "STRING", notes),
        bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id),
    ]

    _execute_validated_query(
        cleaned_insert,
        params=insert_params,
        operation_context="record_transaction:INSERT",
    )

    # --- Step 2: Query saldo hari ini untuk state update ---
    balance_sql = f"""
        SELECT
            SUM(amount)  AS total_income_today,
            COUNT(*)     AS trip_count_today
        FROM `{DS}.trx_daily_income`
        WHERE DATE(transaction_date) = CURRENT_DATE()
          AND driver_id = @driver_id
          AND status = 'recorded'
    """

    # ════ L3 GATE ════
    cleaned_balance = verify_and_clean_query(
        sql=balance_sql,
        operation_context="record_transaction:BALANCE_QUERY",
    )

    balance_rows = _execute_validated_query(
        cleaned_balance,
        params=[bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id)],
        operation_context="record_transaction:BALANCE_QUERY",
    )

    total_today = float(balance_rows[0].get("total_income_today") or 0) if balance_rows else float(transaction.amount)
    trip_count = int(balance_rows[0].get("trip_count_today") or 1) if balance_rows else 1

    # --- Deteksi anomali trip count (state_management.md) ---
    anomaly_detected = False
    if trip_count > MAX_EXPECTED_TRIPS_PER_DAY:
        anomaly_detected = True
        if ANOMALY_LOG_ONLY:
            logger.warning(
                f"[The Auditor] ANOMALI: trip_count_today {trip_count} "
                f"melebihi threshold {MAX_EXPECTED_TRIPS_PER_DAY}. "
                f"Transaksi tetap dicatat. driver_id={driver_id}"
            )

    # --- Step 3: MERGE ke driver_state (sesuai state_management.md) ---
    merge_sql = f"""
        MERGE `{DS}.driver_state` AS target
        USING (
            SELECT @driver_id AS driver_id, CURRENT_DATE() AS state_date
        ) AS source
        ON target.driver_id = source.driver_id
           AND target.state_date = source.state_date
        WHEN MATCHED THEN
            UPDATE SET
                total_income_today = @total_income_today,
                trip_count_today   = @trip_count_today,
                last_zone          = @last_zone,
                updated_at         = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (driver_id, state_date, total_income_today, trip_count_today,
                    active_hours, last_zone, updated_at)
            VALUES (@driver_id, CURRENT_DATE(), @total_income_today, @trip_count_today,
                    0.0, @last_zone, CURRENT_TIMESTAMP())
    """

    # ════ L3 GATE ════
    cleaned_merge = verify_and_clean_query(
        sql=merge_sql,
        operation_context="record_transaction:STATE_MERGE",
    )

    merge_params = [
        bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id),
        bigquery.ScalarQueryParameter("total_income_today", "FLOAT64", total_today),
        bigquery.ScalarQueryParameter("trip_count_today", "INT64", trip_count),
        bigquery.ScalarQueryParameter("last_zone", "STRING", zone),
    ]

    _execute_validated_query(
        cleaned_merge,
        params=merge_params,
        operation_context="record_transaction:STATE_MERGE",
    )

    logger.log_agent_event(
        f"TRANSACTION_RECORDED: txn_id={txn_id}, amount=Rp{transaction.amount:,.0f}, "
        f"balance_today=Rp{total_today:,.0f}, trips={trip_count}",
        agent_name=AGENT_NAME,
    )

    return AuditorResultSchema(
        transaction_id=txn_id,
        operation="INSERT + STATE_UPDATE",
        table=f"{DS}.trx_daily_income + {DS}.driver_state",
        status=TaskStatus.COMPLETED,
        balance_snapshot=total_today,
        records_affected=1,
        anomaly_detected=anomaly_detected,
    )


# ============================================================
# TOOL 2: get_financial_report
# Referensi: skills/the_auditor/financial_report_format.md
# ============================================================

def get_financial_report(
    period: str = "daily",
    driver_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Hasilkan laporan keuangan sesuai periode yang diminta.
    Mendukung: 'daily', 'weekly', 'monthly', 'snapshot'.

    Referensi: skills/the_auditor/financial_report_format.md — 4 Jenis Laporan.

    Args:
        period: "daily" | "weekly" | "monthly" | "snapshot".
        driver_id: ID pengemudi (opsional, filter data).

    Returns:
        Dict laporan keuangan terstruktur.
    """
    from google.cloud import bigquery

    period = period.lower().strip()
    driver_filter = "AND driver_id = @driver_id" if driver_id else ""

    # -- Snapshot (real-time hari ini) --
    if period == "snapshot":
        sql = f"""
            SELECT
                SUM(amount)                       AS total_income_today,
                COUNT(*)                           AS trip_count_today,
                MAX(transaction_date)              AS last_trip_at
            FROM `{DS}.trx_daily_income`
            WHERE DATE(transaction_date) = CURRENT_DATE()
              AND status = 'recorded'
              {driver_filter}
        """
        params = ([bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id)]
                  if driver_id else [])

        # ════ L3 GATE ════
        cleaned = verify_and_clean_query(sql, operation_context="get_financial_report:snapshot")
        rows = _execute_validated_query(cleaned, params=params,
                                        operation_context="get_financial_report:snapshot")
        row = rows[0] if rows else {}

        return {
            "snapshot_at": datetime.now(tz=timezone.utc).isoformat(),
            "total_income_today": float(row.get("total_income_today") or 0),
            "trip_count_today": int(row.get("trip_count_today") or 0),
            "last_trip_at": str(row.get("last_trip_at") or ""),
            "status": "active",
        }

    # -- Laporan periodik (daily, weekly, monthly) --
    # Query dari demand_history (tabel utama yang sudah terisi 583 rows)
    lookback = {
        "daily": 1,
        "weekly": LOOKBACK_WEEKLY_DAYS,
        "monthly": LOOKBACK_MONTHLY_DAYS,
    }.get(period, 1)

    sql = f"""
        SELECT
            COUNT(*)                                                    AS transaction_count,
            SUM(SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64))  AS total_income,
            AVG(SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64))  AS average_per_trip,
            MAX(SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64))  AS max_trip,
            MIN(SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64))  AS min_trip,
            COUNTIF(LOWER(jenis) LIKE '%bike%')       AS ride_count,
            COUNTIF(LOWER(jenis) LIKE '%food%')       AS food_count,
            COUNTIF(LOWER(jenis) LIKE '%package%')    AS package_count,
            SUM(IF(LOWER(jenis) LIKE '%bike%',    SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64), 0)) AS ride_income,
            SUM(IF(LOWER(jenis) LIKE '%food%',    SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64), 0)) AS food_income,
            SUM(IF(LOWER(jenis) LIKE '%package%', SAFE_CAST(REGEXP_REPLACE(biaya, r'[^0-9]', '') AS INT64), 0)) AS package_income
        FROM `{BIGQUERY_PROJECT}.{DS}.demand_history`
        WHERE SAFE.PARSE_DATE('%d.%m.%Y', tanggal)
              >= DATE_SUB(CURRENT_DATE(), INTERVAL @lookback_days DAY)
    """

    params = [
        bigquery.ScalarQueryParameter("lookback_days", "INT64", lookback),
    ]

    try:
        # ════ L3 GATE ════
        cleaned = verify_and_clean_query(
            sql, operation_context=f"get_financial_report:{period}"
        )
        rows = _execute_validated_query(
            cleaned, params=params, operation_context=f"get_financial_report:{period}"
        )
        row = rows[0] if rows else {}
    except Exception as bq_err:
        logger.warning(f"[The Auditor] BQ tidak tersedia untuk laporan, pakai fallback: {bq_err}")
        row = {
            "total_income": 0, "transaction_count": 0,
            "average_per_trip": 0, "max_trip": 0, "min_trip": 0,
            "ride_income": 0, "food_income": 0, "package_income": 0,
        }

    # Format angka sesuai financial_report_format.md — Aturan Penyajian Angka
    total = float(row.get("total_income") or 0)
    count = int(row.get("transaction_count") or 0)

    return {
        "report_period": period,
        "lookback_days": lookback,
        "total_income": total,
        "transaction_count": count,
        "average_per_trip": float(row.get("average_per_trip") or 0),
        "max_trip": float(row.get("max_trip") or 0),
        "min_trip": float(row.get("min_trip") or 0),
        "by_service_type": {
            "ride": float(row.get("ride_income") or 0),
            "food": float(row.get("food_income") or 0),
            "package": float(row.get("package_income") or 0),
        },
        "message": "Belum ada transaksi tercatat untuk periode ini." if count == 0 else None,
    }


# ============================================================
# TOOL 3: get_daily_state
# Referensi: skills/the_auditor/state_management.md — Query: Ambil State Saat Ini
# ============================================================

def get_daily_state(driver_id: str = "default_driver") -> Dict[str, Any]:
    """
    Ambil snapshot state aktivitas pengemudi hari ini dari driver_state.
    Jika state hari ini belum ada, inisialisasi state baru.

    Referensi: skills/the_auditor/state_management.md.
    """
    from google.cloud import bigquery

    sql = f"""
        SELECT
            total_income_today,
            trip_count_today,
            active_hours,
            last_zone,
            updated_at
        FROM `{DS}.driver_state`
        WHERE driver_id = @driver_id
          AND state_date = CURRENT_DATE()
        LIMIT 1
    """

    # ════ L3 GATE ════
    cleaned = verify_and_clean_query(sql, operation_context="get_daily_state:SELECT")
    rows = _execute_validated_query(
        cleaned,
        params=[bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id)],
        operation_context="get_daily_state:SELECT",
    )

    if not rows:
        # State hari ini belum ada — kembalikan state kosong
        logger.info(
            f"[The Auditor] State hari ini belum ada untuk driver={driver_id}. "
            f"Mengembalikan state awal."
        )
        return {
            "driver_id": driver_id,
            "state_date": date.today().isoformat(),
            "total_income_today": 0.0,
            "trip_count_today": 0,
            "active_hours": 0.0,
            "last_zone": None,
            "updated_at": None,
            "is_new_state": True,
        }

    row = rows[0]

    # Deteksi anomali: total_income menurun (state_management.md — Penanganan Anomali)
    # Ini akan di-cross-check saat update_daily_state

    return {
        "driver_id": driver_id,
        "state_date": date.today().isoformat(),
        "total_income_today": float(row.get("total_income_today") or 0),
        "trip_count_today": int(row.get("trip_count_today") or 0),
        "active_hours": float(row.get("active_hours") or 0),
        "last_zone": row.get("last_zone"),
        "updated_at": str(row.get("updated_at") or ""),
        "is_new_state": False,
    }


# ============================================================
# TOOL 4: update_daily_state
# Referensi: skills/the_auditor/state_management.md — Query: Update State
# ============================================================

def update_daily_state(
    driver_id: str,
    total_income_today: float,
    trip_count_today: int,
    last_zone: Optional[str] = None,
    active_hours: float = 0.0,
) -> Dict[str, Any]:
    """
    Perbarui state harian pengemudi menggunakan MERGE pattern (upsert).
    Deteksi anomali: total_income tidak boleh turun dari nilai sebelumnya.

    Referensi: skills/the_auditor/state_management.md — Penanganan Anomali.
    """
    from google.cloud import bigquery

    # Cek state sebelumnya untuk anomali detection
    current_state = get_daily_state(driver_id)
    prev_income = current_state.get("total_income_today", 0.0)

    anomaly_detected = False
    if total_income_today < prev_income and not current_state.get("is_new_state", False):
        anomaly_detected = True
        logger.warning(
            f"[The Auditor] ANOMALI STATE: total_income_today turun! "
            f"Sebelumnya: Rp{prev_income:,.0f} → Sekarang: Rp{total_income_today:,.0f}. "
            f"driver_id={driver_id}"
        )
        # TIDAK memblokir — log warning saja (state_management.md Penanganan Anomali)

    merge_sql = f"""
        MERGE `{DS}.driver_state` AS target
        USING (
            SELECT @driver_id AS driver_id, CURRENT_DATE() AS state_date
        ) AS source
        ON target.driver_id = source.driver_id
           AND target.state_date = source.state_date
        WHEN MATCHED THEN
            UPDATE SET
                total_income_today = @total_income_today,
                trip_count_today   = @trip_count_today,
                last_zone          = @last_zone,
                updated_at         = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
            INSERT (driver_id, state_date, total_income_today, trip_count_today,
                    active_hours, last_zone, updated_at)
            VALUES (@driver_id, CURRENT_DATE(), @total_income_today, @trip_count_today,
                    @active_hours, @last_zone, CURRENT_TIMESTAMP())
    """

    # ════ L3 GATE ════
    cleaned = verify_and_clean_query(merge_sql, operation_context="update_daily_state:MERGE")

    params = [
        bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id),
        bigquery.ScalarQueryParameter("total_income_today", "FLOAT64", total_income_today),
        bigquery.ScalarQueryParameter("trip_count_today", "INT64", trip_count_today),
        bigquery.ScalarQueryParameter("last_zone", "STRING", last_zone or ""),
        bigquery.ScalarQueryParameter("active_hours", "FLOAT64", active_hours),
    ]

    _execute_validated_query(
        cleaned, params=params, operation_context="update_daily_state:MERGE"
    )

    logger.log_agent_event(
        f"STATE_UPDATED: driver={driver_id}, income=Rp{total_income_today:,.0f}, "
        f"trips={trip_count_today}, zone={last_zone}",
        agent_name=AGENT_NAME,
    )

    return {
        "driver_id": driver_id,
        "state_date": date.today().isoformat(),
        "total_income_today": total_income_today,
        "trip_count_today": trip_count_today,
        "last_zone": last_zone,
        "operation": "STATE_MERGE",
        "anomaly_detected": anomaly_detected,
    }


# ============================================================
# TOOL 5: get_historical_medians (Target Hunter)
# Referensi: deep_dive.txt Layer 2
# ============================================================

def get_historical_medians(driver_id: str = "default_driver") -> Dict[str, Any]:
    """
    Tarik data historis dari BigQuery untuk kalkulasi Target Hunter.
    Jika BigQuery gagal atau data belum ada, fallback ke defaults dari deep_dive.txt.
    """
    # Defaults dari deep_dive.txt — dipakai jika BQ tidak tersedia
    DEFAULTS = {
        "avg_argo": 14500.0,
        "avg_argo_method": "median_trimmed_default",
        "komisi_rate": 0.11,
        "komisi_per_trip": 1595.0,
        "cancel_rate": 0.08,
        "avg_trips_per_day": 7.0,
        "avg_cycle_time": 35,
    }

    try:
        from google.cloud import bigquery

        sql = f"""
            SELECT
                (SELECT approx_quantiles(amount, 100)[OFFSET(50)]
                 FROM `{DS}.trx_daily_income`
                 WHERE driver_id = @driver_id AND status = 'recorded'
                   AND amount BETWEEN 5000 AND 200000) AS median_argo,

                COUNTIF(status = 'cancelled' OR status = 'dibatalkan')
                    / NULLIF(COUNT(*), 0) AS cancel_rate,

                COUNT(*) / NULLIF(COUNT(DISTINCT DATE(transaction_date)), 0)
                    AS avg_trips_per_day

            FROM `{DS}.trx_daily_income`
            WHERE driver_id = @driver_id
        """

        # ════ L3 GATE ════
        cleaned = verify_and_clean_query(sql, operation_context="get_historical_medians:SELECT")

        rows = _execute_validated_query(
            cleaned,
            params=[bigquery.ScalarQueryParameter("driver_id", "STRING", driver_id)],
            operation_context="get_historical_medians:SELECT",
        )

        row = rows[0] if rows else {}

        median_argo = float(row.get("median_argo") or DEFAULTS["avg_argo"])
        cancel_rate = float(row.get("cancel_rate") or DEFAULTS["cancel_rate"])
        avg_trips_per_day = float(row.get("avg_trips_per_day") or DEFAULTS["avg_trips_per_day"])
        komisi_rate = 0.11
        komisi_per_trip = median_argo * komisi_rate

        source = "bigquery"

    except Exception as e:
        # Fallback ke defaults — fitur tetap jalan meski BQ tidak tersedia
        logger.warning(
            f"[The Auditor] BigQuery tidak tersedia untuk get_historical_medians: {e}. "
            f"Menggunakan defaults dari deep_dive.txt."
        )
        median_argo = DEFAULTS["avg_argo"]
        cancel_rate = DEFAULTS["cancel_rate"]
        avg_trips_per_day = DEFAULTS["avg_trips_per_day"]
        komisi_rate = DEFAULTS["komisi_rate"]
        komisi_per_trip = DEFAULTS["komisi_per_trip"]
        source = "default_deep_dive"

    logger.info(
        f"[The Auditor] Historical medians ({source}): "
        f"median_argo=Rp{median_argo:,.0f}, cancel_rate={cancel_rate*100:.1f}%, "
        f"avg_trips={avg_trips_per_day:.1f}"
    )

    return {
        "avg_argo": median_argo,
        "avg_argo_method": "median_trimmed",
        "komisi_rate": komisi_rate,
        "komisi_per_trip": komisi_per_trip,
        "cancel_rate": cancel_rate,
        "avg_trips_per_day": avg_trips_per_day,
        "avg_cycle_time": 35,
        "data_source": source,
    }


