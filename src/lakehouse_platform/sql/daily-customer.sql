SELECT customer_id, local_date,
       CAST(consumption_kwh AS varchar) AS consumption_kwh,
       CAST(energy_cost_eur AS varchar) AS energy_cost_eur
FROM "{database}"."daily_customer_energy"
WHERE local_date >= DATE '{start}' AND local_date < DATE '{end}'
{customer_filter}
ORDER BY local_date, customer_id
