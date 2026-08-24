"""Plan 3 / D4.2 — seed mock Monitoring IDs with history for the dashboard."""
from __future__ import annotations


def _series(start, vals, threshold):
    out = []
    y, m = start
    for v in vals:
        out.append({"period": f"{y:04d}-{m:02d}", "value": round(v, 4),
                    "status": "FAIL" if v > threshold else "PASS"})
        m += 1
        if m > 12:
            m = 1; y += 1
    return out


_MONITORS = [
    {
        "monitoring_id": "MON-001", "logical_db": "retail_risk_db",
        "table": "retail_accounts", "fields": ["bureau_score"], "test_id": "psi",
        "frequency": "Monthly", "run_started": "2019-07", "last_run": "2020-06",
        "history": _series((2019, 7), [0.05, 0.08, 0.12, 0.17, 0.21, 0.26, 0.29,
                                       0.31, 0.28, 0.30, 0.33, 0.29], 0.20),
        "actions": [{"period": "2019-12", "action": "Ticket IDQ raised (bureau recalibration)"}],
    },
    {
        "monitoring_id": "MON-002", "logical_db": "retail_fraud_db",
        "table": "transactions", "fields": ["amount"], "test_id": "psi",
        "frequency": "Monthly", "run_started": "2020-01", "last_run": "2020-12",
        "history": _series((2020, 1), [0.06, 0.09, 0.15, 0.19, 0.22, 0.24, 0.21,
                                       0.20, 0.23, 0.25, 0.22, 0.21], 0.20),
        "actions": [],
    },
    {
        "monitoring_id": "MON-003", "logical_db": "retail_risk_db",
        "table": "retail_accounts", "fields": ["balance"], "test_id": "missing",
        "frequency": "Monthly", "run_started": "2018-06", "last_run": "2018-12",
        "history": _series((2018, 6), [0.0, 0.02, 0.5, 1.0, 1.0, 1.0, 0.4], 0.01),
        "actions": [{"period": "2018-08", "action": "Online balance feed gap detected"}],
    },
    {
        "monitoring_id": "MON-004", "logical_db": "wholesale_irb_db",
        "table": "obligors", "fields": ["leverage"], "test_id": "psi",
        "frequency": "Quarterly", "run_started": "2021-03", "last_run": "2022-12",
        "history": _series((2021, 3), [0.01, 0.02, 0.015, 0.02, 0.018, 0.016,
                                       0.02, 0.017], 0.20),
        "actions": [],
    },
]


def load() -> int:
    """Fetch-first: only seed monitors whose logical_db is currently ingested,
    so the Monitoring dashboard stays empty until the user fetches the relevant
    database and fills as each DB comes online."""
    import system_db as s
    ingested = {r["logical_db"] for r in s.query("ingested_databases")}
    n = 0
    for m in _MONITORS:
        if m["logical_db"] not in ingested:
            continue
        s.upsert("monitoring", m)
        n += 1
    return n


if __name__ == "__main__":
    print(load(), "monitoring rows")
