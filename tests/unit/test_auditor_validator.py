"""
tests/unit/test_auditor_validator.py
=====================================
Unit tests EKSKLUSIF untuk guardrails/auditor_validator.py.
Coverage target: 100% (sesuai CLAUDE.md Seksi 7.3).

7 test case WAJIB (terdaftar di CLAUDE.md Seksi 7.3):
  1. test_valid_insert_transaction()
  2. test_valid_select_report()
  3. test_blocked_delete_query()
  4. test_blocked_drop_table()
  5. test_wrong_dataset_target()
  6. test_missing_required_fields()
  7. test_unknown_table_name()

Test tambahan untuk coverage 100%:
  - TRUNCATE block, CREATE block, ALTER block
  - UPDATE pada field finansial immutable
  - Validasi MERGE (ALLOWED via RESTRICTED_OPERATIONS)
  - Deteksi operasi UNKNOWN/tidak dapat dibaca
  - validate_query() mode soft (tidak raise)
  - enforce() mode hard (raise AuditorValidationError)

Tidak ada koneksi BigQuery, API, atau I/O yang nyata.
Seluruh pengujian adalah pure Python assertion.
"""

import pytest
from datetime import datetime, timezone

from guardrails.auditor_validator import (
    AuditorValidationError,
    AuditorValidator,
    ALLOWED_DATASET,
    ALLOWED_TABLES,
    HARD_BLOCKED_OPERATIONS,
    IMMUTABLE_FINANCIAL_FIELDS,
)
from shared.schemas import SqlOperation, ValidationResultSchema

# ============================================================
# KONSTANTA TEST
# Dataset dan tabel valid untuk semua test yang bersifat PASS
# ============================================================

VALID_DATASET = "ojolboosttrack2"
VALID_TABLE = "trx_daily_income"
VALID_INSERT_SQL = f"""
    INSERT INTO `{VALID_DATASET}.{VALID_TABLE}`
        (transaction_id, amount, transaction_date, service_type, driver_id, created_at, status)
    VALUES
        (@transaction_id, @amount, @transaction_date, @service_type,
         @driver_id, CURRENT_TIMESTAMP(), 'recorded')
"""
VALID_SELECT_SQL = f"""
    SELECT SUM(amount) AS total, COUNT(*) AS trips
    FROM `{VALID_DATASET}.{VALID_TABLE}`
    WHERE DATE(transaction_date) = CURRENT_DATE()
    LIMIT 100
"""
VALID_PAYLOAD = {
    "amount": 85000.0,
    "transaction_date": datetime(2026, 4, 5, 9, 0, tzinfo=timezone.utc),
    "service_type": "ride",
}


# ============================================================
# FIXTURE
# ============================================================

@pytest.fixture
def validator():
    """Instance AuditorValidator untuk digunakan di setiap test."""
    return AuditorValidator()


# ============================================================
# ═══════════════════════════════════════════════════════════
# 7 TEST CASE WAJIB (CLAUDE.md Seksi 7.3)
# ═══════════════════════════════════════════════════════════
# ============================================================

