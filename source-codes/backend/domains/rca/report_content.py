"""Read-only report projections. No new assessments, model calls or dataset reads."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone


def label(value) -> str:
    return str(value or "Not retained").replace("_", " ").capitalize()


def prose(value) -> str:
    if value is None:
        return "Not retained"
    if isinstance(value, list):
        return "\n".join(f"- {prose(item)}" for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{label(key)}: {prose(item)}" for key, item in value.items())
    # Retained narrative often contains inline Markdown; preserve its words.
    return re.sub(r"\*\*(.*?)\*\*|`([^`]+)`", lambda m: m[1] or m[2], str(value))


def technical_column(key: str) -> bool:
    return (any(part in key.lower() for part in ("fingerprint", "hash", "artifact", "python_code"))
            or key.lower() in {"execution_id", "look_id", "hypothesis_id", "snapshot_id", "workflow_generation"})


def cell_value(value, key="") -> str:
    if value is None:
        return "Not retained"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            return "Not available"
        if key.endswith(("_rate", "_share", "_proportion")) and 0 <= value <= 1:
            return f"{value:.2%}"
        return f"{value:,}" if isinstance(value, int) else f"{value:,.4g}"
    return prose(value)


def parse_time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def time_label(value):
    parsed = parse_time(value)
    if parsed is None:
        return "Not retained"
    # Do not invent a timezone or time of day for legacy timestamps.
    if len(str(value)) == 10:
        return parsed.strftime("%d %b %Y") + " (date only)"
    if parsed.tzinfo is None:
        return parsed.strftime("%d %b %Y %H:%M") + " (zone not retained)"
    return parsed.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")


def elapsed(start, end):
    first, last = parse_time(start), parse_time(end)
    if not first or not last or not first.tzinfo or not last.tzinfo:
        return "Not available"
    seconds = int((last - first).total_seconds())
    if seconds < 0:
        return "Not available"
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return " ".join(f"{value}{unit}" for value, unit in
                    ((days, "d"), (hours, "h"), (minutes, "m"), (seconds, "s")) if value) or "0s"


def hypothesis_groups(case):
    """Same first-plan numbering as UI hypothesisGroups, not candidate creation order."""
    known = {}
    for source in ("hypothesis_catalog", "hypothesis_candidates", "focused_hypothesis_candidates", "hypotheses"):
        for item in case.get(source) or []:
            if item.get("hypothesis_id"):
                known.setdefault(item["hypothesis_id"], {**item, "number": None, "looks": []})
    for key in ("selected_initial_hypothesis", "selected_focused_hypothesis", "active_investigation_hypothesis"):
        item = case.get(key) or {}
        if item.get("hypothesis_id"):
            known.setdefault(item["hypothesis_id"], {**item, "number": None, "looks": []})
    looks = case.get("looks") or []
    by_id = {look["look_id"]: look for look in looks}
    next_number = 1
    for look in looks:
        fork = look.get("fork_json") or {}
        parent = (by_id.get(fork.get("combined_parent_look_id")) or {}).get("fork_json") or {}
        identity = fork.get("hypothesis_id") or parent.get("hypothesis_id")
        if not identity:
            continue
        group = known.setdefault(identity, {"hypothesis_id": identity,
            "statement": fork.get("hypothesis") or parent.get("hypothesis"), "number": None, "looks": []})
        if group["number"] is None:
            group["number"] = next_number
            next_number += 1
        group["looks"].append(look)
    active = (case.get("active_investigation_hypothesis") or case.get("selected_focused_hypothesis")
              or case.get("selected_initial_hypothesis") or {})
    if active.get("hypothesis_id") in known and known[active["hypothesis_id"]]["number"] is None:
        known[active["hypothesis_id"]]["number"] = next_number
    return sorted(known.values(), key=lambda group: group["number"] or math.inf)


def interpretation_for(look, events):
    matched = [event for event in events if (event.get("details") or {}).get("look_id") == look["look_id"]
               and event.get("evidence_kind") in {"combined_hypothesis_run", "agent_interpretation"}]
    combined = [event for event in matched if event.get("evidence_kind") == "combined_hypothesis_run"]
    event = (combined or matched or [{}])[-1]
    return (event.get("details") or {}).get("interpretation") or {}


def run_status(look, executions, events):
    fork = look.get("fork_json") or {}
    execution = executions.get(look["look_id"]) or {}
    runtime = (execution.get("summary_json") or {}).get("runtime") or {}
    if fork.get("combined_run_state") == "failed" or runtime.get("ok") is False or execution.get("status") in {"failed", "cancelled"}:
        return "Cancelled" if execution.get("status") == "cancelled" else "Failed - no accepted finding"
    assessment = interpretation_for(look, events).get("assessment")
    if assessment:
        return label(assessment)
    return label(execution.get("status")) if execution else "Not run"


def timeline(case, groups):
    events = case.get("aar_evidence") or []
    executions = case.get("executions") or {}
    conclusion = case.get("conclusion") or {}
    # case.created_at survives Start afresh; use the current generation's event instead.
    starts = [e for e in events if e.get("evidence_kind") == "workflow_reset"]
    starts = starts or [e for e in events if e.get("evidence_kind") == "case_context_created"]
    started = starts[0].get("recorded_at") if starts else case.get("created_at") if int(case.get("workflow_generation") or 1) == 1 else None
    rows = [{"at": started, "milestone": "RCA started", "detail": "Current investigation generation", "actor": case.get("created_by")}]
    if starts:
        rows[0]["actor"] = starts[0].get("created_by")
    reviews = [e for e in events if e.get("evidence_kind") == "llm_initial_review" and e.get("status") != "started"]
    reviews = reviews or [e for e in events if e.get("evidence_kind") == "static_initial_review" and e.get("status") != "started"]
    for event in reviews:
        rows.append({"at": event.get("recorded_at"), "milestone": "Initial review", "detail": label(event.get("status")), "actor": event.get("created_by")})
    if not reviews:
        for look in case.get("looks") or []:
            execution = executions.get(look["look_id"]) or {}
            if look.get("kind") == "opening" and execution:
                rows.append({"at": execution.get("executed_at"), "milestone": "Opening review",
                             "detail": label(execution.get("status")), "actor": None})
    for group in groups:
        index = 0
        for look in group["looks"]:
            fork = look.get("fork_json") or {}
            if fork.get("combined_parent_look_id"):
                continue
            index += 1
            execution = executions.get(look["look_id"]) or {}
            terminal = next((e for e in reversed(events) if e.get("evidence_kind") == "combined_hypothesis_run"
                             and (e.get("details") or {}).get("look_id") == look["look_id"]), {})
            terminal = terminal or next((e for e in reversed(events) if e.get("evidence_kind") == "agent_interpretation"
                and e.get("status") != "started" and (e.get("details") or {}).get("look_id") == look["look_id"]), {})
            if not execution and not terminal:
                continue
            rows.append({"at": terminal.get("recorded_at") or execution.get("executed_at"),
                "milestone": f"Hypothesis {group['number']} / run {index}",
                "detail": run_status(look, executions, events), "actor": terminal.get("created_by")})
    rows.extend([
        {"at": conclusion.get("approved_at"), "milestone": "Conclusion approved", "detail": conclusion.get("outcome_label") or "Approval details not retained", "actor": conclusion.get("approved_by")},
        {"at": (case.get("closure") or {}).get("closed_at") or case.get("closed_at"), "milestone": "RCA closed", "detail": "Investigation complete; remediation not implied", "actor": None},
    ])
    # Preserve logical positions for missing or timezone-ambiguous legacy dates.
    if all(parse_time(row["at"]) and parse_time(row["at"]).tzinfo for row in rows):
        rows.sort(key=lambda row: parse_time(row["at"]))
    return started, rows


ARTIFACT_GROUPS = {
    "case_context_created": ("Source evidence and context", "Frozen case context"),
    "static_initial_review": ("Source evidence and context", "Opening diagnostic evidence"),
    "llm_initial_review": ("Source evidence and context", "Initial evidence review"),
    "human_context": ("Source evidence and context", "User-supplied context"),
    "agent_code_generation": ("Generated code and full outputs", "Generated investigation code"),
    "code_generation": ("Generated code and full outputs", "Generated investigation code"),
    "data_chat_code_generation": ("Generated code and full outputs", "Generated chat code"),
    "sandbox_output": ("Generated code and full outputs", "Full investigation output"),
    "data_chat_sandbox_output": ("Generated code and full outputs", "Full chat output"),
    "driver_target_definition": ("Investigation and decisions", "Discovery target and scope"),
    "library_search": ("Investigation and decisions", "Governed helper selection"),
    "agent_investigation_plan": ("Investigation and decisions", "Precommitted investigation plan"),
    "sandbox_execution": ("Investigation and decisions", "Analysis and execution outcome"),
    "agent_interpretation": ("Investigation and decisions", "Assessment and supporting rationale"),
    "combined_hypothesis_run": ("Investigation and decisions", "Combined discovery / confirmation outcome"),
    "human_decision": ("Investigation and decisions", "Recorded user decision"),
    "alternative_hypothesis": ("Investigation and decisions", "Alternative explanation proposal"),
    "workflow_reset": ("Source evidence and context", "Start-afresh record"),
    "data_chat_user_message": ("Data-chat records", "User question and supplied evidence"),
    "data_chat_plan": ("Data-chat records", "Bounded chat analysis plan"),
    "data_chat_library_search": ("Data-chat records", "Chat helper selection"),
    "data_chat_assistant_message": ("Data-chat records", "Chat answer, references and limitations"),
    "data_chat_sandbox_execution": ("Data-chat records", "Chat sandbox analysis result"),
    "data_chat_analysis_execution": ("Data-chat records", "Chat helper analysis result"),
}


class ArtifactIndex:
    def __init__(self, events):
        self.entries = {}
        self.excluded = {e.get("artifact_id") for e in events if e.get("evidence_kind") == "operation_progress"}
        for event in events:
            kind = event.get("evidence_kind") or "retained_evidence"
            if kind == "operation_progress" or event.get("status") == "started":
                continue
            category, title = ARTIFACT_GROUPS.get(kind, (
                "Data-chat records" if kind.startswith("data_chat_") else "Investigation and decisions", label(kind)))
            self.add(event.get("artifact_id"), title, category, event.get("status"))

    def add(self, identity, title="Referenced evidence", category="Additional referenced artifacts", status=None):
        if not identity or identity in self.excluded:
            return None
        if identity not in self.entries:
            self.entries[identity] = {"reference": f"A{len(self.entries) + 1}", "title": title,
                "category": category, "status": label(status) if status else "Referenced", "identity": identity}
        return self.entries[identity]["reference"]

    def refs(self, identities):
        return ", ".join(dict.fromkeys(ref for identity in identities or [] if (ref := self.add(identity))))
