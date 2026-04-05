# Skill: Semantic Search
# Agent: The Archivist
# Trigger: Ketika pengguna mencari catatan lama, bertanya "catatan apa yang ada tentang X", atau membutuhkan referensi historis
# Description: Panduan pencarian catatan berbasis makna/semantik di Google Keep/Notes via MCP untuk hasil yang relevan
---

## Tujuan

Mendefinisikan strategi pencarian catatan di Google Keep/Notes yang mengutamakan
**relevansi makna** (bukan sekadar kecocokan kata kunci persis) untuk membantu
pengemudi menemukan informasi lama yang berguna dengan cepat.

---

## Jenis Pencarian yang Didukung

### Tipe 1: Pencarian Kata Kunci
Digunakan ketika pengguna menyebut kata/frasa spesifik.

```python
search_notes(query="oli motor", search_type="keyword")
```

### Tipe 2: Pencarian Semantik (Preferensi Utama)
Digunakan ketika query pengguna bersifat deskriptif atau tidak spesifik.

```python
search_notes(query="catatan soal kondisi motor", search_type="semantic")
```

### Tipe 3: Pencarian Berdasarkan Tag
Digunakan ketika pengguna menyebut kategori atau topik umum.

```python
search_notes(tags=["#kendaraan"], search_type="tag")
```

### Tipe 4: Pencarian Berdasarkan Rentang Waktu
Digunakan ketika pengguna menyebut periode ("minggu lalu", "bulan ini").

```python
search_notes(
    date_from="2026-03-28",
    date_to="2026-04-05",
    search_type="date_range"
)
```

---

## Mapping Pertanyaan Pengguna → Strategi Pencarian

| Contoh Pertanyaan | Strategi | Query/Filter |
|---|---|---|
| "Cari catatan soal servis motor" | Semantic | `"catatan servis motor"` |
| "Catatan minggu lalu ada apa?" | Date range | `date_from=7 hari lalu` |
| "Tunjukkan semua catatan keuangan" | Tag | `tags=["#keuangan"]` |
| "Ada catatan tentang pelanggan di Menteng?" | Semantic + Keyword | `"pelanggan Menteng"` |
| "Cari 'Pak Budi'" | Keyword | `"Pak Budi"` |

---

## Logika Ranking Hasil

Jika ditemukan lebih dari 5 hasil, urutkan berdasarkan:
1. **Relevansi** — seberapa dekat konten dengan query (score tertinggi dulu)
2. **Kesegaran** — catatan lebih baru diprioritaskan jika skor relevansi sama
3. **Tag prioritas** — catatan dengan tag `#penting` naik 1 peringkat

---

## Penanganan Hasil Kosong

Jika `search_notes()` mengembalikan 0 hasil:

1. **Coba query lebih luas**: Hapus kata-kata spesifik, sisakan kata kunci utama.
2. **Coba pencarian tag alternatif**: Infer tag yang mungkin relevan dari konteks.
3. **Jika masih kosong**: Kembalikan ke Bang Jek dengan pesan:
   ```json
   {
     "total_found": 0,
     "message": "Tidak ditemukan catatan yang relevan dengan pencarian ini."
   }
   ```
   Bang Jek akan menyampaikan ke pengguna secara natural: *"Hmm, saya tidak nemu catatan soal itu Bang. Mungkin belum pernah dicatat?"*

---

## Output Format ke Bang Jek

```json
{
  "query": "servis motor",
  "results": [
    {
      "note_id": "keep_note_abc123",
      "title": "[KENDARAAN] Ganti oli — 2026-04-05",
      "content": "Ganti oli di bengkel Pak Budi. Biaya Rp 85.000.",
      "tags": ["#kendaraan", "#keuangan"],
      "created_at": "2026-04-05T10:00:00+07:00",
      "url": "https://keep.google.com/u/0/#NOTE/abc123"
    }
  ],
  "total_found": 1
}
```

---

## Batas & Performa

- Maksimum hasil yang dikembalikan: **10 catatan** per query.
- Timeout pencarian: **3 detik** — jika melebihi, kembalikan partial result yang sudah ada.
- Caching hasil pencarian identik: **5 menit** untuk mengurangi MCP round-trip.