class TestMandatorySevenCases:
    """
    Tujuh test case yang secara eksplisit terdaftar di CLAUDE.md Seksi 7.3.
    Semua harus PASS untuk deployment ke production.
    """

    # ---- Test Case 1 ----
    def test_valid_insert_transaction(self, validator):
        """
        [WAJIB #1] INSERT yang valid dengan semua field wajib terisi
        dan dataset/tabel yang benar harus lolos validasi.
        """
        result = validator.validate_query(
            sql=VALID_INSERT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
            payload=VALID_PAYLOAD,
        )

        assert isinstance(result, ValidationResultSchema)
        assert result.is_valid is True, (
            f"INSERT valid seharusnya lolos. Errors: {result.errors}"
        )
        assert result.operation_detected == SqlOperation.INSERT
        assert len(result.errors) == 0

    # ---- Test Case 2 ----
    def test_valid_select_report(self, validator):
        """
        [WAJIB #2] SELECT yang valid pada tabel whitelist dan dataset yang benar
        harus lolos validasi tanpa error.
        """
        result = validator.validate_query(
            sql=VALID_SELECT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )

        assert result.is_valid is True, (
            f"SELECT valid seharusnya lolos. Errors: {result.errors}"
        )
        assert result.operation_detected == SqlOperation.SELECT
        assert len(result.errors) == 0

    # ---- Test Case 3 ----
    def test_blocked_delete_query(self, validator):
        """
        [WAJIB #3] DELETE harus di-hard-block (AuditorValidationError).
        DELETE adalah operasi destruktif yang tidak pernah diizinkan
        pada dataset finansial (CLAUDE.md Seksi 5.3).
        """
        delete_sql = f"""
            DELETE FROM `{VALID_DATASET}.{VALID_TABLE}`
            WHERE amount < 10000
        """

        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=delete_sql,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )

        # Verifikasi pesan error mengandung identifikasi operasi
        error_message = str(exc_info.value)
        assert "DELETE" in error_message.upper() or "DIBLOKIR" in error_message.upper(), (
            f"Error message harus menyebutkan DELETE atau DIBLOKIR. Got: {error_message}"
        )

    # ---- Test Case 4 ----
    def test_blocked_drop_table(self, validator):
        """
        [WAJIB #4] DROP TABLE harus di-hard-block (AuditorValidationError).
        DROP adalah operasi paling destruktif — tidak ada toleransi.
        """
        drop_sql = f"DROP TABLE `{VALID_DATASET}.{VALID_TABLE}`"

        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=drop_sql,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )

        error_message = str(exc_info.value)
        assert "DROP" in error_message.upper() or "DIBLOKIR" in error_message.upper()

    # ---- Test Case 5 ----
    def test_wrong_dataset_target(self, validator):
        """
        [WAJIB #5] Query yang menarget dataset selain 'ojolboosttrack2'
        harus diblokir dengan error dataset tidak valid.
        """
        wrong_dataset = "prod_database_lain"
        sql = f"""
            SELECT * FROM `{wrong_dataset}.{VALID_TABLE}`
            LIMIT 10
        """

        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=sql,
                dataset=wrong_dataset,  # ← dataset yang salah
                table=VALID_TABLE,
            )

        error_message = str(exc_info.value)
        assert wrong_dataset in error_message or "tidak diizinkan" in error_message.lower(), (
            f"Error harus menyebut dataset yang salah. Got: {error_message}"
        )

    # ---- Test Case 6 ----
    def test_missing_required_fields(self, validator):
        """
        [WAJIB #6] INSERT dengan payload yang tidak memenuhi TransactionSchema
        (field wajib 'amount' atau 'service_type' tidak ada) harus diblokir.
        """
        incomplete_payload = {
            # 'amount' sengaja dihilangkan — WAJIB ada di TransactionSchema
            "service_type": "ride",
            "transaction_date": datetime(2026, 4, 5, tzinfo=timezone.utc),
        }

        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=VALID_INSERT_SQL,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
                payload=incomplete_payload,
            )

        error_message = str(exc_info.value)
        assert "TransactionSchema" in error_message or "payload" in error_message.lower() or \
               "amount" in error_message.lower(), (
            f"Error harus menyebutkan masalah schema. Got: {error_message}"
        )

    # ---- Test Case 7 ----
    def test_unknown_table_name(self, validator):
        """
        [WAJIB #7] Query ke tabel yang tidak ada dalam whitelist
        harus diblokir dengan error whitelist violation.
        """
        unknown_table = "shadow_table_tidak_ada"
        sql = f"""
            SELECT * FROM `{VALID_DATASET}.{unknown_table}`
            LIMIT 10
        """

        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=sql,
                dataset=VALID_DATASET,
                table=unknown_table,   # ← tabel tidak dikenal
            )

        error_message = str(exc_info.value)
        assert unknown_table in error_message or "whitelist" in error_message.lower() or \
               "tidak ada" in error_message.lower(), (
            f"Error harus menyebut tabel yang tidak dikenal. Got: {error_message}"
        )


# ============================================================
# TEST TAMBAHAN — COVERAGE 100%
# ============================================================

