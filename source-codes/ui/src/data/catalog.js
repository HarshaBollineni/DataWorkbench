// Test catalog mirrored from backend gx/suite_builder.py CATALOG.
// `id` values are the exact test IDs the backend /run-validations expects.
export const testCatalog = [
  { id: "missing",    name: "Missingness",                 l1_theme: "Data Completeness",                l2_area: "Missingness Mechanism",                  criticality: "High",     defaultSelected: true },
  { id: "uniqueness", name: "PK Uniqueness",               l1_theme: "Dataset Stability & Monitoring",   l2_area: "Pipeline Stability",                     criticality: "High",     defaultSelected: true },
  { id: "outlier",    name: "Outliers & Extreme Values",   l1_theme: "Data Completeness",                l2_area: "Outliers & Extreme Values",              criticality: "Medium",   defaultSelected: false },
  { id: "freshness",  name: "Freshness",                   l1_theme: "Dataset Stability & Monitoring",   l2_area: "Pipeline Stability",                     criticality: "Medium",   defaultSelected: true },
  { id: "psi",        name: "PSI (Population Stability Index)", l1_theme: "Dataset Stability & Monitoring", l2_area: "Dataset Drift & Degradation",          criticality: "Critical", defaultSelected: true },
  { id: "ks",         name: "KS by Segment",               l1_theme: "Representativeness",               l2_area: "Portfolio Representativeness",           criticality: "High",     defaultSelected: false },
  { id: "leakage",    name: "Post-Outcome Leakage",        l1_theme: "Temporal Integrity",              l2_area: "Temporal Integrity & Feature Leakage",   criticality: "Critical", defaultSelected: true },
  { id: "regime",     name: "Downturn Regime Coverage",    l1_theme: "Representativeness",               l2_area: "Downturn & Regime Coverage",             criticality: "High",     defaultSelected: false },
  { id: "vintage",    name: "Observation Window Depth",    l1_theme: "Representativeness",               l2_area: "Historical Depth & Observation Windows", criticality: "Medium",   defaultSelected: false },
  { id: "target",     name: "Target Rate Stability",       l1_theme: "Temporal Integrity",              l2_area: "Target Definition & Label Consistency",  criticality: "High",     defaultSelected: false },
];

export const criticalityVariant = (c) =>
  c === "Critical" ? "destructive" : c === "High" ? "warning" : "secondary";
