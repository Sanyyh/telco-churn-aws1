SELECT contract, COUNT(*) AS total_customers,
       SUM(CASE WHEN churn = 'Yes' THEN 1 ELSE 0 END) AS churned_customers,
       ROUND(100.0 * SUM(CASE WHEN churn = 'Yes' THEN 1 ELSE 0 END) / COUNT(*), 2) AS churn_rate_percent
FROM customers GROUP BY contract ORDER BY churn_rate_percent DESC;