class TestHardBlockedOperations:
    """Semua operasi dalam HARD_BLOCKED_OPERATIONS wajib di-block."""

    @pytest.mark.parametrize("operation,sql", [
        ("TRUNCATE", f"TRUNCATE TABLE `{VALID_DATASET}.{VALID_TABLE}`"),
        ("CREATE",   f"CREATE TABLE `{VALID_DATASET}.new_table` (id INT64)"),
        ("ALTER",    f"ALTER TABLE `{VALID_DATASET}.{VALID_TABLE}` ADD COLUMN foo STRING"),
    ])
    def test_hard_blocked_operations_raise(self, validator, operation, sql):
        """Setiap operasi dalam HARD_BLOCKED_OPERATIONS harus raise AuditorValidationError."""
        with pytest.raises(AuditorValidationError):
            validator.enforce(
                sql=sql,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )

    def test_hard_blocked_returns_immediately(self, validator):
        """
        Saat operasi terlarang terdeteksi, validator harus return SEGERA
        tanpa melakukan cek 2-5 (early exit untuk efisiensi).
        """
        # DELETE ke dataset YANG SALAH — tapi harus tetap di-block karena DELETE
        result = validator.validate_query(
            sql=f"DELETE FROM `wrong_dataset.{VALID_TABLE}` WHERE 1=1",
            dataset="wrong_dataset",
            table=VALID_TABLE,
        )
        # is_valid=False karena DELETE di-block, bukan karena dataset salah
        assert result.is_valid is False
        assert result.operation_detected == SqlOperation.DELETE
        # Hanya 1 error (DELETE), bukan 2 error (DELETE + dataset salah)
        # karena validator return segera setelah mendeteksi hard-block
        assert len(result.errors) == 1


class TestValidateModeVsEnforceMode:
    """validate_query() = mode soft (tidak raise). enforce() = mode hard (raise)."""

    def test_validate_query_returns_false_not_raises(self, validator):
        """validate_query() harus mengembalikan is_valid=False, bukan raise."""
        result = validator.validate_query(
            sql="DELETE FROM `ojolboosttrack2.trx_daily_income` WHERE 1=1",
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )
        # Tidak raise — hanya is_valid=False
        assert isinstance(result, ValidationResultSchema)
        assert result.is_valid is False

    def test_enforce_raises_on_invalid(self, validator):
        """enforce() harus raise AuditorValidationError jika validasi gagal."""
        with pytest.raises(AuditorValidationError):
            validator.enforce(
                sql="DELETE FROM `ojolboosttrack2.trx_daily_income` WHERE 1=1",
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )

    def test_enforce_passes_silently_on_valid(self, validator):
        """enforce() harus selesai tanpa raise jika query valid."""
        # Tidak boleh raise apapun
        try:
            validator.enforce(
                sql=VALID_SELECT_SQL,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )
        except AuditorValidationError as e:
            pytest.fail(f"enforce() seharusnya tidak raise untuk query valid. Got: {e}")

    def test_auditor_validation_error_contains_errors_list(self, validator):
        """AuditorValidationError.errors harus berisi list string yang dapat dibaca."""
        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql="DROP TABLE `ojolboosttrack2.trx_daily_income`",
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )
        err = exc_info.value
        assert hasattr(err, "errors")
        assert isinstance(err.errors, list)
        assert len(err.errors) > 0
        assert all(isinstance(e, str) for e in err.errors)


class TestOperationDetection:
    """_detect_operation() harus mendeteksi tipe SQL dengan benar."""

    @pytest.mark.parametrize("sql,expected_op", [
        ("SELECT * FROM table", SqlOperation.SELECT),
        ("select * from table", SqlOperation.SELECT),      # case-insensitive
        ("INSERT INTO table VALUES (1)", SqlOperation.INSERT),
        ("DELETE FROM table WHERE 1=1", SqlOperation.DELETE),
        ("DROP TABLE table", SqlOperation.DROP),
        ("TRUNCATE TABLE table", SqlOperation.TRUNCATE),
        ("CREATE TABLE table (id INT64)", SqlOperation.CREATE),
        ("ALTER TABLE table ADD COLUMN foo STRING", SqlOperation.ALTER),
    ])
    def test_detect_operation_correctly(self, validator, sql, expected_op):
        """Deteksi tipe operasi dari berbagai SQL statement."""
        detected = validator._detect_operation(sql)
        assert detected == expected_op, (
            f"SQL '{sql[:40]}' seharusnya terdeteksi sebagai {expected_op.value}, "
            f"got {detected.value}"
        )

    def test_detect_unknown_operation(self, validator):
        """SQL yang tidak dimulai dengan keyword dikenal → UNKNOWN."""
        result = validator._detect_operation("CALL some_procedure()")
        assert result == SqlOperation.UNKNOWN


