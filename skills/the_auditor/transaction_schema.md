# Skill: Transaction Schema
# Agent: The Auditor
# Trigger: SELALU — wajib dibaca sebelum operasi BigQuery apapun. Dipanggil sebagai referensi schema.
# Description: Definisi lengkap skema tabel BigQuery untuk dataset ojolboosttrack2. Source of truth untuk AuditorValidator.
---

## Dataset: `ojolboosttrack2`

> ⚠️ **NAMA DATASET INI IMMUTABLE.** Jangan pernah hardcode nama dataset yang berbeda.

---

## Tabel 1: `trx_daily_income` (Pencatatan Transaksi Harian)

**Tujuan**: Menyimpan setiap transaksi pendapatan pengemudi.

| Kolom | Tipe BigQuery | Nullable | Deskripsi |
|---|---|---|---|
| `transaction_id` | STRING | NOT NULL | UUID unik (auto-generated) |
| `amount` | FLOAT64 | NOT NULL | Pendapatan dalam Rupiah (> 0, maks 10 juta) |
| `transaction_date` | TIMESTAMP | NOT NULL | Waktu transaksi (UTC) |
| `service_type` | STRING | NOT NULL | Enum: `ride`, `food`, `package` |
| `zone` | STRING | NULLABLE | Area/zona pickup (maks 100 karakter) |
| `notes` | STRING | NULLABLE | Catatan tambahan (maks 500 karakter) |
| `driver_id` | STRING | NULLABLE | ID pengemudi (maks 50 karakter) |
| `created_at` | TIMESTAMP | NOT NULL | Waktu record dibuat (auto, UTC) |
| `updated_at` | TIMESTAMP | NULLABLE | Waktu terakhir update (hanya kolom ini yang boleh di-UPDATE) |
| `status` | STRING | NOT NULL | Default: `recorded` |

**DDL Reference**:
```sql
CREATE TABLE IF NOT EXISTS `ojolboosttrack2.trx_daily_income` (
    transaction_id  STRING      NOT NULL,
    amount          FLOAT64     NOT NULL,
    transaction_date TIMESTAMP  NOT NULL,
    service_type    STRING      NOT NULL,
    zone            STRING,
    notes           STRING,
    driver_id       STRING,
    created_at      TIMESTAMP   NOT NULL,
    updated_at      TIMESTAMP,
    status          STRING      NOT NULL DEFAULT 'recorded'
)
OPTIONS (
    description = 'Pencatatan transaksi pendapatan harian pengemudi OjolBoost'
);
```

---

## Tabel 2: `zone_demand_history` (Data Permintaan Zona — READ ONLY untuk The Auditor)

**Tujuan**: Sumber data historis untuk Demand Analytics. The Auditor hanya boleh SELECT.

| Kolom | Tipe BigQuery | Nullable | Deskripsi |
|---|---|---|---|
| `record_id` | STRING | NOT NULL | UUID unik |
| `zone_name` | STRING | NOT NULL | Nama zona/area |
| `pickup_timestamp` | TIMESTAMP | NOT NULL | Waktu pickup terjadi |
| `trip_duration_minutes` | FLOAT64 | NULLABLE | Durasi trip |
| `fare_amount` | FLOAT64 | NULLABLE | Ongkos trip (Rp) |
| `service_type` | STRING | NOT NULL | Enum: `ride`, `food`, `package` |

---

## Tabel 3: `driver_state` (Status Aktivitas Pengemudi)

**Tujuan**: Menyimpan status harian pengemudi (diupdate oleh The Auditor).

| Kolom | Tipe BigQuery | Nullable | Deskripsi |
|---|---|---|---|
| `driver_id` | STRING | NOT NULL | ID pengemudi |
| `state_date` | DATE | NOT NULL | Tanggal status |
| `total_income_today` | FLOAT64 | NOT NULL | Total pendapatan hari ini (Rp) |
| `trip_count_today` | INT64 | NOT NULL | Jumlah trip hari ini |
| `active_hours` | FLOAT64 | NULLABLE | Jam aktif hari ini |
| `last_zone` | STRING | NULLABLE | Zona terakhir |
| `updated_at` | TIMESTAMP | NOT NULL | Waktu update terakhir |

---

## Aturan Operasi Per Tabel

| Tabel | INSERT | SELECT | UPDATE | DELETE |
|---|---|---|---|---|
| `trx_daily_income` | ✅ | ✅ | ⚠️ (hanya `updated_at`) | ❌ |
| `zone_demand_history` | ❌ | ✅ | ❌ | ❌ |
| `driver_state` | ✅ | ✅ | ⚠️ (hanya `updated_at`, `total_income_today`, `trip_count_today`) | ❌ |
| `trx_monthly_summary` | ❌ | ✅ | ❌ | ❌ |
| `schedule_reminders` | ✅ | ✅ | ✅ | ❌ |

---

## Contoh INSERT yang Valid

```sql
INSERT INTO `ojolboosttrack2.trx_daily_income`
    (transaction_id, amount, transaction_date, service_type, zone, notes, driver_id, created_at, status)
VALUES
    (@transaction_id, @amount, @transaction_date, @service_type, @zone, @notes, @driver_id, CURRENT_TIMESTAMP(), 'recorded');
```

## Contoh INSERT yang INVALID (akan diblokir AuditorValidator)

```sql
-- ❌ INVALID: amount = 0
INSERT INTO `ojolboosttrack2.trx_daily_income` (amount, ...) VALUES (0, ...);

-- ❌ INVALID: dataset salah
INSERT INTO `wrong_dataset.trx_daily_income` ...;

-- ❌ INVALID: operasi DELETE
DELETE FROM `ojolboosttrack2.trx_daily_income` WHERE ...;
```
