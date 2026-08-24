"""Plan 8 / Aspect (d) — role-gated, schema-validated tool registry.

The agents' "hands". Each tool wraps an EXISTING engine (router/module) behind a
single typed surface that enforces three contracts the brainstorm's Delegation
Doctrine requires:

1. Role gating — which roles (``agent_key``) may call a tool is SYSTEM-OWNED here,
   not requested by a user .md. ``call`` raises ``ToolAccessError`` otherwise.
2. Schema-forced I/O — input args and output are validated against fixed schemas;
   malformed input is rejected, not coerced.
3. Metadata-only egress — a tool may never return raw rows / PII to the model.
   The egress contract (``metadata_only`` | ``id_only`` | ``score_only``) is
   asserted at the boundary, after the backing function runs.

Phase 2 / PLT-08 change: this module keeps its public API shape (``Tool``,
``register``, ``get_tool``, ``list_tools``, ``call``, ``ToolAccessError``,
``ToolEgressError``) but no longer keeps a parallel store of its own. Every
``register(tool)`` call wraps ``tool`` into an ``ai.test_kit.Helper`` (kind=
"tool", role/egress/schema metadata stashed in ``Helper.meta``) and registers
it in ``ai.test_kit`` — THE single governed catalogue. ``list_tools`` /
``get_tool`` / ``call`` all read back through ``ai.test_kit``; ``_REGISTRY``
below is a read-through alias onto ``ai.test_kit``'s own dict (the SAME object,
not a copy) kept only because ``ai/dict_ingest.py`` (preserved dead code,
Phase 3) does ``"parse_data_dictionary" in tr._REGISTRY`` for idempotent
re-registration — that call site is unchanged, so the alias must behave like a
plain dict of tool_id -> registered-thing.

This generalizes the partial pattern already in ``ai/rca_helpers.py`` (RCAHelper)
and ``ai/prompts.py`` (RCA_TOOLS). It does NOT reimplement any engine — every
``fn`` delegates to deterministic code that already exists.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

from ai import test_kit as _tk

EGRESS_CONTRACTS = {"metadata_only", "id_only", "score_only"}


class ToolAccessError(PermissionError):
    """Raised when a role calls a tool it is not granted."""


class ToolEgressError(RuntimeError):
    """Raised when a tool's output violates its declared egress contract."""


@dataclass(frozen=True)
class Tool:
    tool_id: str
    description: str
    param_schema: dict          # {"required": [...], "properties": {name: {"type": ...}}}
    output_schema: dict         # same shape; validated against the returned dict
    allowed_roles: tuple[str, ...]
    egress: str                 # one of EGRESS_CONTRACTS
    fn: Callable[..., dict]
    tags: tuple[str, ...] = field(default_factory=tuple)

    def public(self) -> dict:
        return {
            "tool_id": self.tool_id, "description": self.description,
            "param_schema": self.param_schema, "output_schema": self.output_schema,
            "allowed_roles": list(self.allowed_roles), "egress": self.egress,
            "tags": list(self.tags),
        }


# Keys that, if present in a metadata-only/score/id output, signal a raw-row leak.
_RAW_ROW_KEYS = {"rows", "records", "sample", "sample_rows", "data", "raw", "values"}
_PYTHON_JSON_TYPES = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "object": dict, "array": list,
}


def _tool_from_helper(helper: _tk.Helper) -> Tool:
    """Reconstruct a ``Tool`` from an ``ai.test_kit.Helper`` this module
    registered — ``helper.meta`` carries everything ``Tool`` needs."""
    m = helper.meta
    return Tool(
        tool_id=m["tool_id"], description=m["description"],
        param_schema=m["param_schema"], output_schema=m["output_schema"],
        allowed_roles=m["allowed_roles"], egress=m["egress"], fn=helper.fn,
        tags=m.get("tags", ()),
    )


