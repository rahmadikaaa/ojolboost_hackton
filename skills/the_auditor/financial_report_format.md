# Skill: Financial Report Format
# Agent: The Auditor
# Trigger: Ketika pengguna meminta laporan keuangan, ringkasan pendapatan, atau rekap harian/mingguan/bulanan
# Description: Format standar laporan keuangan yang dihasilkan dari query BigQuery dan dikembalikan ke Bang Jek
---

## Tujuan

Mendefinisikan struktur dan format laporan keuangan yang dihasilkan The Auditor
dari data BigQuery, sehingga Bang Jek dapat mengubahnya menjadi narasi taktis
yang mudah dipahami pengemudi.

---

## Jenis Laporan yang Didukung

| Tipe | Trigger Kata Kunci | Periode |
|---|---|---|
| `daily` | "hari ini", "tadi", "pendapatan hari ini" | 1 hari (CURRENT_DATE) |
| `weekly` | "minggu ini", "7 hari terakhir", "minggu lalu" | 7 hari |
| `monthly` | "bulan ini", "bulan lalu", "rekap bulanan" | 30 hari |
| `snapshot` | "saldo sekarang", "udah berapa hari ini" | Real-time hari ini |

---

## Format Laporan Harian (`daily`)

### Query

```sql
SELECT
    SUM(amount)                          AS total_income,
    COUNT(*)                             AS transaction_count,
    AVG(amount)                          AS avg_per_trip,
    MAX(amount)                          AS max_trip,
    MIN(amount)                          AS min_trip,
    COUNTIF(service_type = 'ride')       AS ride_count,
    COUNTIF(service_type = 'food')       AS food_count,
    COUNTIF(service_type = 'package')    AS package_count,
    SUM(IF(service_type='ride', amount, 0))    AS ride_income,
    SUM(IF(service_type='food', amount, 0))    AS food_income,
    SUM(IF(service_type='package', amount, 0)) AS package_income
FROM `ojolboosttrack2.trx_daily_income`
WHERE DATE(transaction_date) = @target_date
  AND status = 'recorded';
```

### Output JSON ke Bang Jek

```json
{
  "report_date": "2026-04-05",
  "total_income": 250000,
  "transaction_count": 8,
  "average_per_trip": 31250,
  "max_trip": 55000,
  "min_trip": 15000,
  "by_service_type": {
    "ride": 150000,
    "food": 85000,
    "package": 15000
  },
  "top_zone": "Sudirman"
}
```

### Konversi Bang Jek → Narasi Pengguna

> "Bang, rekap hari ini: total **Rp 250.000** dari **8 trip**. Rata-rata per trip Rp 31.250.
> Trip terbesar Rp 55 ribu. Ride jadi penghasil terbesar (Rp 150 ribu). Udah lumayan nih! 🔥"

---

## Format Laporan Mingguan (`weekly`)

```json
{
  "report_period": "2026-03-30 s/d 2026-04-05",
  "total_income": 1750000,
  "transaction_count": 56,
  "average_per_trip": 31250,
  "best_day": "Jumat",
  "best_day_income": 320000,
  "by_service_type": {
    "ride": 980000,
    "food": 620000,
    "package": 150000
  },
  "trend": "rising"
}
```

---

## Format Snapshot Real-time (`snapshot`)

```json
{
  "snapshot_at": "2026-04-05T13:05:00+07:00",
  "total_income_today": 250000,
  "trip_count_today": 8,
  "last_trip_at": "2026-04-05T12:45:00+07:00",
  "status": "active"
}
```

---

## Aturan Penyajian Angka

| Kondisi | Format | Contoh |
|---|---|---|
| < Rp 1.000 | Integer biasa | `Rp 750` |
| Rp 1.000 - Rp 999.999 | Ribuan dengan titik | `Rp 250.000` |
| ≥ Rp 1.000.000 | Jutaan | `Rp 1,75 juta` |

Bang Jek wajib menggunakan format ini saat menyampaikan angka ke pengguna.

---

## Penanganan Data Kosong

Jika query mengembalikan `total_income = NULL` (tidak ada transaksi):

```json
{
  "report_date": "2026-04-05",
  "total_income": 0,
  "transaction_count": 0,
  "message": "Belum ada transaksi tercatat untuk periode ini."
}
```

Bang Jek: *"Belum ada trip yang tercatat hari ini, Bang. Jangan lupa catat setiap orderan ya!"*
