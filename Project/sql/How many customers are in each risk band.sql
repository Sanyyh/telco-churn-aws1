SELECT risk_band,
       COUNT(*) AS customers,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS share_percent
FROM churn_db.scores
GROUP BY risk_band
ORDER BY customers DESC;