def register(tool: Tool) -> Tool:
    """Wrap ``tool`` as an ``ai.test_kit.Helper`` and register it there — the
    single catalogue. Raises ``ValueError`` (from ``ai.test_kit.register``) if
    ``tool.tool_id`` is already registered, or if ``tool.egress`` is unknown."""
    if tool.egress not in EGRESS_CONTRACTS:
        raise ValueError(f"Unknown egress contract: {tool.egress!r}")
    helper = _tk.Helper(
        name=tool.tool_id,
        fn=tool.fn,
        purpose=tool.description,
        kind="tool",
        inputs=json.dumps(tool.param_schema),
        outputs=json.dumps(tool.output_schema),
        failure_modes=(
            "Raises ToolAccessError if the caller's role is not granted; "
            "ValueError on schema violation; ToolEgressError if the output "
            "carries a raw-row key or isn't JSON-safe."
        ),
        version="1.0",
        meta={
            "tool_id": tool.tool_id, "description": tool.description,
            "param_schema": tool.param_schema, "output_schema": tool.output_schema,
            "allowed_roles": tool.allowed_roles, "egress": tool.egress,
            "tags": tool.tags,
        },
    )
    _tk.register(helper)
    return tool


# No parallel store: `_REGISTRY` is a read-through alias onto
# `ai.test_kit._CATALOGUE` (the exact same dict object). Kept only so
# `ai/dict_ingest.py`'s `"parse_data_dictionary" in tr._REGISTRY` idempotency
# check (a pre-existing call site outside this phase's scope) keeps working
# unchanged. Do not reintroduce a second dict here.
_REGISTRY: dict[str, _tk.Helper] = _tk._CATALOGUE


def get_tool(tool_id: str, agent_key: str) -> Tool:
    """Resolve a tool for a role, enforcing the role grant."""
    helper = _tk.get_helper(tool_id)
    if helper is None or helper.kind != "tool":
        raise KeyError(f"Unknown tool: {tool_id}")
    tool = _tool_from_helper(helper)
    if (agent_key or "").lower() not in tool.allowed_roles:
        raise ToolAccessError(
            f"Role {agent_key!r} is not granted tool {tool_id!r} "
            f"(allowed: {', '.join(tool.allowed_roles)})")
    return tool


def list_tools(agent_key: str | None = None) -> list[dict]:
    """Public catalog of tool_registry-registered tools (``ai.test_kit``
    entries of kind "tool"), optionally filtered to a role's grants."""
    tools = [_tool_from_helper(h) for h in _tk.list_helpers() if h.kind == "tool"]
    if agent_key:
        ak = agent_key.lower()
        tools = [t for t in tools if ak in t.allowed_roles]
    return [t.public() for t in tools]


def _validate(payload: dict, schema: dict, what: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"{what} must be an object, got {type(payload).__name__}")
    missing = [k for k in schema.get("required", []) if k not in payload]
    if missing:
        raise ValueError(f"{what} missing required keys: {missing}")
    props = schema.get("properties", {})
    for key, spec in props.items():
        if key not in payload or payload[key] is None:
            continue
        want = spec.get("type")
        py = _PYTHON_JSON_TYPES.get(want)
        if py and not isinstance(payload[key], py):
            raise ValueError(f"{what} key {key!r} must be {want}")


def _assert_egress(tool: Tool, output: dict) -> None:
    """Enforce the egress contract: no raw rows / PII escape to the model."""
    leak = _RAW_ROW_KEYS.intersection(output.keys())
    if leak:
        raise ToolEgressError(
            f"Tool {tool.tool_id!r} ({tool.egress}) returned forbidden raw-row "
            f"key(s): {sorted(leak)}")
    # Must be JSON-serializable scalars/containers — never a DataFrame/ndarray.
    try:
        json.dumps(output)
    except (TypeError, ValueError) as exc:
        raise ToolEgressError(
            f"Tool {tool.tool_id!r} output is not metadata-only / JSON-safe: {exc}")