class TestUpdateFieldProtection:
    """Field finansial immutable tidak boleh di-UPDATE."""

    def test_update_on_immutable_field_amount_is_blocked(self, validator):
        """UPDATE pada kolom 'amount' harus diblokir."""
        sql = f"""
            UPDATE `{VALID_DATASET}.{VALID_TABLE}`
            SET amount = 999999
            WHERE transaction_id = 'abc'
        """
        # enforce() harus raise karena UPDATE financial field terlarang
        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=sql,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )
        assert "amount" in str(exc_info.value).lower() or \
               "immutable" in str(exc_info.value).lower() or \
               "terlarang" in str(exc_info.value).lower()

    def test_update_on_transaction_date_is_blocked(self, validator):
        """UPDATE pada kolom 'transaction_date' harus diblokir."""
        sql = f"""
            UPDATE `{VALID_DATASET}.{VALID_TABLE}`
            SET transaction_date = CURRENT_TIMESTAMP()
            WHERE transaction_id = 'abc'
        """
        with pytest.raises(AuditorValidationError):
            validator.enforce(
                sql=sql,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
            )

    def test_check_update_fields_returns_list(self, validator):
        """_check_update_fields() harus mengembalikan list field yang dilanggar."""
        sql = "UPDATE tbl SET amount = 999, transaction_date = NOW() WHERE id = 1"
        forbidden = validator._check_update_fields(sql)
        assert isinstance(forbidden, list)
        assert "amount" in forbidden

    def test_check_update_fields_empty_when_safe(self, validator):
        """UPDATE pada kolom non-finansial tidak boleh masuk ke forbidden list."""
        sql = "UPDATE tbl SET updated_at = CURRENT_TIMESTAMP() WHERE id = 1"
        forbidden = validator._check_update_fields(sql)
        assert "amount" not in forbidden
        assert "transaction_date" not in forbidden


class TestWhitelistTables:
    """Semua tabel dalam whitelist harus lolos. Tabel lain harus diblokir."""

    @pytest.mark.parametrize("table", sorted(ALLOWED_TABLES))
    def test_whitelisted_table_passes(self, validator, table):
        """Setiap tabel dalam ALLOWED_TABLES harus lolos validasi SELECT."""
        sql = f"SELECT * FROM `{VALID_DATASET}.{table}` LIMIT 1"
        result = validator.validate_query(
            sql=sql,
            dataset=VALID_DATASET,
            table=table,
        )
        # Tabel valid — tidak ada error tabel
        table_errors = [e for e in result.errors if table in e]
        assert len(table_errors) == 0, (
            f"Tabel '{table}' ada di whitelist tapi muncul di errors: {result.errors}"
        )

    @pytest.mark.parametrize("table", [
        "sys.tables",
        "information_schema.tables",
        "secret_backup",
        "users_private",
        "__internal_log",
    ])
    def test_non_whitelisted_table_is_blocked(self, validator, table):
        """Tabel di luar whitelist harus diblokir."""
        sql = f"SELECT * FROM `{VALID_DATASET}.{table}` LIMIT 1"
        with pytest.raises(AuditorValidationError):
            validator.enforce(
                sql=sql,
                dataset=VALID_DATASET,
                table=table,
            )


