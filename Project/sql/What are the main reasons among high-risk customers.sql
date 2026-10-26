SELECT reason_1 AS main_reason,
       COUNT(*) AS customers
FROM churn_db.scores
WHERE risk_band = 'High'
GROUP BY reason_1
ORDER BY customers DESC
LIMIT 10;