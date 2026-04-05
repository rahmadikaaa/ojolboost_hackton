# Skill: API Response Parser
# Agent: Environmental
# Trigger: Setiap kali response dari OpenWeather API diterima dan perlu diparsing ke WeatherResponseSchema
# Description: Aturan parsing dan transformasi raw OpenWeather API response ke format standar sistem
---

## Tujuan

Mendefinisikan bagaimana Environmental agent harus memparsing response mentah dari OpenWeather API
(`/weather` endpoint) ke dalam `WeatherResponseSchema` yang terdefinisi di `shared/schemas.py`.

## Mapping: OpenWeather → WeatherResponseSchema

| OpenWeather Field | Path | Mapped To | Transformasi |
|---|---|---|---|
| Nama kota | `name` | `location` | Langsung |
| Kondisi utama | `weather[0].main` | `condition` | Lowercase, lihat tabel mapping |
| Suhu | `main.temp` | `temperature_celsius` | Langsung (sudah metric) |
| Kelembaban | `main.humidity` | `humidity_percent` | Langsung |
| `weather[0].main` | — | `alert_level` | Berlakukan tabel alert_level |

## Mapping Kondisi OpenWeather → WeatherCondition Enum

| OpenWeather `main` | `WeatherCondition` |
|---|---|
| `Clear` | `clear` |
| `Clouds` | `cloudy` |
| `Drizzle` | `rain` |
| `Rain` | `rain` |
| `Thunderstorm` | `storm` |
| `Snow` | `cloudy` (tidak relevan, Jakarta) |
| Lainnya | `unknown` |

Mapping kondisi ke `heavy_rain`:
- Jika `main.Rain.1h > 10` (mm/jam) → override ke `heavy_rain`
- Jika `weather[0].description` contains "heavy" → override ke `heavy_rain`

## Error Handling

| Kondisi Error | Tindakan |
|---|---|
| API key tidak valid (401) | Raise exception, kembalikan error ke Bang Jek |
| Kota tidak ditemukan (404) | Kembalikan `condition: unknown`, `alert_level: low`, dengan pesan "Kota tidak ditemukan" |
| Timeout (> 5 detik) | Retry 1x, jika masih gagal kembalikan error |
| Rate limit (429) | Tunggu 60 detik, kembalikan cached data jika ada |

## Contoh Raw Response → Parsed

Input (OpenWeather raw):
```json
{
  "name": "South Jakarta",
  "main": {"temp": 28.5, "humidity": 87},
  "weather": [{"main": "Rain", "description": "heavy intensity rain"}],
  "rain": {"1h": 12.3}
}
```

Output (WeatherResponseSchema):
```json
{
  "location": "South Jakarta",
  "condition": "heavy_rain",
  "temperature_celsius": 28.5,
  "humidity_percent": 87.0,
  "alert_level": "high",
  "pivot_recommendation": "Hujan deras terdeteksi. Pivot ke Food delivery disarankan."
}
```
