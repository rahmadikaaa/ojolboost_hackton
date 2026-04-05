# Skill: Task Reminder Format
# Agent: The Planner
# Trigger: Ketika membuat pengingat tugas, task to-do, atau reminder one-time/recurring untuk pengemudi
# Description: Format standar untuk membuat task reminder via MCP Task Manager agar konsisten dan actionable
---

## Tujuan

Mendefinisikan format baku pengingat tugas (task reminder) yang dibuat oleh The Planner
melalui MCP Task Manager, sehingga setiap notifikasi yang diterima pengguna bersifat
**spesifik, actionable, dan tidak ambigu**.

---

## Anatomi Reminder yang Baik

Setiap reminder wajib memiliki 3 komponen inti:

```
[KAPAN] + [APA yang dilakukan] + [KONTEKS opsional]
```

| Komponen | Wajib | Contoh |
|---|---|---|
| Kapan | ✅ | "Besok jam 09:00 WIB" |
| Apa | ✅ | "Ganti oli motor" |
| Konteks | ⚠️ Jika relevan | "di bengkel langganan Pak Budi" |

---

## Template Judul Reminder per Kategori

### Kategori: Servis & Kendaraan
```
[SERVIS] {detail} — {lokasi_opsional}
Contoh: "[SERVIS] Ganti oli motor — Bengkel Pak Budi Kemayoran"
```

### Kategori: Keuangan & Transfer
```
[KEUANGAN] {detail} — Rp {nominal_opsional}
Contoh: "[KEUANGAN] Transfer cicilan — Rp 500.000"
```

### Kategori: Operasional Lapangan
```
[OPS] {detail} — {zona_opsional}
Contoh: "[OPS] Ambil orderan batch pagi — Zona Sudirman"
```

### Kategori: Umum / Lainnya
```
[INFO] {detail}
Contoh: "[INFO] Perpanjang SIM di Samsat"
```

---

## Aturan Durasi & Reminder Offset

| Tipe Tugas | Durasi Default | Reminder Before |
|---|---|---|
| Servis kendaraan | 60 menit | 30 menit sebelum |
| Keuangan/transfer | 15 menit | 15 menit sebelum |
| Operasional lapangan | 30 menit | 10 menit sebelum |
| Umum | 30 menit | 15 menit sebelum |

---

## Aturan Recurring Reminder

Jika pengguna meminta pengingat berulang:
- "setiap hari" → `recurrence: daily`
- "setiap Senin" → `recurrence: weekly, day: monday`
- "setiap awal bulan" → `recurrence: monthly, day: 1`

Recurring reminder WAJIB memiliki tanggal berakhir maksimum 1 tahun dari tanggal dibuat,
kecuali pengguna secara eksplisit menyebutkan durasi berbeda.

---

## MCP Tool Call

```python
create_task_reminder(
    title="[SERVIS] Ganti oli motor",
    due_datetime="2026-04-06T09:00:00+07:00",
    duration_minutes=60,
    reminder_minutes_before=30,
    notes="Konteks tambahan dari pengguna jika ada",
    recurrence=None  # atau "daily" / "weekly" / "monthly"
)
```

---

## Output Format ke Bang Jek

```json
{
  "event_id": "task_reminder_xyz789",
  "status": "completed",
  "scheduled_at": "2026-04-06T09:00:00+07:00",
  "title": "[SERVIS] Ganti oli motor",
  "reminder_set": true
}
```

---

## Anti-Pattern yang Dilarang

- ❌ Jangan buat reminder tanpa judul yang jelas: `"Reminder 1"`, `"Tugas baru"`
- ❌ Jangan buat reminder di masa lalu (waktu sudah lewat)
- ❌ Jangan gandakan reminder yang identik dalam 24 jam → cek dulu via `list_upcoming_events()`
