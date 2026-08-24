# Database Understanding — retail_risk_db

## Summary
## Findings

This database supports **retail credit risk analysis** and contains three tables with a total of 50,072 records. The core tables, **retail_accounts** and **customers**, each hold 25,000 rows, capturing account-level and customer-level details respectively. **retail_accounts** links to **customers** via **customer_id** (63% cardinality ratio, indicating some customers hold multiple accounts).

Data completeness is strong, with nearly all fields in **retail_accounts** and **customers** showing zero nulls, except for a 7% null rate in **retail_accounts.balance**. Key numeric fields like **bureau_score** (mean 686.4, range 395–900) and **income** (mean 49,240, range 8,000–242,900) display wide, realistic distributions, though **credit_limit**, **balance**, and **income** each have a small proportion of extreme outliers. Identifier fields such as **account_id** and **customer_id** are unique, supporting robust joins. Categorical fields like **product** and **segment** have low cardinality.

## Potential Usages

This database enables IFRS 9 ECL calculation, AIRB (Basel) risk modelling—including PD/LGD estimation—and credit decisioning analytics by linking account-level risk metrics (e.g., bureau_score, dpd_count, utilization, ifrs9_stage) with customer demographics and macroeconomic scenarios. It supports portfolio reporting, regulatory submissions, and stress testing by facilitating segmentation, migration analysis, and scenario-based impairment forecasting under IFRS 9 and Basel frameworks.

_Further manual inputs from the analyst highlight use-case alignment with AIRB and IFRS 9, model-family coverage spanning IFRS9 / CECL, IRB / Basel and Credit Decisioning, and a structured data dictionary covering 28 field descriptions — these contextual signals have been factored into the understanding above._

## Key Anomalies
- [Info] retail_accounts.credit_limit — extreme_outliers (n=154, 0.6%) (Outliers, Extreme Values & Plausibility)
- [Info] retail_accounts.balance — extreme_outliers (n=265, 1.1%) (Outliers, Extreme Values & Plausibility)
- [Info] retail_accounts.dpd_count — extreme_outliers (n=5, 0.0%) (Outliers, Extreme Values & Plausibility)
- [Info] customers.income — extreme_outliers (n=136, 0.5%) (Outliers, Extreme Values & Plausibility)
- [Info] macro_scenarios.gdp_growth — extreme_outliers (n=10, 13.9%) (Outliers, Extreme Values & Plausibility)
- [Info] macro_scenarios.unemployment — extreme_outliers (n=10, 13.9%) (Outliers, Extreme Values & Plausibility)

## Suggested Test Hypotheses
- The null pattern in retail_accounts.balance is not systematically associated with default_flag, product, or region, supporting the assumption of missing at random (MAR) or missing completely at random (MCAR) for this key exposure feature. [Missingness Mechanism & Data Availability · Critical · MCAR missingness]
- The downturn regime is adequately represented in macro_scenarios, with at least ten months labeled as downturn and these periods exhibiting negative GDP growth and elevated unemployment, ensuring robust stress-period coverage. [Downturn & Regime Coverage · Critical · Stress-period coverage analysis]
- The extreme outliers detected in retail_accounts.credit_limit and customers.income are plausible, do not cluster disproportionately in recent vintages, and do not materially distort model feature stability or monotonicity. [Outliers, Extreme Values & Plausibility · High · IQR outlier analysis by vintage]
- The distribution of bureau_score in retail_accounts is stable over time, with no material drift or selection bias between recent and older account vintages, as measured by PSI between vintage cohorts. [Portfolio Representativeness & Selection Bias · Critical · PSI]
- There are no unexplained structural breaks or trends in the time series of default_flag and ifrs9_stage rates in retail_accounts, ensuring target label consistency and supporting regulatory compliance. [Target Definition & Label Consistency · Critical · Structural break analysis, Trend analysis]
- Feature drift and monotonicity in retail_accounts.utilization are within acceptable thresholds across vintage cohorts, supporting behavioral consistency and model reliability for IRB and IFRS 9 models. [Feature Stability & Behavioral Consistency · Critical · Feature drift analysis, Monotonicity check]