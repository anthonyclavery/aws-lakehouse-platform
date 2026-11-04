SELECT customer_id, local_month,
       CAST(consumption_kwh AS varchar) AS consumption_kwh,
       CAST(energy_cost_eur AS varchar) AS energy_cost_eur
FROM "{database}"."monthly_customer_energy"
WHERE local_month >= '{start}' AND local_month < '{end}'
{customer_filter}
ORDER BY local_month, customer_id
