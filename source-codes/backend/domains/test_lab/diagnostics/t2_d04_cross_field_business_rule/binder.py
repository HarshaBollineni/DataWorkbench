"""KB-08/KB-09 — the rule -> primitive binder (signature match only).

Phase 5 parses a rule table into ``kb_rules`` rows and stops at
``binding_status='reference-only'``: it never assigns ``'bound'``, because
"never execute a predicate recovered from prose" (KB-09) means a rule only
becomes executable when its *structure* matches a registered primitive's
*signature*. That match is this module's whole job.

Rules of engagement, all of them conservative on purpose:

* **Signature match only.** A small set of GENERIC patterns (comparison,
  range, set, date order, identity formula, IF/THEN) is applied to the
  rule's own ``rule_type`` + ``semantic_roles_json`` + ``rule_text``. There
  is no per-rule-id branch anywhere in this file, and none may be added.
* **No guessing.** A rule whose text does not match a pattern stays
  ``reference-only`` with a stated reason. An unbindable rule is a visible,
  explainable gap — never a wrong binding.
* **A parse hazard blocks binding outright (KB-11).** If Phase 5 flagged a
  collapsed/ambiguous role name on the rule, it is skipped regardless of
  whether the text would otherwise match: the role identity itself is not
  yet trustworthy, so neither is any predicate over it.
* **No code is generated or evaluated.** The output is a JSON params
  document naming a registered primitive and its arguments. There is no
  ``eval``/``exec``/``compile`` on this path (6-T13).

Role spellings: a role may appear in the rule text in a normalized-form
variant of its recorded name (``exposureatdefault`` in the Roles cell vs
``exposureat_default`` in the Rule cell — a PDF-extraction artefact,
KB-11). Aliases are recovered structurally (same alphanumeric collapse,
different literal text) and recorded, which is also what feeds the role
resolver's 0.92 synonym tier (DX-04).
"""
from __future__ import annotations

import re
from typing import Any

import system_db as s

from .rules import RULE_TYPES

PARSER_VERSION = "generic-v1"

_NUM = r"[-+]?\d+(?:\.\d+)?"
_NUM_RE = re.compile(rf"^{_NUM}$")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_TRAILING_NOTE_RE = re.compile(r"\s*\((?!\s*\+/-)[^()]*\)\s*[_\s]*$")
_EXCEPTION_CLAUSE_RE = re.compile(r"\(\s*exceptions?\s*:\s*([^()]*)\)", re.IGNORECASE)
_OPS = ("<=", ">=", "==", "!=", "<", ">")


