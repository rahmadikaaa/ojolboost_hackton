# Skill: SQL Write Rules
# Agent: The Auditor
# Trigger: SELALU sebelum mengeksekusi query INSERT atau UPDATE ke BigQuery. Wajib dibaca bersama transaction_schema.md.
# Description: Aturan teknis penulisan SQL yang diizinkan untuk The Auditor pada dataset ojolboosttrack2
---

## Tujuan

Mendefinisikan aturan teknis penulisan SQL yang boleh dieksekusi oleh The Auditor
ke BigQuery dataset `ojolboosttrack2`. Dokumen ini adalah panduan komplementer dari
`transaction_schema.md` yang berfokus pada **cara menulis query yang aman dan valid**.

> ⚠️ **INGAT**: Semua query yang dihasilkan WAJIB dilewatkan ke `AuditorValidator.enforce()`
> sebelum dieksekusi. Ini adalah ATURAN #2 yang tidak dapat dilanggar (CLAUDE.md Seksi 5.1).

---

## Prinsip Dasar SQL untuk The Auditor

1. **Gunakan parameterized queries** — tidak pernah string interpolation langsung untuk nilai user.
2. **Selalu sertakan dataset prefix** — `ojolboosttrack2.nama_tabel`, bukan `nama_tabel` saja.
3. **Batasi SELECT** — gunakan `LIMIT` untuk query analisis (maks 1.000 baris).
4. **Gunakan CURRENT_TIMESTAMP()** untuk field waktu otomatis — jangan hardcode timestamp.
5. **Selalu sertakan `created_at`** dalam setiap INSERT.

---

## Template INSERT yang Valid

### INSERT Transaksi Harian

```sql
INSERT INTO `ojolboosttrack2.trx_daily_income`
    (transaction_id, amount, transaction_date, service_type,
     zone, notes, driver_id, created_at, status)
VALUES
    (@transaction_id, @amount, @transaction_date, @service_type,
     @zone, @notes, @driver_id, CURRENT_TIMESTAMP(), 'recorded');
```

**Parameter wajib**:
- `@transaction_id` — UUID (di-generate oleh sistem, bukan pengguna)
- `@amount` — FLOAT64, > 0
- `@transaction_date` — TIMESTAMP, tidak boleh masa depan
- `@service_type` — STRING, salah satu dari: `'ride'`, `'food'`, `'package'`

**Parameter opsional** (boleh NULL):
- `@zone`, `@notes`, `@driver_id`

---

### INSERT Driver State (Upsert Pattern)

```sql
-- BigQuery tidak memiliki native UPSERT, gunakan MERGE
MERGE `ojolboosttrack2.driver_state` AS target
USING (SELECT @driver_id AS driver_id, @state_date AS state_date) AS source
ON target.driver_id = source.driver_id AND target.state_date = source.state_date
WHEN MATCHED THEN
    UPDATE SET
        total_income_today = @total_income_today,
        trip_count_today = @trip_count_today,
        last_zone = @last_zone,
        updated_at = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN
    INSERT (driver_id, state_date, total_income_today, trip_count_today,
            active_hours, last_zone, updated_at)
    VALUES (@driver_id, @state_date, @total_income_today, @trip_count_today,
            @active_hours, @last_zone, CURRENT_TIMESTAMP());
```

---

## Template SELECT yang Valid

### Laporan Pendapatan Harian

```sql
SELECT
    DATE(transaction_date) AS tanggal,
    SUM(amount) AS total_pendapatan,
    COUNT(*) AS jumlah_trip,
    service_type
FROM `ojolboosttrack2.trx_daily_income`
WHERE DATE(transaction_date) = @target_date
GROUP BY tanggal, service_type
ORDER BY tanggal DESC
LIMIT 100;
```

### Saldo / Balance Snapshot Hari Ini

```sql
SELECT
    SUM(amount) AS total_hari_ini,
    COUNT(*) AS trip_count,
    MAX(transaction_date) AS transaksi_terakhir
FROM `ojolboosttrack2.trx_daily_income`
WHERE DATE(transaction_date) = CURRENT_DATE()
  AND status = 'recorded';
```

---

## Query yang Dilarang Keras (Contoh Anti-Pattern)

```sql
-- ❌ String interpolation langsung (SQL injection risk)
f"INSERT INTO ojolboosttrack2.trx_daily_income VALUES ({user_input})"

-- ❌ Tanpa dataset prefix
SELECT * FROM trx_daily_income

-- ❌ DELETE
DELETE FROM `ojolboosttrack2.trx_daily_income` WHERE amount < 10000

-- ❌ SELECT tanpa LIMIT pada dataset besar
SELECT * FROM `ojolboosttrack2.zone_demand_history`

-- ❌ DROP / TRUNCATE
TRUNCATE TABLE `ojolboosttrack2.trx_daily_income`
```

---

## Estimasi Biaya Query BigQuery

Setiap query SELECT memindai data. Panduan efisiensi:

| Tabel | Estimasi Ukuran | Rekomendasi Filter Wajib |
|---|---|---|
| `trx_daily_income` | < 10 MB | `WHERE DATE(transaction_date) = ...` |
| `zone_demand_history` | > 1 GB | `WHERE DATE(pickup_timestamp) BETWEEN ... AND ...` |
| `driver_state` | < 1 MB | `WHERE driver_id = @driver_id` |

> Selalu filter dengan kolom partisi/tanggal untuk meminimalkan biaya scan.
