# Skill: Opportunity Cost Calculator
# Agent: Demand Analytics
# Trigger: Ketika menghitung kerugian dari waktu menganggur, "berapa rugi kalau ngetem di sini", atau optimasi idle time
# Description: Kalkulasi Opportunity Cost of Idle Time berdasarkan potensi pendapatan yang terlewat per zona
---

## Tujuan

Menghitung biaya peluang (Opportunity Cost of Idle Time) dari keputusan menunggu
di suatu zona dibandingkan zona lain, berdasarkan data historis rata-rata fare dan frekuensi trip.

## Formula

```
Opportunity Cost (per jam) = 
    (Avg Fare Zona Terbaik × Avg Trips/Jam Zona Terbaik) 
    - (Avg Fare Zona Saat Ini × Avg Trips/Jam Zona Saat Ini)
```

## Query Kalkulasi

```sql
WITH zone_metrics AS (
    SELECT
        zone_name,
        EXTRACT(HOUR FROM pickup_timestamp) AS hour_of_day,
        COUNT(*) / 30.0 AS avg_trips_per_day,
        AVG(fare_amount) AS avg_fare,
        (COUNT(*) / 30.0) * AVG(fare_amount) AS expected_hourly_yield
    FROM `ojolboosttrack2.zone_demand_history`
    WHERE
        DATE(pickup_timestamp) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
        AND EXTRACT(HOUR FROM pickup_timestamp) = @current_hour
    GROUP BY zone_name, hour_of_day
)
SELECT
    zone_name,
    expected_hourly_yield,
    expected_hourly_yield - MIN(expected_hourly_yield) OVER () AS opportunity_cost_diff
FROM zone_metrics
ORDER BY expected_hourly_yield DESC;
```

## Interpretasi & Komunikasi ke Pengguna (via Bang Jek)

- Jika opportunity cost > Rp 20.000/jam → "Bang, kamu lagi rugi sekitar Rp X/jam di sini. Worth it pindah!"
- Jika opportunity cost 10.000-20.000 → "Ada potensi lebih baik di [zona], tapi tidak terlalu signifikan."
- Jika opportunity cost < 10.000 → "Posisi sekarang sudah cukup optimal, Bang."

## Output Format

```json
{
  "current_zone": "Kemayoran",
  "best_zone": "Sudirman",
  "opportunity_cost_per_hour_idr": 22500,
  "recommendation": "Pindah ke Sudirman — potensi tambahan Rp 22.500/jam",
  "confidence": 0.78
}
```