def call(tool_id: str, agent_key: str, args: dict | None = None) -> dict:
    """Validated, role-gated, egress-checked invocation. Invokes through
    ``ai.test_kit.call`` (so this call is logged through the one logging
    helper too, with ``context_id=agent_key``)."""
    tool = get_tool(tool_id, agent_key)
    args = dict(args or {})
    _validate(args, tool.param_schema, f"{tool_id} input")
    out = _tk.call(tool_id, context_id=agent_key, **args)
    _validate(out, tool.output_schema, f"{tool_id} output")
    _assert_egress(tool, out)
    return out


# ---------------------------------------------------------------------------
# Backing functions — thin wrappers over existing engines (lazy imports keep
# this module importable without FastAPI / a DB / an LLM key at boot).
# ---------------------------------------------------------------------------

def _fetch_schema_stats(logical_db: str, table: str | None = None) -> dict:
    """Metadata-only profile: per-table row/col counts + column name/dtype/
    description from ``system_db.table_metadata``. NEVER returns rows.

    Historical note (RET-05, 0.5.0) — before 0.4.0 Phase 2 this read from
    ``routers.ingestion.record_counts`` and ``database.load_table``, a router
    and a warehouse loader that no longer exist (both deleted at 0.2.0; see
    docs/0.4.0/08-helper-layer.md §3 for the original root-cause fix). It now
    reads ``system_db.table_metadata`` instead: static per-table/column
    metadata seeded via ``system_db.py``'s ``query()`` idiom and the ``seeds``
    package that populates it — no live per-column null%/n_distinct scan,
    since there is no longer a single fixed warehouse loader to scan through
    safely here. Verified 3 Aug 2026: no live import or call of
    ``routers.ingestion`` exists anywhere in ``backend/`` — this docstring is
    the only remaining mention, and is historical only.
    """
    import system_db as s

    rows = s.query("table_metadata", logical_db=logical_db)
    if table:
        rows = [r for r in rows if r.get("table") == table]
    per_table_counts = {r["table"]: r.get("row_count", 0) for r in rows}
    columns: dict[str, list[dict]] = {}
    for r in rows:
        cols = r.get("columns") or []
        dtypes = r.get("datatypes") or {}
        descriptions = r.get("descriptions") or {}
        if isinstance(dtypes, list):
            dtypes = dict(zip(cols, dtypes))
        if isinstance(descriptions, list):
            descriptions = dict(zip(cols, descriptions))
        columns[r["table"]] = [
            {"column": str(c), "dtype": str(dtypes.get(c, "")),
             "description": str(descriptions.get(c, ""))}
            for c in cols
        ]
    return {
        "logical_db": logical_db,
        "per_table_counts": per_table_counts,
        "total_rows": sum(per_table_counts.values()),
        "columns": columns,
    }


def _calculate_health_score(results: list) -> dict:
    """Wrap scoring.health.final_score. LLM passes tags; Python returns the number."""
    from scoring.health import final_score
    return final_score(list(results or []))


def _calculate_criticality(ranking: list) -> dict:
    """Wrap scoring.criticality.assign + framework + validate (deterministic)."""
    from scoring import criticality
    ids = [str(r) for r in (ranking or [])]
    assignment = criticality.assign(ids)
    return {
        "assignment": assignment,
        "framework": criticality.framework(len(ids)),
        "validate": criticality.validate(assignment),
    }


def _execute_sandboxed_code(code: str, table: str, extra: dict | None = None) -> dict:
    """Wrap code_sandbox.run; return metadata + traceback, NEVER raw sample rows.

    NOTE (Phase 2 / PLT-08 scope note, not fixed this phase): like the two
    lazy imports fixed above, ``database.load_table`` below no longer exists
    (``database.py`` was deleted at 0.2.0) — this function raises
    ``ModuleNotFoundError`` if actually called, exactly as it did before this
    phase. It is left as-is here because it is a lazy, function-scoped import
    (does not break importing this module — 2-T1 only requires clean module
    import) and was not one of the two defects this phase's task called out
    to root-cause; re-wiring it to the live per-item table store
    (``ai.v2.service._read_table``, which needs an ``item_id`` this tool's
    schema does not carry) is left for the same later pass as
    ``commit_test_logic``/``infer_relationships`` below.
    """
    from ai.code_sandbox import run as sandbox_run
    from database import load_table

    df = load_table(table)
    out = sandbox_run(code, df, extra=extra or {})
    # Egress: keep ok / a stringified result summary / stdout tail / traceback only.
    return {
        "ok": bool(out.get("ok")),
        "result_summary": str(out.get("result", out.get("error", "")))[:2000],
        "stdout_tail": (out.get("stdout") or "")[:2000],
        "traceback": out.get("traceback", ""),
    }


