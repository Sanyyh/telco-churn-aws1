SELECT CASE WHEN tenure <= 12 THEN '0-12 months'
            WHEN tenure <= 24 THEN '13-24 months'
            ELSE '25+ months' END AS tenure_group,
       COUNT(*) AS total_customers,
       ROUND(100.0 * SUM(CASE WHEN churn = 'Yes' THEN 1 ELSE 0 END) / COUNT(*), 2) AS churn_rate_percent
FROM customers
GROUP BY CASE WHEN tenure <= 12 THEN '0-12 months'
              WHEN tenure <= 24 THEN '13-24 months'
              ELSE '25+ months' END
ORDER BY tenure_group;