class BinderRefusal(Exception):
    """A rule that cannot be bound honestly. Carries the stated reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
#  Small structural helpers
# ---------------------------------------------------------------------------

def _collapse(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _parse_value(raw: str) -> Any:
    token = (raw or "").strip().strip("'\"")
    if _NUM_RE.match(token):
        return int(token) if re.match(r"^[-+]?\d+$", token) else float(token)
    return token


def _strip_note(text: str) -> str:
    """Drop trailing explanatory parentheticals (``(logical bound)``) but
    never a tolerance clause (``(+/- 0.5)``), and never anything mid-text."""
    out = (text or "").strip()
    while True:
        stripped = _TRAILING_NOTE_RE.sub("", out).strip()
        if stripped == out:
            return out.rstrip("_ ").strip()
        out = stripped


def _alias_map(roles: list[str], text: str) -> dict[str, str]:
    """``{spelling: canonical_role}`` for every recorded spelling of a role.

    A word in the rule text whose alphanumeric collapse equals a role's is
    the same role written differently — the KB's own record of a
    normalized-form variant (DX-04 tier 2).
    """
    by_collapse = {_collapse(r): r for r in roles if r}
    aliases = {r: r for r in roles if r}
    for word in _WORD_RE.findall(text or ""):
        role = by_collapse.get(_collapse(word))
        if role and word not in aliases:
            aliases[word] = role
    return aliases


def _role_pattern(aliases: dict[str, str]) -> str:
    ordered = sorted(aliases, key=len, reverse=True)
    return "(?:" + "|".join(re.escape(a) for a in ordered) + ")"


def _canonical(alias: str, aliases: dict[str, str]) -> str:
    return aliases.get(alias, alias)


# ---------------------------------------------------------------------------
#  dtype inference (DX-04 clause 4) — structural, from the rule's own usage
# ---------------------------------------------------------------------------

def _set_kind(values: list[Any]) -> str:
    numeric = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if numeric and len(numeric) == len(values):
        if set(numeric) <= {0, 1}:
            return "flag"
        return "number"
    return "category"


def _dtypes_for(spec: dict[str, Any]) -> dict[str, str]:
    """What structural type does THIS primitive call imply for its roles?"""
    p = spec.get("primitive")
    out: dict[str, str] = {}
    if p == "dateorder":
        out[spec["earlier"]] = "date"
        out[spec["later"]] = "date"
    elif p == "identity":
        out[spec["target"]] = "number"
        for role in spec.get("operands", []):
            out[role] = "number"
    elif p == "ineq":
        out[spec["left"]] = "number"
        if spec.get("right_role"):
            out[spec["right_role"]] = "number"
    elif p == "dom_range":
        out[spec["col"]] = "number"
    elif p == "dom_set":
        out[spec["col"]] = _set_kind(list(spec.get("allowed", [])))
    elif p == "in_cond":
        out[spec["col"]] = _set_kind(list(spec.get("values", [])))
    elif p == "eq_cond":
        out[spec["col"]] = "number" if spec.get("numeric") else "category"
    elif p == "present_number":
        out[spec["col"]] = "number"
    # 'presence' claims nothing: populated-ness is type-agnostic.
    return out


# ---------------------------------------------------------------------------
#  Generic clause parsers
# ---------------------------------------------------------------------------

def _parse_comparison(clause: str, aliases: dict[str, str], *,
                      allow_scaled: bool = True) -> dict[str, Any] | None:
    """``<role> <op> <role|number>[ <*|+|-|/> <number>]`` -> an ``ineq`` spec."""
    rp = _role_pattern(aliases)
    ops = "|".join(re.escape(o) for o in _OPS) + "|="
    pattern = rf"^({rp})\s*({ops})\s*({rp}|{_NUM})(?:\s*([*+\-/])\s*({_NUM}))?$"
    m = re.match(pattern, clause.strip(), re.IGNORECASE)
    if not m:
        return None
    left, op, right, scale_op, scale_num = m.groups()
    if op == "=":
        op = "=="
    spec: dict[str, Any] = {"primitive": "ineq", "left": _canonical(left, aliases), "op": op}
    if _NUM_RE.match(right):
        spec["right_literal"] = float(right)
    else:
        spec["right_role"] = _canonical(right, aliases)
    if scale_op:
        if not allow_scaled:
            return None
        value = float(scale_num)
        if scale_op == "*":
            spec["factor"] = value
        elif scale_op == "/":
            if value == 0:
                return None
            spec["factor"] = 1.0 / value
        elif scale_op == "+":
            spec["offset"] = value
        elif scale_op == "-":
            spec["offset"] = -value
    return spec


def _parse_range(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    """``<role> in [lo, hi]`` -> ``dom_range``."""
    rp = _role_pattern(aliases)
    m = re.match(rf"^({rp})\s+in\s*\[\s*({_NUM})\s*,\s*({_NUM})\s*\]$", clause.strip(), re.IGNORECASE)
    if not m:
        return None
    return {"primitive": "dom_range", "col": _canonical(m.group(1), aliases),
            "lo": float(m.group(2)), "hi": float(m.group(3))}


def _parse_set(clause: str, aliases: dict[str, str], primitive: str) -> dict[str, Any] | None:
    """``<role> in {a, b, c}`` -> ``dom_set`` (expression) / ``in_cond`` (condition)."""
    rp = _role_pattern(aliases)
    m = re.match(rf"^({rp})\s+in\s*\{{([^{{}}]*)\}}$", clause.strip(), re.IGNORECASE)
    if not m:
        return None
    members = [_parse_value(x) for x in m.group(2).split(",") if x.strip()]
    if not members:
        return None
    key = "allowed" if primitive == "dom_set" else "values"
    return {"primitive": primitive, "col": _canonical(m.group(1), aliases), key: members}


def _parse_dateorder(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    rp = _role_pattern(aliases)
    m = re.match(rf"^({rp})\s*(<=|<)\s*({rp})$", clause.strip(), re.IGNORECASE)
    if not m:
        return None
    return {"primitive": "dateorder", "earlier": _canonical(m.group(1), aliases),
            "later": _canonical(m.group(3), aliases), "allow_equal": m.group(2) == "<="}


def _parse_identity(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    """``<target> == <a> <op> <b> [* factor] (+/- tol)`` -> ``identity``."""
    rp = _role_pattern(aliases)
    pattern = (rf"^({rp})\s*==\s*({rp})(?:\s*([*/+\-])\s*({rp}))?"
               rf"(?:\s*\*\s*({_NUM}))?\s*\(\s*\+/-\s*({_NUM})\s*\)$")
    m = re.match(pattern, clause.strip(), re.IGNORECASE)
    if not m:
        return None
    target, first, operator, second, factor, tol = m.groups()
    operands = [_canonical(first, aliases)]
    if second:
        operands.append(_canonical(second, aliases))
    return {"primitive": "identity", "target": _canonical(target, aliases),
            "operands": operands, "operator": operator,
            "factor": float(factor) if factor else 1.0,
            "tolerance": float(tol)}


def _parse_presence(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    """``<role> populated`` -> ``presence``; ``<role> present`` ->
    ``present_number`` — S5 draws exactly this distinction between the two
    wordings, and the binder keeps it rather than collapsing them."""
    rp = _role_pattern(aliases)
    m = re.match(rf"^({rp})\s+(populated|present)$", clause.strip(), re.IGNORECASE)
    if not m:
        return None
    role = _canonical(m.group(1), aliases)
    if m.group(2).lower() == "present":
        return {"primitive": "present_number", "col": role}
    return {"primitive": "presence", "cols": [role], "must": True}


def _parse_condition(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    """The IF half. Supported: ``role = value`` · ``role in {…}`` ·
    ``role <op> number``. Anything else (a bare adjective such as
    "IF cured", a slash-list of states) is deliberately unsupported."""
    text = _strip_note(clause)
    in_set = _parse_set(text, aliases, "in_cond")
    if in_set:
        return in_set
    comparison = _parse_comparison(text, aliases, allow_scaled=False)
    if comparison:
        if comparison["op"] == "==" and "right_literal" in comparison:
            value = comparison["right_literal"]
            whole = int(value) if float(value).is_integer() else value
            return {"primitive": "eq_cond", "col": comparison["left"],
                    "value": whole, "numeric": True}
        if comparison["op"] == "==" and "right_role" in comparison:
            return None  # role == role is a comparison, not a scope selector
        return comparison
    rp = _role_pattern(aliases)
    m = re.match(rf"^({rp})\s*=\s*(\S+)$", text, re.IGNORECASE)
    if m:
        return {"primitive": "eq_cond", "col": _canonical(m.group(1), aliases),
                "value": _parse_value(m.group(2)), "numeric": False}
    return None


def _parse_expression(clause: str, aliases: dict[str, str]) -> dict[str, Any] | None:
    """The THEN half (and the whole clause for a non-conditional rule)."""
    text = _strip_note(clause)
    for parser in (_parse_presence,):
        spec = parser(text, aliases)
        if spec:
            return spec
    spec = _parse_range(text, aliases)
    if spec:
        return spec
    spec = _parse_set(text, aliases, "dom_set")
    if spec:
        return spec
    return _parse_comparison(text, aliases)


def _parse_exceptions(rule_text: str, aliases: dict[str, str]) -> list[dict[str, Any]]:
    """``(exceptions: role=value[, role=value])`` -> equality exceptions
    applied BEFORE a row counts as a violation (CFR-07)."""
    out: list[dict[str, Any]] = []
    for clause in _EXCEPTION_CLAUSE_RE.findall(rule_text or ""):
        rp = _role_pattern(aliases)
        for m in re.finditer(rf"({rp})\s*=\s*([^,;]+)", clause, re.IGNORECASE):
            out.append({"role": _canonical(m.group(1), aliases), "value": _parse_value(m.group(2))})
    return out


# ---------------------------------------------------------------------------
#  The binder
# ---------------------------------------------------------------------------

def propose_binding(rule_row: dict[str, Any]) -> dict[str, Any]:
    """Match ONE ``kb_rules`` row to a registered primitive.

    Returns ``{"primitive": ..., "params": {...}}``. Raises
    :class:`BinderRefusal` with a stated reason for anything that cannot be
    bound honestly — the caller records the reason and leaves the rule
    ``reference-only``.
    """
    if rule_row.get("parse_hazards_json"):
        raise BinderRefusal(
            "parse hazard on this rule's roles — the role identity is not confirmed (KB-11), "
            "so no predicate over it may be bound")
    rule_type = rule_row.get("rule_type")
    if rule_type not in RULE_TYPES:
        raise BinderRefusal(f"no structured rule_type recovered (got {rule_type!r})")
    roles = [r for r in (rule_row.get("semantic_roles_json") or []) if r]
    if not roles:
        raise BinderRefusal("no semantic roles recorded on the rule")
    text = (rule_row.get("rule_text") or "").strip()
    if not text:
        raise BinderRefusal("no rule text to match against")

    aliases = _alias_map(roles, text)
    exceptions = _parse_exceptions(text, aliases)
    body = _EXCEPTION_CLAUSE_RE.sub("", text).strip().rstrip("_ ").strip()

    condition: dict[str, Any] | None = None
    matched: str
    if rule_type == "conditional":
        m = re.match(r"^\s*IF\s+(?P<cond>.+?)\s+THEN\s+(?P<then>.+?)\s*$", body, re.IGNORECASE)
        if not m:
            raise BinderRefusal("conditional rule text is not in 'IF <condition> THEN <consequent>' form")
        condition = _parse_condition(m.group("cond"), aliases)
        if condition is None:
            raise BinderRefusal(
                "IF-condition is not a supported form (supported: 'role = value', "
                f"'role in {{...}}', 'role <op> number'); got {m.group('cond').strip()!r}")
        expression = _parse_expression(m.group("then"), aliases)
        if expression is None:
            raise BinderRefusal(
                "THEN-consequent is not a supported form (supported: 'role populated', "
                "'role present', 'role <op> role|number', 'role in {...}', 'role in [lo, hi]'); "
                f"got {m.group('then').strip()!r}")
        matched = "conditional:if-then"
    elif rule_type == "date_ordering":
        expression = _parse_dateorder(_strip_note(body), aliases)
        if expression is None:
            raise BinderRefusal("date-ordering rule text is not in 'earlier <= later' form")
        matched = "date_ordering:order"
    elif rule_type == "identity":
        expression = _parse_identity(body, aliases)
        if expression is None:
            raise BinderRefusal(
                "identity rule text is not in 'target == a <op> b [* factor] (+/- tol)' form")
        matched = "identity:formula"
    else:  # inequality | domain
        # Generic domain/inequality rules commonly carry a trailing prose
        # note after an otherwise executable comparison.  Normalize only the
        # whole non-conditional expression here: conditional clauses retain
        # their own IF/THEN parsing and meaningful parenthesized syntax.
        expression = _parse_expression(_strip_note(body), aliases)
        if expression is None:
            raise BinderRefusal(
                f"{rule_type} rule text matched no generic pattern "
                "(comparison, 'in [lo, hi]', 'in {a, b}')")
        # A domain bound written as an inclusive comparison against a number
        # is exactly a one-sided dom_range; a STRICT comparison is not, and
        # is kept as the inequality it literally is rather than nudged to an
        # adjacent integer the rule never stated.
        if (rule_type == "domain" and expression["primitive"] == "ineq"
                and "right_literal" in expression and expression["op"] in (">=", "<=")
                and expression.get("factor", 1.0) == 1.0 and expression.get("offset", 0.0) == 0.0):
            bound = expression["right_literal"]
            expression = {"primitive": "dom_range", "col": expression["left"],
                          "lo": bound if expression["op"] == ">=" else None,
                          "hi": bound if expression["op"] == "<=" else None}
        matched = f"{rule_type}:{expression['primitive']}"

    referenced = set()
    for spec in (condition, expression):
        if spec:
            referenced |= set(_dtypes_for(spec))
            for key in ("left", "right_role", "col", "target", "earlier", "later"):
                if spec.get(key):
                    referenced.add(spec[key])
            referenced |= set(spec.get("cols", []) or [])
            referenced |= set(spec.get("operands", []) or [])
    unknown = sorted(referenced - set(roles))
    if unknown:
        raise BinderRefusal(f"rule text references role(s) not declared on the rule: {unknown}")

    role_dtypes: dict[str, str] = {}
    for spec in (condition, expression):
        if spec:
            role_dtypes.update(_dtypes_for(spec))

    params = {
        "primitive": expression["primitive"],
        "expression": expression,
        "condition": condition,
        "roles": roles,
        "roles_used": sorted(referenced),
        "role_aliases": {a: r for a, r in sorted(aliases.items()) if a != r},
        "role_dtypes": role_dtypes,
        "exceptions": exceptions,
        "parser": PARSER_VERSION,
        "matched_pattern": matched,
    }
    return {"primitive": expression["primitive"], "params": params}


_SYSTEM_DIAGNOSTIC_PRIMITIVES = {
    "row_completeness": {
        "key_assignability", "panel_calendar_continuity", "period_level_coverage",
        "duplicate_facility_period_pairs", "facility_period_coverage",
        "segment_period_coverage",
    },
}


def bind_registered_rule(rule_id: str, primitive: str, params: dict[str, Any], actor: str,
                         *, registry: str = "cross_field") -> dict[str, Any]:
    """Persist a binding: ``binding_status='bound'`` + primitive + params.

    The ONLY writer of ``binding_status='bound'`` in the product. Writes an
    append-only ``transaction_log`` row (PLT-05) so every rule that became
    executable is attributable.
    """
    from .rules import PRIMITIVES
    allowed = set(PRIMITIVES) if registry == "cross_field" else _SYSTEM_DIAGNOSTIC_PRIMITIVES.get(registry, set())
    if primitive not in allowed:
        raise ValueError(f"not a registered primitive: {primitive!r}")
    rule = s.query_one("kb_rules", rule_id=rule_id)
    if not rule:
        raise KeyError(f"Unknown knowledge rule: {rule_id}")
    if rule.get("parse_hazards_json"):
        raise ValueError("cannot bind a rule carrying an unresolved parse hazard (KB-11)")
    now = s.now_ist()
    s.update("kb_rules", {"rule_id": rule_id}, {
        "binding_status": "bound", "binding_primitive": primitive,
        "binding_params_json": params, "updated_at": now,
    })
    s.insert("transaction_log", {"ts": now, "actor": actor, "event": "kb_rule_bound",
                                 "payload": {"rule_id": rule_id, "primitive": primitive,
                                             "matched_pattern": params.get("matched_pattern")}})
    return s.query_one("kb_rules", rule_id=rule_id)


def bind_rule(rule_id: str, primitive: str, params: dict[str, Any], actor: str) -> dict[str, Any]:
    """Backward-compatible cross-field binding entrypoint."""
    return bind_registered_rule(rule_id, primitive, params, actor, registry="cross_field")


def bind_version(tenant_id: str, version_id: str, actor: str = "cross_field_binder") -> dict[str, Any]:
    """Attempt to bind every rule of one KB document version.

    Returns ``{"bound": [...], "skipped": [{rule_id, source_rule_id,
    reason}], "counts": {...}}`` — the skip reasons are first-class output,
    not a log line: an unbindable rule must be explainable to the person who
    uploaded the document.
    """
    rows = [r for r in s.query("kb_rules", tenant_id=tenant_id, version_id=version_id)]
    return _bind_rows(rows, actor)


def bind_published(tenant_id: str, actor: str = "cross_field_binder") -> dict[str, Any]:
    """Attempt to bind every published, not-yet-bound rule for a tenant."""
    rows = [r for r in s.query("kb_rules", tenant_id=tenant_id, lifecycle_state="published")
            if r.get("binding_status") != "bound"]
    return _bind_rows(rows, actor)


def _bind_rows(rows: list[dict[str, Any]], actor: str) -> dict[str, Any]:
    bound: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in rows:
        try:
            proposal = propose_binding(row)
        except BinderRefusal as exc:
            skipped.append({"rule_id": row["rule_id"],
                            "source_rule_id": row.get("source_rule_id"),
                            "rule_type": row.get("rule_type"),
                            "reason": exc.reason})
            continue
        bind_rule(row["rule_id"], proposal["primitive"], proposal["params"], actor)
        bound.append({"rule_id": row["rule_id"], "source_rule_id": row.get("source_rule_id"),
                      "rule_type": row.get("rule_type"), "primitive": proposal["primitive"],
                      "matched_pattern": proposal["params"]["matched_pattern"]})
    return {"bound": bound, "skipped": skipped,
            "counts": {"considered": len(rows), "bound": len(bound), "skipped": len(skipped)}}
