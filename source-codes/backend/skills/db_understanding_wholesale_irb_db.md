# Database Understanding — wholesale_irb_db

## Summary
## Findings

This database supports **wholesale IRB (Internal Ratings-Based) credit risk analysis** and contains two tables with a combined **3,072 records**. The main table, **obligors**, holds detailed profiles for 3,000 unique obligors, each identified by **obligors.obligor_id**, with fields covering sector, country, credit rating, and financial metrics.

Data completeness is excellent, with **zero nulls** across all fields. Key identifiers like **obligors.obligor_id** and **macro_scenarios.month** are fully populated and unique. Numeric fields such as **obligors.revenue** and **macro_scenarios.gdp_growth** exhibit wide value ranges and notable **extreme outliers**—5.6% of obligors have unusually high revenues, and nearly 14% of macroeconomic records show outlier GDP growth and unemployment rates, likely reflecting economic shocks. Categorical fields like **obligors.sector** and **obligors.rating** have moderate cardinality, supporting robust segmentation.

## Potential Usages

This database enables comprehensive **wholesale IRB credit risk modelling** by linking obligor-level financials, ratings, and default outcomes to macroeconomic scenarios. It supports **regulatory stress testing** (including Basel III/IV IRB and IFRS 9 ECL), **portfolio segmentation** by sector and country, **default prediction**, and scenario-based reporting. The presence of outliers in financial and macroeconomic fields facilitates robust **economic capital** and **ICAAP analysis**, as well as scenario analysis for downturn regimes.

## Key Anomalies
- [Info] obligors.revenue — extreme_outliers (n=168, 5.6%) (Outliers, Extreme Values & Plausibility)
- [Info] macro_scenarios.gdp_growth — extreme_outliers (n=10, 13.9%) (Outliers, Extreme Values & Plausibility)
- [Info] macro_scenarios.unemployment — extreme_outliers (n=10, 13.9%) (Outliers, Extreme Values & Plausibility)

## Suggested Test Hypotheses
- The distribution of obligors.revenue contains a material tail population of extreme outliers that may distort portfolio risk estimates if not properly justified. [Outliers, Extreme Values & Plausibility · Critical · IQR outlier]
- The macro_scenarios.gdp_growth and macro_scenarios.unemployment columns exhibit extreme outliers during downturn periods, which may impact regime segmentation and stress-period coverage. [Downturn & Regime Coverage · Critical · Regime segmentation]
- The obligors.default_flag target variable maintains a stable low-default rate across reporting dates, with no unexplained structural breaks or trends. [Target Definition & Label Consistency · Critical · Trend analysis]
- The distribution of obligors.rating_num is stable across reporting periods, indicating no material drift in credit quality assignment or behavioral inconsistency. [Feature Stability & Behavioral Consistency · Critical · Feature drift analysis]
- The obligors table is representative of the portfolio's sector and country composition, with no material selection bias over time. [Portfolio Representativeness & Selection Bias · Critical · PSI]
- Critical financial features such as obligors.leverage and obligors.interest_coverage should be tested for non-random missingness, even though nulls are not observed, to confirm data availability mechanisms are robust. [Missingness Mechanism & Data Availability · Critical · MCAR missingness]