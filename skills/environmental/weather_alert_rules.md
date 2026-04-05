# Skill: Weather Alert Rules
# Agent: Environmental
# Trigger: Ketika ada permintaan pemantauan cuaca, alert hujan, atau kondisi lingkungan yang mempengaruhi operasional
# Description: Aturan kapan dan bagaimana weather alert dipicu berdasarkan data OpenWeather API
---

## Tujuan

Mendefinisikan logika deterministik kapan Environmental agent harus mengirimkan weather alert
dan pada level apa, berdasarkan data real-time dari OpenWeather API.

## Alert Level Matrix

| Kondisi | Suhu | Kelembaban | Alert Level | Aksi |
|---|---|---|---|---|
| `clear` | Berapapun | < 70% | `low` | Tidak ada alert khusus |
| `cloudy` | Berapapun | 70-85% | `low` | Monitor saja |
| `rain` | < 30°C | > 80% | `medium` | Rekomendasikan pivot ke Food |
| `rain` | Berapapun | > 90% | `high` | Alert segera — pivot wajib |
| `heavy_rain` | Berapapun | Berapapun | `high` | Alert segera — pivot wajib |
| `storm` | Berapapun | Berapapun | `critical` | Hentikan operasional |

## Aturan Trigger Alert

1. **Alert MEDIUM atau lebih**: Kirim rekomendasi pivot ke Bang Jek dalam output.
2. **Alert HIGH**: Sertakan teks spesifik dalam `pivot_recommendation` field.
3. **Alert CRITICAL**: Rekomendasikan penghentian operasional sementara. Bang Jek akan sampaikan dengan nada serius.

## Format Response ke Bang Jek

```json
{
  "location": "Sudirman, Jakarta",
  "condition": "rain",
  "temperature_celsius": 27.3,
  "humidity_percent": 88.0,
  "alert_level": "high",
  "pivot_recommendation": "Hujan deras di Sudirman. Food delivery lebih potensial saat ini.",
  "fetched_at": "2026-04-05T13:00:00Z"
}
```

## Catatan Teknis

- Selalu gunakan satuan `metric` (°C) saat memanggil OpenWeather API.
- Cache hasil selama 10 menit untuk menghindari rate limiting.
- Endpoint: `GET /weather?q={city}&units=metric&appid={API_KEY}`
