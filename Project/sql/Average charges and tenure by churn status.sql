SELECT churn, COUNT(*) AS customer_count,
       ROUND(AVG(monthlycharges), 2) AS average_monthly_charges,
       ROUND(AVG(tenure), 2) AS average_tenure
FROM customers GROUP BY churn;