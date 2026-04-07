"""
guardrails/auditor_validator.py
===============================
Layer 3 — Hard Block BigQuery Validator untuk The Auditor.

ATURAN #2 (IMMUTABLE dari CLAUDE.md Seksi 5.1):
The Auditor TIDAK BOLEH mengeksekusi query BigQuery apapun tanpa
melalui validasi deterministik dari class ini terlebih dahulu.
Bypass adalah pelanggaran arsitektur kritis.

Validation Checklist (CLAUDE.md Seksi 5.2):
  1. Schema Check   — payload sesuai TransactionSchema?
  2. Operation Check — hanya INSERT/SELECT yang diizinkan?
  3. Dataset Check   — target dataset == 'ojolboosttrack2'?
  4. Table Whitelist — nama tabel ada di whitelist?
  5. Field Completeness — semua required fields terisi?
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from shared.schemas import (
    SqlOperation,
    TransactionSchema,
    ValidationResultSchema,
)
from shared.logger import get_logger

logger = get_logger("auditor_validator")

# ============================================================
# KONFIGURASI IMMUTABLE
# Nilai ini tidak boleh diubah tanpa persetujuan eksplisit owner.
# Sesuai CLAUDE.md Seksi 4.4 dan 5.3.
# ============================================================

ALLOWED_DATASET: str = os.getenv("BIGQUERY_DATASET", "ojolboosttrack2")

ALLOWED_TABLES: frozenset = frozenset({
    "trx_daily_income",
    "zone_demand_history",
    "demand_history",
    "schedule_reminders",
    "trx_monthly_summary",
    "driver_state",
})

ALLOWED_OPERATIONS: frozenset = frozenset({
    SqlOperation.SELECT,
    SqlOperation.INSERT,
})

RESTRICTED_OPERATIONS: frozenset = frozenset({
    SqlOperation.UPDATE,  # Diizinkan terbatas — hanya kolom updated_at
})

# Operasi yang langsung HARD BLOCK tanpa toleransi
HARD_BLOCKED_OPERATIONS: frozenset = frozenset({
    SqlOperation.DELETE,
    SqlOperation.DROP,
    SqlOperation.TRUNCATE,
    SqlOperation.CREATE,
    SqlOperation.ALTER,
})

# Kolom yang TIDAK BOLEH di-UPDATE (data finansial) — berlaku untuk RESTRICTED UPDATE
IMMUTABLE_FINANCIAL_FIELDS: frozenset = frozenset({
    "amount", "transaction_date", "service_type", "transaction_id", "created_at"
})


# ============================================================
# CUSTOM EXCEPTION
# ============================================================

class AuditorValidationError(Exception):
    """
    Raised oleh AuditorValidator.enforce() ketika validasi gagal.
    Berisi list error spesifik untuk logging dan response ke Bang Jek.
    """

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"[AUDITOR HARD BLOCK] Validasi gagal: {'; '.join(errors)}")


# ============================================================
# VALIDATOR CLASS
# ============================================================

class AuditorValidator:
    """
    Validator deterministik untuk semua operasi BigQuery The Auditor.

    Usage:
        # Mode soft (hanya validasi, tidak raise):
        result = AuditorValidator.validate_query(sql, dataset, table, payload)

        # Mode hard (raise jika tidak valid):
        AuditorValidator.enforce(sql, dataset, table, payload)
    """

    @staticmethod
    def _detect_operation(sql: str) -> SqlOperation:
        """Deteksi operasi SQL dari statement pertama."""
        sql_clean = sql.strip().upper()
        first_word = sql_clean.split()[0] if sql_clean else ""

        for op in SqlOperation:
            if op.value == "UNKNOWN":
                continue
            # Match kata pertama atau "CREATE TABLE", "ALTER TABLE"
            if first_word == op.value:
                return op
            # Handle multi-word: "CREATE TABLE", "ALTER TABLE"
            if sql_clean.startswith(f"{op.value} TABLE") or sql_clean.startswith(f"{op.value} "):
                if op.value in {"CREATE", "ALTER"}:
                    return op

        return SqlOperation.UNKNOWN

    @staticmethod
    def _check_update_fields(sql: str) -> list[str]:
        """
        Untuk operasi UPDATE, periksa apakah ada kolom finansial yang dimodifikasi.
        Mengembalikan list field terlarang yang ditemukan.
        """
        sql_upper = sql.upper()
        found_immutable = []
        for field in IMMUTABLE_FINANCIAL_FIELDS:
            # Pattern: SET field_name = atau field_name=
            pattern = rf'\bSET\b.*\b{field.upper()}\s*='
            if re.search(pattern, sql_upper, re.DOTALL):
                found_immutable.append(field)
        return found_immutable

    @classmethod
    def validate_query(
        cls,
        sql: str,
        dataset: str,
        table: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> ValidationResultSchema:
        """
        Validasi query BigQuery. Mode soft — tidak raise exception.

        Args:
            sql: Query SQL yang akan dieksekusi.
            dataset: Nama BigQuery dataset target.
            table: Nama tabel target.
            payload: Dict data untuk INSERT (opsional, untuk TransactionSchema check).

        Returns:
            ValidationResultSchema dengan is_valid=True/False dan daftar error/warning.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # --- Deteksi operasi ---
        operation = cls._detect_operation(sql)
        logger.info(
            f"[AUDITOR VALIDATOR] Memeriksa: {operation.value} ON {dataset}.{table}"
        )

        # ----------------------------------------
        # CHECK 1: Hard-blocked operations
        # ----------------------------------------
        if operation in HARD_BLOCKED_OPERATIONS:
            msg = (
                f"OPERASI KRITIS DIBLOKIR: '{operation.value}' tidak pernah diizinkan "
                f"pada dataset finansial '{ALLOWED_DATASET}'. "
                f"Ini adalah pelanggaran arsitektur kritis (CLAUDE.md Seksi 5.3)."
            )
            errors.append(msg)
            logger.log_guardrail_block(
                tool_name="bigquery",
                agent_name="The Auditor",
                reason=f"Hard-blocked SQL operation: {operation.value}",
            )
            # Return immediately — tidak perlu lanjut validasi
            return ValidationResultSchema(
                is_valid=False,
                errors=errors,
                warnings=warnings,
                operation_detected=operation,
            )

        # ----------------------------------------
        # CHECK 2: Dataset validation
        # ----------------------------------------
        if dataset != ALLOWED_DATASET:
            errors.append(
                f"Dataset '{dataset}' tidak diizinkan. "
                f"Hanya '{ALLOWED_DATASET}' yang valid (CLAUDE.md Seksi 4.4)."
            )

        # ----------------------------------------
        # CHECK 3: Table whitelist
        # ----------------------------------------
        if table not in ALLOWED_TABLES:
            errors.append(
                f"Tabel '{table}' tidak ada dalam whitelist yang diizinkan. "
                f"Tabel valid: {sorted(ALLOWED_TABLES)}."
            )

        # ----------------------------------------
        # CHECK 4: Operation allowlist
        # ----------------------------------------
        if operation not in ALLOWED_OPERATIONS and operation not in RESTRICTED_OPERATIONS:
            if operation != SqlOperation.UNKNOWN:
                errors.append(
                    f"Operasi '{operation.value}' tidak diizinkan untuk The Auditor. "
                    f"Hanya {[op.value for op in ALLOWED_OPERATIONS]} yang diizinkan."
                )
            else:
                warnings.append(
                    "Tipe operasi SQL tidak dapat terdeteksi. Periksa format query."
                )

        # ----------------------------------------
        # CHECK 4b: Restricted UPDATE — cek field finansial
        # ----------------------------------------
        if operation == SqlOperation.UPDATE:
            warnings.append(
                "UPDATE diizinkan terbatas. "
                "Hanya kolom 'updated_at' yang boleh dimodifikasi (CLAUDE.md Seksi 5.3)."
            )
            forbidden_fields = cls._check_update_fields(sql)
            if forbidden_fields:
                errors.append(
                    f"UPDATE pada field finansial terlarang: {forbidden_fields}. "
                    f"Field ini immutable setelah INSERT."
                )

        # ----------------------------------------
        # CHECK 5: Schema / Field completeness (khusus INSERT)
        # ----------------------------------------
        if operation == SqlOperation.INSERT and payload is not None:
            try:
                TransactionSchema(**payload)
            except Exception as e:
                errors.append(
                    f"Payload INSERT tidak memenuhi TransactionSchema: {e}. "
                    f"Lihat skills/the_auditor/transaction_schema.md."
                )

        # ----------------------------------------
        # Log & Return
        # ----------------------------------------
        is_valid = len(errors) == 0
        if is_valid:
            logger.info(
                f"[AUDITOR VALIDATOR] ✅ PASSED: {operation.value} ON {dataset}.{table}"
            )
        else:
            for err in errors:
                logger.error(f"[AUDITOR VALIDATOR] ❌ {err}")

        for warn in warnings:
            logger.warning(f"[AUDITOR VALIDATOR] ⚠️ {warn}")

        return ValidationResultSchema(
            is_valid=is_valid,
            errors=errors,
            warnings=warnings,
            operation_detected=operation,
        )

    @classmethod
    def enforce(
        cls,
        sql: str,
        dataset: str,
        table: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Hard block mode — raise AuditorValidationError jika validasi gagal.

        Wajib dipanggil di agents/the_auditor/tools.py SEBELUM
        mengirim query apapun ke BigQuery.

        Args:
            sql: Query SQL yang akan dieksekusi.
            dataset: Nama BigQuery dataset target.
            table: Nama tabel target.
            payload: Dict data untuk INSERT (opsional).

        Raises:
            AuditorValidationError: Jika salah satu dari 5 cek validasi gagal.
        """
        result = cls.validate_query(sql, dataset, table, payload)

        if not result.is_valid:
            raise AuditorValidationError(result.errors)
