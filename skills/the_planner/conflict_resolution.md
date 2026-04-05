# Skill: Conflict Resolution
# Agent: The Planner
# Trigger: Ketika ada jadwal baru yang berpotensi bertabrakan dengan jadwal yang sudah ada, atau saat cek ketersediaan waktu
# Description: Logika deterministik untuk mendeteksi dan menyelesaikan konflik jadwal sebelum membuat entri kalender baru
---

## Tujuan

Mendefinisikan bagaimana The Planner mendeteksi tabrakan jadwal (scheduling conflict)
dan mengambil keputusan resolusi sebelum membuat entri kalender baru via MCP Calendar.

---

## Alur Wajib Sebelum `create_calendar_event()`

```
SETIAP KALI akan membuat event baru:
    1. Panggil list_upcoming_events(range_hours=24)
    2. Cek apakah ada overlap dengan event baru
    3. Jika TIDAK ada konflik → buat event langsung
    4. Jika ADA konflik → jalankan strategi resolusi (lihat di bawah)
```

---

## Definisi Konflik

Dua event dianggap **konflik** jika:

```
start_A < end_B  DAN  end_A > start_B
```

Artinya: event baru tumpang tindih dengan event yang sudah ada, bahkan sebagian.

**Toleransi minimum antar event**: 15 menit (buffer waktu perjalanan/persiapan).

---

## Strategi Resolusi Konflik

### Strategi 1: Auto-Shift (Preferensi Utama)
Geser waktu event baru ke slot yang tersedia terdekat **setelah** konflik selesai + 15 menit buffer.

**Kondisi**: Digunakan jika durasi event konflik ≤ 2 jam.

```
Event baru asal: 09:00-09:30
Event existing:  08:45-09:15
→ Auto-shift ke: 09:30 (15:00 buffer setelah 09:15)
```

### Strategi 2: Tanya Konfirmasi (Jika Auto-Shift Tidak Memungkinkan)
Kembalikan ke Bang Jek untuk disampaikan ke pengguna.

**Kondisi**: Digunakan jika:
- Konflik berlangsung > 2 jam
- Tidak ada slot tersedia dalam 4 jam ke depan
- Event konflik bersifat `high_priority` (servis kendaraan, keuangan)

Format response ke Bang Jek saat konfirmasi diperlukan:
```json
{
  "status": "conflict_detected",
  "conflict_with": "Servis Kendaraan 09:00-10:00",
  "proposed_alternatives": ["11:00", "14:00", "besok 09:00"],
  "message": "Ada jadwal bentrok Bang. Mau saya geser ke jam 11 pagi atau kapan?"
}
```

### Strategi 3: Override (Khusus Prioritas Kritis)
Hanya digunakan jika Bang Jek **secara eksplisit** menyatakan event baru harus di waktu spesifik
dan pengguna sudah mengkonfirmasi ingin override.

---

## Prioritas Event

| Kategori | Prioritas | Bisa Di-override? |
|---|---|---|
| Servis kendaraan wajib | HIGH | Tidak |
| Keuangan/deadline | HIGH | Tidak |
| Operasional lapangan | MEDIUM | Ya (oleh pengguna) |
| Pengingat umum | LOW | Ya (auto-shift) |

---

## MCP Tool Check Sebelum Buat Event

```python
# Cek slot yang ada dulu
existing_events = list_upcoming_events(
    calendar_id="primary",
    time_min=new_event.scheduled_at.isoformat(),
    time_max=(new_event.scheduled_at + timedelta(hours=4)).isoformat()
)
```

---

## Output Format ke Bang Jek (sukses tanpa konflik)

```json
{
  "event_id": "calendar_abc123",
  "status": "completed",
  "scheduled_at": "2026-04-06T09:30:00+07:00",
  "title": "[SERVIS] Ganti oli motor",
  "reminder_set": true,
  "conflict_resolved": false
}
```

## Output Format ke Bang Jek (konflik diselesaikan auto-shift)

```json
{
  "event_id": "calendar_abc124",
  "status": "completed",
  "scheduled_at": "2026-04-06T09:30:00+07:00",
  "title": "[SERVIS] Ganti oli motor",
  "reminder_set": true,
  "conflict_resolved": true,
  "original_time": "2026-04-06T09:00:00+07:00",
  "shift_reason": "Konflik dengan event existing 08:45-09:15"
}
```
