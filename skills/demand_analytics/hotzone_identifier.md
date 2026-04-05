# Skill: Hotzone Identifier
# Agent: Demand Analytics
# Trigger: Ketika pengguna meminta tahu zona mana yang paling ramai, "di mana sebaiknya ngetem", atau "hotspot orderan"
# Description: Identifikasi zona pickup dengan probabilitas permintaan tertinggi dari data BigQuery historis
---

## Tujuan

Mengidentifikasi zona geografis (area/kelurahan/landmark) yang memiliki volume permintaan pickup paling tinggi
berdasarkan data historis di tabel `zone_demand_history` pada dataset `ojolboosttrack2`.

## Query Template BigQuery

```sql
SELECT
    zone_name,
    COUNT(*) AS total_trips,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS probability_score,
    AVG(trip_duration_minutes) AS avg_duration,
    CASE
        WHEN COUNT(*) > LAG(COUNT(*)) OVER (PARTITION BY zone_name ORDER BY DATE(pickup_timestamp))
        THEN 'rising'
        WHEN COUNT(*) < LAG(COUNT(*)) OVER (PARTITION BY zone_name ORDER BY DATE(pickup_timestamp))
        THEN 'falling'
        ELSE 'stable'
    END AS demand_trend
FROM `ojolboosttrack2.zone_demand_history`
WHERE
    DATE(pickup_timestamp) BETWEEN DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) AND CURRENT_DATE()
    AND EXTRACT(HOUR FROM pickup_timestamp) BETWEEN @start_hour AND @end_hour
GROUP BY zone_name
ORDER BY probability_score DESC
LIMIT 5;
```

## Parameter Wajib
- `@start_hour`: Jam mulai analisis (INTEGER, 0-23)
- `@end_hour`: Jam akhir analisis (INTEGER, 0-23)

## Interpretasi Hasil

| probability_score | Interpretasi | Aksi Rekomendasi |
|---|---|---|
| > 0.25 | Hotzone Utama | "Segera menuju zona ini" |
| 0.10 - 0.25 | Zona Potensial | "Layak dikunjungi jika hotzone penuh" |
| < 0.10 | Zona Dingin | "Hindari saat ini" |

## Output Format (JSON ke Bang Jek)

```json
{
  "zones": [
    {
      "zone_name": "Sudirman",
      "probability_score": 0.42,
      "demand_trend": "rising",
      "recommended_service": "food",
      "historical_avg": 8.5
    }
  ],
  "recommendation": "Sudirman zona terpanas saat ini dengan probabilitas 42%",
  "confidence": 0.87
}
```

## Catatan Penting
- Selalu filter berdasarkan jam aktif saat ini untuk relevansi.
- Jika query mengembalikan 0 baris, kembalikan pesan "Data tidak cukup untuk analisis zona saat ini."
- Semua query ke BigQuery WAJIB melalui AuditorValidator? TIDAK — Demand Analytics hanya READ, cukup PreToolUse hook.