# ---------------------------------------------------------------------------
# Registry definitions (grouped by pipeline step + role grant + egress contract).
# parse_data_dictionary and repair_and_retry are registered by their own modules
# (Phase 3) to avoid import cycles; see ai/dict_ingest.py and ai/test_manager.py.
#
# Phase 2 / PLT-08: `raise_mitigation_ticket` was DELETED here (not merely
# fixed) — its dependency `routers/tickets.py` is unmounted dead code removed
# this phase, and its only consumer (`ai/test_manager.py`) is itself
# preserved-but-broken until Phase 3 deletes it (see docs/0.4.0/08-helper-layer
# .md). There is nothing left to call it and nothing for it to reach.
#
# DEFERRED (follow-up): `commit_test_logic` (wrap test_manager+system_db; gated on a
# challenger break condition) and `infer_relationships` (extract the Jaccard/name
# heuristic ERD fallback into a pure function, then wrap). Both require lifting
# orchestration state out of their call sites and are tracked for a later pass
# rather than half-built here.
# ---------------------------------------------------------------------------

register(Tool(
    tool_id="fetch_schema_stats",
    description="Row counts + per-column dtype/description for a logical DB. Metadata only.",
    param_schema={"required": ["logical_db"],
                  "properties": {"logical_db": {"type": "string"},
                                 "table": {"type": "string"}}},
    output_schema={"required": ["logical_db", "per_table_counts", "columns"],
                   "properties": {"total_rows": {"type": "integer"}}},
    allowed_roles=("newton", "gauss", "poincare"),
    egress="metadata_only",
    fn=_fetch_schema_stats,
    tags=("step1", "ingestion", "profiling"),
))

register(Tool(
    tool_id="calculate_health_score",
    description="Deterministic 0-100 health score from tagged test results.",
    param_schema={"required": ["results"],
                  "properties": {"results": {"type": "array"}}},
    output_schema={"required": ["final_score", "categories"],
                   "properties": {"active_categories": {"type": "integer"}}},
    allowed_roles=("gauss", "laplace"),
    egress="score_only",
    fn=_calculate_health_score,
    tags=("step3", "scoring"),
))

register(Tool(
    tool_id="calculate_criticality",
    description="Deterministic High/Medium/Low criticality from an importance ranking.",
    param_schema={"required": ["ranking"],
                  "properties": {"ranking": {"type": "array"}}},
    output_schema={"required": ["assignment", "framework"],
                   "properties": {}},
    allowed_roles=("pascal", "gauss"),
    egress="score_only",
    fn=_calculate_criticality,
    tags=("step3", "scoring"),
))

register(Tool(
    tool_id="execute_sandboxed_code",
    description="Run pandas/numpy/scipy code in the AST-guarded sandbox. Returns "
                "ok + result summary + stdout tail + traceback. Never raw rows.",
    param_schema={"required": ["code", "table"],
                  "properties": {"code": {"type": "string"},
                                 "table": {"type": "string"},
                                 "extra": {"type": "object"}}},
    output_schema={"required": ["ok"],
                   "properties": {"result_summary": {"type": "string"},
                                  "traceback": {"type": "string"}}},
    allowed_roles=("gauss", "feynman"),
    egress="metadata_only",
    fn=_execute_sandboxed_code,
    tags=("step4", "execution"),
))
