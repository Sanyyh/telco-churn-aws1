SELECT s.customerid,
       s.churn_probability,
       s.reason_1, s.reason_2, s.reason_3,
       c.contract, c.tenure, c.monthlycharges
FROM churn_db.scores s
JOIN churn_db.customers c ON s.customerid = c.customerid
WHERE s.risk_band = 'High'
ORDER BY s.churn_probability DESC
LIMIT 20;