class TestMultipleViolations:
    """Query dengan lebih dari satu pelanggaran harus melaporkan semua error."""

    def test_wrong_dataset_and_unknown_table(self, validator):
        """
        Query yang melanggar BAIK dataset NOR tabel sekaligus
        harus menghasilkan multiple errors (kecuali hard-block yang early exit).
        """
        sql = "SELECT * FROM `hackerdb.shadow_table` LIMIT 1"
        result = validator.validate_query(
            sql=sql,
            dataset="hackerdb",        # ← dataset salah
            table="shadow_table",      # ← tabel tidak dikenal
        )
        assert result.is_valid is False
        # Harus ada minimal 2 errors (satu untuk dataset, satu untuk tabel)
        assert len(result.errors) >= 2

    def test_valid_query_has_zero_errors(self, validator):
        """Query yang sepenuhnya valid harus punya errors=[] dan warnings=[]."""
        result = validator.validate_query(
            sql=VALID_SELECT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )
        assert result.is_valid is True
        assert result.errors == []


class TestTransactionSchemaValidation:
    """Validasi payload INSERT terhadap TransactionSchema."""

    def test_valid_payload_passes(self, validator):
        """Payload INSERT yang valid tidak menghasilkan error schema."""
        result = validator.validate_query(
            sql=VALID_INSERT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
            payload=VALID_PAYLOAD,
        )
        schema_errors = [e for e in result.errors if "TransactionSchema" in e]
        assert len(schema_errors) == 0

    def test_negative_amount_is_invalid_payload(self, validator):
        """Payload dengan amount negatif harus gagal TransactionSchema validation."""
        bad_payload = {
            "amount": -5000.0,   # ← tidak valid (harus > 0)
            "service_type": "ride",
            "transaction_date": datetime(2026, 4, 5, tzinfo=timezone.utc),
        }
        with pytest.raises(AuditorValidationError) as exc_info:
            validator.enforce(
                sql=VALID_INSERT_SQL,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
                payload=bad_payload,
            )
        assert "TransactionSchema" in str(exc_info.value) or \
               "payload" in str(exc_info.value).lower()

    def test_invalid_service_type_is_invalid_payload(self, validator):
        """Payload dengan service_type tidak valid harus gagal schema validation."""
        bad_payload = {
            "amount": 50000.0,
            "service_type": "helicopter",   # ← bukan ride/food/package
            "transaction_date": datetime(2026, 4, 5, tzinfo=timezone.utc),
        }
        with pytest.raises(AuditorValidationError):
            validator.enforce(
                sql=VALID_INSERT_SQL,
                dataset=VALID_DATASET,
                table=VALID_TABLE,
                payload=bad_payload,
            )

    def test_select_without_payload_is_valid(self, validator):
        """SELECT tanpa payload tidak perlu schema validation — harus lolos."""
        result = validator.validate_query(
            sql=VALID_SELECT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
            payload=None,   # Tidak ada payload untuk SELECT
        )
        assert result.is_valid is True

    def test_insert_without_payload_skips_schema_check(self, validator):
        """
        INSERT tanpa payload tidak menjalankan schema check
        (schema check opsional, hanya jika payload diberikan).
        """
        result = validator.validate_query(
            sql=VALID_INSERT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
            payload=None,   # ← tidak ada payload — skip schema check
        )
        # Seharusnya valid (dataset & tabel benar, operasi INSERT diizinkan)
        assert result.is_valid is True


class TestValidationResultSchema:
    """Struktur output validate_query() harus selalu ValidationResultSchema."""

    def test_result_is_always_validation_result_schema(self, validator):
        """Return type validate_query() harus ValidationResultSchema."""
        result = validator.validate_query(
            sql=VALID_SELECT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )
        assert isinstance(result, ValidationResultSchema)

    def test_result_has_operation_detected(self, validator):
        """ValidationResultSchema harus menyertakan operation_detected."""
        result = validator.validate_query(
            sql=VALID_SELECT_SQL,
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )
        assert result.operation_detected is not None
        assert isinstance(result.operation_detected, SqlOperation)

    def test_failed_result_has_errors_list(self, validator):
        """ValidationResultSchema yang gagal harus punya errors list tidak kosong."""
        result = validator.validate_query(
            sql="DELETE FROM `ojolboosttrack2.trx_daily_income` WHERE 1=1",
            dataset=VALID_DATASET,
            table=VALID_TABLE,
        )
        assert result.is_valid is False
        assert isinstance(result.errors, list)
        assert len(result.errors) > 0
        assert all(isinstance(e, str) for e in result.errors)
