"""
agents/the_auditor/validator.py
==================================
Layer 4 ↔ Layer 3 Integration Bridge: The Auditor.

File ini adalah titik integrasi WAJIB antara Layer 4 (The Auditor)
dan Layer 3 (Guardrails). Semua query BigQuery dari tools.py
HARUS melewati verify_and_clean_query() sebelum dieksekusi.

Referensi:
  - guardrails/auditor_validator.py  (L3 deterministic hard-block)
  - skills/the_auditor/sql_write_rules.md
  - skills/the_auditor/transaction_schema.md

ATURAN (CLAUDE.md Seksi 5.1 & 6.5):
Tidak ada jalan memutar (bypass) untuk validator ini.
Pelanggaran → PermissionError (hard block).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from guardrails.auditor_validator import AuditorValidator
from shared.logger import get_logger
from shared.schemas import ValidationResultSchema

logger = get_logger("the_auditor.validator")

# ============================================================
# SINGLETON VALIDATOR
# AuditorValidator adalah stateless — aman di-share
# ============================================================

_auditor_validator = AuditorValidator()


# ============================================================
# CLEANED QUERY CONTAINER
# ============================================================

@dataclass
class CleanedQuery:
    """
    Hasil query yang sudah divalidasi dan dibersihkan oleh L3 Guardrail.
    Hanya query dengan status validated=True yang boleh dieksekusi BQ client.

    Attributes:
        original_sql   : Query SQL asli sebelum validasi.
        validated_sql  : Query SQL yang aman untuk dieksekusi (identik jika valid).
        validated       : True jika semua 5 validasi L3 lolos.
        violation_codes : Daftar kode pelanggaran jika ada.
        violation_msgs  : Pesan detail setiap pelanggaran.
    """
    original_sql: str
    validated_sql: str
    validated: bool
    violation_codes: List[str]
    violation_msgs: List[str]


# ============================================================
# VERIFY AND CLEAN QUERY — Fungsi Utama
# ============================================================

def verify_and_clean_query(
    sql: str,
    operation_context: Optional[str] = None,
) -> CleanedQuery:
    """
    Validasi query SQL melalui AuditorValidator (L3 Guardrails) sebelum
    diizinkan dieksekusi oleh BigQuery client.

    Menjalankan 5 pengecekan deterministik dari guardrails/auditor_validator.py:
      Check 1: Operasi terlarang (DELETE, DROP, TRUNCATE, ALTER, CREATE)
      Check 2: Whitelist dataset (hanya 'ojolboosttrack2')
      Check 3: Whitelist tabel (trx_daily_income, driver_state, zone_demand_history)
      Check 4: Validasi field wajib pada INSERT (transaction_id, amount, transaction_date)
      Check 5: SQL injection patterns (UNION, multi-statement, komentar berbahaya)

    Args:
        sql: SQL query yang akan divalidasi.
        operation_context: Nama operasi untuk logging (opsional, contoh: "record_transaction").

    Returns:
        CleanedQuery — berisi status validasi, SQL bersih, dan kode pelanggaran.

    Raises:
        PermissionError: Jika AuditorValidator mendeteksi pelanggaran keamanan.
                         HARD BLOCK — query TIDAK boleh dieksekusi.
        TypeError: Jika input SQL bukan string.
    """
    if not isinstance(sql, str):
        raise TypeError(f"Query SQL harus berupa string, bukan {type(sql).__name__}.")

    if not sql.strip():
        raise ValueError("Query SQL tidak boleh kosong.")

    ctx_label = f"[{operation_context}] " if operation_context else ""

    logger.info(
        f"[Auditor Validator] {ctx_label}Memvalidasi query: "
        f"{sql.strip()[:120]}{'...' if len(sql) > 120 else ''}"
    )

    # ---- Panggil L3: AuditorValidator.validate_query() ----
    import re as _re
    # Handle 3-part ref: `project.dataset.table`
    _three_part = _re.findall(r'`([^`]+)\.([^`]+)\.([^`]+)`', sql)
    if _three_part:
        _, _dataset, _table = _three_part[0]
    else:
        # Handle 2-part ref: `dataset.table`
        _two_part = _re.findall(r'`([^`]+)\.([^`]+)`', sql)
        if _two_part:
            _dataset, _table = _two_part[0]
        else:
            _plain = _re.search(r'\b(ojolboosttrack2)\.([a-z_]+)\b', sql)
            _dataset = _plain.group(1) if _plain else "ojolboosttrack2"
            _table = _plain.group(2) if _plain else "trx_daily_income"

    try:
        validation_result: ValidationResultSchema = AuditorValidator.validate_query(
            sql=sql,
            dataset=_dataset,
            table=_table,
        )
    except Exception as e:
        # Error tak terduga dari validator — fail-safe: blokir
        logger.error(
            f"[Auditor Validator] {ctx_label}AuditorValidator error tak terduga: {e}. "
            f"Query DIBLOKIR sebagai fail-safe."
        )
        raise PermissionError(
            f"[HARD BLOCK] AuditorValidator gagal dengan error: {e}. "
            f"Query tidak dieksekusi demi keamanan."
        ) from e

    # ---- Proses hasil validasi ----
    cleaned = CleanedQuery(
        original_sql=sql,
        validated_sql=sql.strip(),   # Trim saja — konten tidak diubah jika valid
        validated=validation_result.is_valid,
        violation_codes=getattr(validation_result, "violation_codes", []),
        violation_msgs=validation_result.errors if not validation_result.is_valid else [],
    )

    if not cleaned.validated:
        # Log detail setiap pelanggaran
        for code, msg in zip(cleaned.violation_codes, cleaned.violation_msgs):
            logger.log_guardrail_block(
                tool_name="bigquery_execute",
                agent_name="The Auditor",
                reason=f"[{code}] {msg}",
            )

        raise PermissionError(
            f"[HARD BLOCK] Query diblokir oleh AuditorValidator. "
            f"Pelanggaran: {'; '.join(cleaned.violation_msgs)}. "
            f"Kode: {', '.join(cleaned.violation_codes)}."
        )

    logger.info(
        f"[Auditor Validator] {ctx_label}✅ Validasi LOLOS — query aman untuk dieksekusi."
    )

    return cleaned


# ============================================================
# VALIDATE TRANSACTION AMOUNT
# Validasi tambahan spesifik transaksi keuangan
# ============================================================

def validate_transaction_amount(amount: float, context: str = "") -> None:
    """
    Validasi nominal transaksi sebelum INSERT.
    Referensi: skills/the_auditor/transaction_schema.md — Constraints.

    Args:
        amount: Nominal transaksi (float, IDR).
        context: Label operasi untuk logging.

    Raises:
        ValueError: Jika amount tidak valid.
    """
    if not isinstance(amount, (int, float)):
        raise ValueError(f"Amount harus berupa angka, bukan {type(amount).__name__}.")

    if amount <= 0:
        raise ValueError(
            f"Amount transaksi harus > 0. Diterima: {amount}. "
            f"Konteks: {context}"
        )

    if amount > 100_000_000:  # Rp 100 juta — threshold anomali
        logger.warning(
            f"[Auditor Validator] ANOMALI: Amount sangat besar: Rp {amount:,.0f}. "
            f"Konteks: {context}. Transaksi tetap dicatat tapi di-flag."
        )
        # Tidak blokir — hanya log WARNING (sesuai CLAUDE.md Seksi 6.5)


# ============================================================
# VALIDATE SERVICE TYPE
# ============================================================

def validate_service_type(service_type: str) -> str:
    """
    Validasi dan normalisasi tipe layanan.
    Referensi: skills/the_auditor/transaction_schema.md.

    Args:
        service_type: Tipe layanan dari pengguna.

    Returns:
        service_type yang sudah dinormalisasi.

    Raises:
        ValueError: Jika service_type tidak dikenal.
    """
    VALID_SERVICE_TYPES = {"ride", "food", "package"}
    normalized = service_type.lower().strip() if service_type else "ride"

    # Mapping alias yang umum digunakan pengguna
    ALIASES = {
        "ojek": "ride",
        "motor": "ride",
        "makanan": "food",
        "makan": "food",
        "paket": "package",
        "kirim": "package",
        "pengiriman": "package",
    }
    normalized = ALIASES.get(normalized, normalized)

    if normalized not in VALID_SERVICE_TYPES:
        raise ValueError(
            f"Service type '{service_type}' tidak dikenal. "
            f"Nilai valid: {VALID_SERVICE_TYPES}."
        )

    return normalized
