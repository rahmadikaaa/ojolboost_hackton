# Skill: Service Pivot Logic
# Agent: Environmental
# Trigger: Ketika cuaca memburuk dan sistem perlu merekomendasikan perubahan strategi layanan (Ride → Food)
# Description: Logika pivot strategi layanan berdasarkan kondisi cuaca real-time untuk memaksimalkan yield
---

## Tujuan

Mendefinisikan kapan dan bagaimana merekomendasikan perubahan jenis layanan (service pivot)
berdasarkan kondisi cuaca yang terdeteksi, untuk memaksimalkan Active-hour yield.

## Logika Pivot

### Situasi 1: Hujan Ringan–Sedang (`rain`, alert: `medium`)
- **Food delivery naik**: Pelanggan enggan keluar, memesan makanan online.
- **Ride turun**: Pengemudi lebih sedikit, tapi permintaan juga turun.
- **Rekomendasi**: Beralih ke Food jika area mendukung (dekat restoran/mall).

### Situasi 2: Hujan Deras (`heavy_rain`, alert: `high`)
- **Food delivery melonjak**: Permintaan Food bisa naik 40-60% di area urban.
- **Ride sangat berbahaya**: Jalan licin, risiko naik.
- **Rekomendasi WAJIB**: Pivot ke Food delivery segera.

### Situasi 3: Badai (`storm`, alert: `critical`)
- **Semua layanan berisiko tinggi**.
- **Rekomendasi**: Hentikan sementara, cari tempat berteduh.

### Situasi 4: Cuaca Cerah (`clear`, alert: `low`)
- **Ride optimal**: Cuaca mendukung perjalanan.
- **Tidak ada pivot**: Tetap di strategi saat ini.

## Area yang Cocok untuk Food Pivot

Zona prioritas pivot ke Food saat hujan:
- Area dengan kepadatan restoran tinggi (mall, food street, cloud kitchen)
- Zona perkantoran saat jam makan siang

## Output Pivot Recommendation (string)

Contoh output `pivot_recommendation`:
- `"Hujan deras mengguyur Sudirman. Data historis: Food demand naik 55% saat kondisi ini. Segera aktifkan mode Food!"`
- `"Cuaca cerah mendukung Ride. Fokus di zona Sudirman yang sedang ramai."`
- `"PERINGATAN BADAI: Hentikan operasional sementara demi keselamatan."`
