# Skill: Note Indexing
# Agent: The Archivist
# Trigger: Ketika menyimpan catatan baru, memberi label, atau mengorganisir informasi ke Google Keep/Notes
# Description: Sistem indexing dan tagging catatan agar mudah ditemukan kembali via pencarian semantik
---

## Tujuan

Mendefinisikan sistem indexing dan pemberian tag catatan yang disimpan oleh The Archivist
ke Google Keep/Notes via MCP, agar setiap catatan dapat ditemukan kembali dengan akurat
dan efisien melalui pencarian di masa mendatang.

---

## Taksonomi Tag (Wajib Diikuti)

Setiap catatan WAJIB memiliki minimal **1 tag kategori utama** dan boleh memiliki tag tambahan.

### Kategori Utama (Pilih Satu)

| Tag | Digunakan untuk |
|---|---|
| `#operasional` | Informasi lapangan: zona panas, rute efisien, trik order |
| `#keuangan` | Catatan pendapatan, pengeluaran, hutang, cicilan |
| `#kendaraan` | Servis, sparepart, kondisi motor/mobil |
| `#pelanggan` | Catatan pelanggan tetap, preferensi, lokasi favorit |
| `#jadwal` | Rencana kerja, shift, libur |
| `#info-platform` | Update aplikasi ojol, promo, kebijakan baru |
| `#personal` | Catatan pribadi non-operasional |

### Tag Modifier (Opsional, dapat dikombinasi)

| Tag | Artinya |
|---|---|
| `#penting` | Informasi kritis yang perlu sering dirujuk |
| `#sementara` | Catatan yang mungkin akan dihapus setelah digunakan |
| `#to-follow-up` | Perlu tindak lanjut di masa depan |
| `#arsip` | Informasi lama yang disimpan sebagai referensi historis |

---

## Logika Auto-Tagging

The Archivist melakukan auto-tagging berdasarkan konten catatan:

| Jika konten mengandung | Tag yang ditambahkan otomatis |
|---|---|
| Kata: "pendapatan", "income", "bayar", "transfer", "Rp" | `#keuangan` |
| Kata: "oli", "ban", "servis", "bengkel", "sparepart" | `#kendaraan` |
| Kata: "zona", "hotspot", "area", "orderan", "pickup" | `#operasional` |
| Kata: "jadwal", "besok", "jam", "shift", "libur" | `#jadwal` |
| Angka besar: deteksi nominal > Rp 100.000 | `#keuangan` |

---

## Format Judul Catatan Standar

```
[KATEGORI] {subjek_singkat} — {tanggal_YYYY-MM-DD}
```

Contoh:
- `[KEUANGAN] Pendapatan minggu ini — 2026-04-05`
- `[KENDARAAN] Ganti oli — 2026-04-05`
- `[OPERASIONAL] Zona panas siang Sudirman — 2026-04-05`

---

## MCP Tool Call

```python
save_note(
    title="[OPERASIONAL] Zona panas siang Sudirman — 2026-04-05",
    content="Berdasarkan data hari ini, Sudirman ramai antara jam 12-14. "
            "Food delivery dominan. Skor permintaan: 0.78.",
    tags=["#operasional", "#penting"]
)
```

---

## Output Format ke Bang Jek

```json
{
  "note_id": "keep_note_abc123",
  "title": "[OPERASIONAL] Zona panas siang Sudirman — 2026-04-05",
  "content": "Berdasarkan data hari ini...",
  "tags": ["#operasional", "#penting"],
  "created_at": "2026-04-05T13:00:00+07:00"
}
```

---

## Aturan Duplikasi

Sebelum menyimpan catatan baru:
1. Cek apakah catatan dengan judul sangat mirip (similarity > 85%) sudah ada dalam 7 hari terakhir.
2. Jika ada → **append** ke catatan yang ada, jangan buat duplikat.
3. Jika tidak ada → buat catatan baru.
