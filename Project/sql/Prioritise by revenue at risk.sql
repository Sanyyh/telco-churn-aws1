SELECT s.customerid,
       s.churn_probability,
       c.monthlycharges,
       ROUND(s.churn_probability * c.monthlycharges, 2) AS expected_monthly_loss,
       s.reason_1
FROM churn_db.scores s
JOIN churn_db.customers c ON s.customerid = c.customerid
WHERE s.risk_band IN ('High', 'Medium')
ORDER BY expected_monthly_loss DESC
LIMIT 20;