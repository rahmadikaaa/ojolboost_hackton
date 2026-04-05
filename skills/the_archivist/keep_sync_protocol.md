# Skill: Keep Sync Protocol
# Agent: The Archivist
# Trigger: Ketika sinkronisasi antara lokal dan Google Keep diperlukan, atau menangani error koneksi MCP Notes
# Description: Protokol sinkronisasi, penanganan error, dan retry logic untuk koneksi MCP ke Google Keep/Notes
---

## Tujuan

Mendefinisikan protokol teknis yang harus diikuti The Archivist saat berinteraksi dengan
Google Keep/Notes melalui MCP server, termasuk penanganan kegagalan koneksi, retry logic,
dan prosedur sinkronisasi data.

---

## Arsitektur Koneksi

```
The Archivist (agent)
        │
        ▼
MCP Server (mcp_server/server.py)
        │  [stateless, via shared/context.py]
        ▼
Google Keep API / Google Notes API
        │
        ▼
Google Keep Cloud Storage
```

Semua state disimpan di **Google Keep Cloud**, bukan di MCP server.
MCP server hanya bertindak sebagai **passthrough yang stateless**.

---

## Operasi MCP yang Tersedia untuk The Archivist

| Operasi | MCP Tool | Timeout |
|---|---|---|
| Simpan catatan baru | `save_note()` | 5 detik |
| Cari catatan | `search_notes()` | 3 detik |
| Daftar catatan terbaru | `list_notes()` | 3 detik |
| Update catatan | `update_note()` | 5 detik |

> ⚠️ The Archivist **tidak memiliki** operasi DELETE ke Google Keep.
> Penghapusan catatan hanya dapat dilakukan oleh pengguna secara manual.

---

## Retry Logic

Untuk setiap operasi MCP, gunakan strategi retry berikut:

```
Attempt 1: Eksekusi langsung
Attempt 2: Retry setelah 1 detik (jika timeout/network error)
Attempt 3: Retry setelah 3 detik (exponential backoff)
Attempt 4+: STOP — kembalikan error ke Bang Jek
```

**Kode error yang di-retry**: `408 Timeout`, `503 Service Unavailable`, `429 Rate Limit`
**Kode error yang TIDAK di-retry**: `400 Bad Request`, `401 Unauthorized`, `404 Not Found`

---

## Penanganan Error Spesifik

### Error: 401 Unauthorized (Token Expired)
```
1. Log error ke shared/logger.py dengan level ERROR
2. Kembalikan pesan ke Bang Jek:
   {"error": "TOKEN_EXPIRED", "message": "Koneksi ke Google Notes perlu diperbarui."}
3. Bang Jek menyampaikan: "Bang, saya perlu re-login ke Google dulu nih."
```

### Error: 429 Rate Limit
```
1. Cek header Retry-After dari response
2. Tunggu sesuai nilai header (atau 60 detik jika tidak ada header)
3. Lakukan 1x retry
4. Jika masih gagal → kembalikan error ke Bang Jek
```

### Error: MCP Server Unreachable
```
1. Fallback: Simpan catatan ke shared/context.py sebagai "pending_notes" (sementara dalam session)
2. Log warning: "MCP server tidak dapat dijangkau, catatan disimpan sementara di session context"
3. Kembalikan ke Bang Jek:
   {"status": "pending", "message": "Catatan disimpan sementara, akan disinkronkan saat koneksi pulih"}
```

---

## Protokol Validasi Sebelum Sinkronisasi

Sebelum memanggil `save_note()`, validasi:

1. ✅ `title` tidak kosong dan ≤ 200 karakter
2. ✅ `content` tidak kosong dan ≤ 5.000 karakter
3. ✅ Minimal 1 tag dari taksonomi resmi (`#operasional`, `#keuangan`, dll.)
4. ✅ MCP server dapat dijangkau (health check via `ping` jika diperlukan)

Jika validasi 1-3 gagal → kembalikan `ValidationError` tanpa memanggil MCP.
Jika validasi 4 gagal (MCP unreachable) → gunakan fallback pending_notes.

---

## Output Format: Sukses

```json
{
  "note_id": "keep_note_abc123",
  "title": "[KEUANGAN] Pendapatan hari ini — 2026-04-05",
  "content": "Total: Rp 250.000 dari 8 trip.",
  "tags": ["#keuangan"],
  "created_at": "2026-04-05T13:05:00+07:00",
  "sync_status": "synced"
}
```

## Output Format: Pending (MCP Unavailable)

```json
{
  "note_id": null,
  "title": "[KEUANGAN] Pendapatan hari ini — 2026-04-05",
  "sync_status": "pending",
  "message": "Catatan disimpan sementara, belum tersinkronisasi ke Google Keep."
}
```
