# Skill: Schedule Creation
# Agent: The Planner
# Trigger: Ketika pengguna meminta membuat jadwal, pengingat, atau reservasi waktu tertentu
# Description: Panduan membuat entri kalender dan task via MCP Calendar & Task Manager
---

## Tujuan

Mendefinisikan bagaimana The Planner membuat entri kalender Google dan pengingat tugas
via MCP server sesuai permintaan yang didelegasikan oleh Bang Jek.

## Format Input dari Bang Jek

Bang Jek akan mengirimkan task dengan konteks seperti:
- `"Buat pengingat besok jam 9 pagi untuk ganti oli"`
- `"Jadwalkan rehat makan siang jam 12 di area Blok M selama 45 menit"`
- `"Ingatkan saya jam 7 malam untuk transfer uang"`

## Logika Parsing Waktu

| Ungkapan Pengguna | Interpretasi |
|---|---|
| "besok jam 9 pagi" | Tanggal = today + 1, jam = 09:00 timezone lokal |
| "jam 9" (tanpa keterangan) | Berasumsi waktu terdekat yang belum lewat |
| "nanti sore" | 16:00-17:00 |
| "malam ini" | 19:00 |
| "minggu depan Senin" | Senin berikutnya dari tanggal saat ini |

**Timezone default**: WIB (UTC+7) — `Asia/Jakarta`

## Struktur ScheduleEntrySchema (referensi shared/schemas.py)

```python
title: str          # max 200 karakter
scheduled_at: datetime
duration_minutes: int  # default 30
description: str    # opsional
reminder_minutes_before: int  # default 15
```

## MCP Tool yang Dipanggil

```python
# Membuat event kalender
create_calendar_event(
    calendar_id="primary",
    title=entry.title,
    start_datetime=entry.scheduled_at.isoformat(),
    duration_minutes=entry.duration_minutes,
    description=entry.description,
    reminder_minutes=entry.reminder_minutes_before
)
```

## Output Format ke Bang Jek

```json
{
  "event_id": "google_event_abc123",
  "status": "completed",
  "scheduled_at": "2026-04-06T09:00:00+07:00",
  "title": "Ganti Oli Motor",
  "reminder_set": true
}
```
