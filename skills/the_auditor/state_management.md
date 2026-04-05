# Skill: State Management
# Agent: The Auditor
# Trigger: Ketika memperbarui status aktivitas pengemudi, sinkronisasi state harian, atau memeriksa posisi/kondisi aktif saat ini
# Description: Protokol State Management untuk melacak dan memperbarui status aktivitas harian pengemudi di BigQuery
---

## Tujuan

Mendefinisikan bagaimana The Auditor mengelola **state aktivitas harian** pengemudi
melalui tabel `driver_state` di BigQuery `ojolboosttrack2`. State ini adalah "memori jangka pendek"
sistem yang mencatat kondisi pengemudi dalam satu hari kerja.

---

## Apa yang Dimaksud "State" dalam Sistem Ini

State adalah snapshot kondisi pengemudi pada saat tertentu, yang mencakup:

| Field State | Artinya |
|---|---|
| `total_income_today` | Akumulasi pendapatan hari ini (update setiap INSERT transaksi) |
| `trip_count_today` | Jumlah trip hari ini |
| `active_hours` | Estimasi jam aktif beroperasi |
| `last_zone` | Zona terakhir tempat pengemudi beroperasi |
| `updated_at` | Waktu state terakhir diperbarui |

---

## Kapan State Harus Diperbarui

| Trigger Event | Field yang Diupdate |
|---|---|
| Transaksi baru dicatat (INSERT ke `trx_daily_income`) | `total_income_today`, `trip_count_today`, `updated_at` |
| Pengemudi pindah zona | `last_zone`, `updated_at` |
| Pengguna menyebut jam aktif | `active_hours`, `updated_at` |
| Awal hari baru (00:00 WIB) | Reset semua field ke 0 / NULL |

---

## Alur State Update Setelah INSERT Transaksi

```
The Auditor mencatat transaksi baru
    │
    ▼
INSERT ke trx_daily_income ✅
    │
    ▼
Query saldo hari ini:
    SELECT SUM(amount), COUNT(*) FROM trx_daily_income WHERE DATE = TODAY
    │
    ▼
MERGE ke driver_state (Upsert):
    UPDATE total_income_today = {hasil query}
    UPDATE trip_count_today   = {hasil query}
    UPDATE updated_at         = CURRENT_TIMESTAMP()
    │
    ▼
Kembalikan balance_snapshot ke Bang Jek
```

---

## Query: Ambil State Saat Ini

```sql
SELECT
    total_income_today,
    trip_count_today,
    active_hours,
    last_zone,
    updated_at
FROM `ojolboosttrack2.driver_state`
WHERE driver_id = @driver_id
  AND state_date = CURRENT_DATE()
LIMIT 1;
```

Jika tidak ada baris → state hari ini belum diinisialisasi → INSERT state awal dengan nilai 0.

---

## Query: Update State (Upsert)

Gunakan template MERGE dari `sql_write_rules.md`:

```sql
MERGE `ojolboosttrack2.driver_state` AS target
USING (SELECT @driver_id AS driver_id, CURRENT_DATE() AS state_date) AS source
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
            0.0, @last_zone, CURRENT_TIMESTAMP());
```

---

## Aturan Reset Harian

- State direset otomatis pada awal hari (deteksi: `state_date != CURRENT_DATE()`).
- Reset = Buat record baru untuk tanggal hari ini, bukan menghapus record kemarin.
- Record kemarin **tetap disimpan** sebagai arsip historis — tidak pernah dihapus.

---

## Output Format ke Bang Jek (setelah state update)

```json
{
  "transaction_id": "txn_uuid_abc123",
  "operation": "INSERT + STATE_UPDATE",
  "table": "trx_daily_income + driver_state",
  "status": "completed",
  "balance_snapshot": 250000,
  "records_affected": 1
}
```

---

## Integrasi dengan Guardrails

Setiap operasi state update (MERGE/INSERT ke `driver_state`) **wajib** melalui:
1. `AuditorValidator.enforce()` — validasi dataset dan tabel
2. `PreToolUseHook.pre_tool_use()` — validasi tool yang dipanggil

Tidak ada shortcut atau bypass yang diizinkan, sesuai ATURAN #2 di `CLAUDE.md`.

---

## Penanganan Anomali State

| Anomali | Deteksi | Tindakan |
|---|---|---|
| `total_income_today` turun dari nilai sebelumnya | Setelah UPDATE, nilai lebih kecil dari sebelumnya | Log WARNING, kembalikan error ke Bang Jek |
| `trip_count_today` melebihi 100 dalam sehari | Nilai > 100 | Log WARNING (tidak diblokir, hanya dicatat) |
| `state_date` tidak sama dengan `CURRENT_DATE()` | Setiap kali ambil state | Inisialisasi state baru untuk hari ini |
