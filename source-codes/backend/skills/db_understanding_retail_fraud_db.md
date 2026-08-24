# Database Understanding — retail_fraud_db

## Summary
## Findings

This database supports **retail banking fraud and credit risk analysis** across three tables—**customers**, **retail_accounts**, and **transactions**—with a total of 250,000 records. The data covers **25,000 unique customers**, each with detailed demographic and financial attributes, linked to one or more accounts and multiple transactions.

Data completeness is excellent, with **zero nulls** in all key identifiers and nearly all fields; only **retail_accounts.balance** shows a **7% null rate**, which may impact exposure calculations. Numeric fields such as **income** (mean 49,240, range 8,000–242,900), **credit_limit** (mean 14,720, range 1,381–87,220), and **transactions.amount** (mean 78.33, range 1–7,590) exhibit wide ranges and notable **extreme outliers** (e.g., 0.5% in income, 0.6% in credit_limit, 4% in transaction amounts), which could skew risk or fraud models if not addressed.

## Potential Usages

This schema enables robust credit risk modelling (e.g., IFRS 9 staging, default prediction, exposure analytics) and fraud detection (transaction-level flagging, anomaly and propensity scoring, AML investigation) by leveraging linked customer demographics, account behaviour, and transactional patterns. Its completeness and granularity support advanced portfolio analytics, scorecard development, and regulatory reporting across segments, channels, and regions. Key fields such as bureau_score, utilization, and fraud_flag facilitate these analyses, but careful handling of outliers and the 7% null rate in retail_accounts.balance is essential for reliable exposure and impairment calculations.

_Further manual inputs from the analyst highlight a structured data dictionary covering 32 field descriptions — these contextual signals have been factored into the understanding above._

## Key Anomalies
- [Info] customers.income — extreme_outliers (n=136, 0.5%) (Outliers, Extreme Values & Plausibility)
- [Info] retail_accounts.credit_limit — extreme_outliers (n=154, 0.6%) (Outliers, Extreme Values & Plausibility)
- [Info] retail_accounts.balance — extreme_outliers (n=265, 1.1%) (Outliers, Extreme Values & Plausibility)
- [Info] retail_accounts.dpd_count — extreme_outliers (n=5, 0.0%) (Outliers, Extreme Values & Plausibility)
- [Info] transactions.amount — extreme_outliers (n=8001, 4.0%) (Outliers, Extreme Values & Plausibility)

## Suggested Test Hypotheses
- The distribution of income in the customers table contains extreme outliers that may distort model training, risk segmentation, and portfolio representativeness. [Outliers, Extreme Values & Plausibility · Critical · IQR outlier]
- The distribution of transaction amounts in the transactions table contains a material tail population of extreme values that may indicate data entry errors, fraud, or atypical customer behavior. [Outliers, Extreme Values & Plausibility · Critical · Tail analysis]
- The balance field in retail_accounts has a 7% missing rate, which may not be missing completely at random and could introduce bias in exposure or loss calculations. [Missingness Mechanism & Data Availability · Critical · MCAR missingness]
- The default_flag target in retail_accounts is stable and consistent over time, with no unexplained structural breaks or shifts in default rates by account vintage. [Target Definition & Label Consistency · Critical · Structural break analysis]
- The population distribution by region and channel in both customers and retail_accounts is stable, with no material selection bias between the two tables. [Portfolio Representativeness & Selection Bias · Critical · PSI]
- The bureau_score feature in retail_accounts remains stable across different time periods, indicating consistent credit risk assessment standards. [Feature Stability & Behavioral Consistency · Critical · Feature drift analysis]