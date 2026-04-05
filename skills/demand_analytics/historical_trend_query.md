# Skill: Historical Trend Query
# Agent: Demand Analytics
# Trigger: Ketika analisis tren historis permintaan dibutuhkan, pertanyaan "biasanya jam berapa ramai", atau "pola mingguan orderan"
# Description: Query pola historis permintaan per zona dan per jam dari BigQuery untuk identifikasi tren temporal
---

## Tujuan

Menganalisis pola temporal permintaan (per jam dan per hari) untuk membantu pengemudi
mengidentifikasi kapan dan di mana waktu terbaik beroperasi berdasarkan data historis.

## Query: Pola Per Jam

```sql
SELECT
    EXTRACT(HOUR FROM pickup_timestamp) AS hour_of_day,
    zone_name,
    COUNT(*) AS trip_count,
    AVG(fare_amount) AS avg_fare,
    STDDEV(trip_duration_minutes) AS demand_volatility
FROM `ojolboosttrack2.zone_demand_history`
WHERE
    DATE(pickup_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
    AND zone_name = @zone_name
GROUP BY hour_of_day, zone_name
ORDER BY hour_of_day ASC;
```

## Query: Pola Per Hari

```sql
SELECT
    FORMAT_DATE('%A', DATE(pickup_timestamp)) AS day_of_week,
    COUNT(*) AS total_trips,
    AVG(fare_amount) AS avg_fare
FROM `ojolboosttrack2.zone_demand_history`
WHERE
    DATE(pickup_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
GROUP BY day_of_week
ORDER BY total_trips DESC;
```

## Interpretasi Pola Jam

| Jam | Kategori | Konteks |
|---|---|---|
| 06:00-09:00 | Morning Rush | Komuter pagi, zona perkantoran/stasiun |
| 11:30-13:00 | Lunch Peak | Food delivery naik signifikan |
| 16:30-19:30 | Evening Rush | Jam pulang kerja, zona perumahan |
| 19:30-22:00 | Dinner Window | Food & entertainment area |
| 22:00-06:00 | Low Demand | Hindari kecuali zona hiburan malam |

## Output Format

```json
{
  "zone_name": "Sudirman",
  "peak_hours": [7, 8, 17, 18],
  "low_hours": [2, 3, 4, 5],
  "best_day": "Jumat",
  "avg_fare_peak": 35000,
  "trend_summary": "Demand naik 23% pada jam 17-18 dibandingkan minggu lalu"
}
```
