"""RCA Stage 2 — Knowledge Base service. See docs/rca/00-contracts.md §6.

KnowledgeDocument -> KnowledgeDocumentVersion -> KnowledgeSection ->
KnowledgeRule. This module is the ONLY writer to kb_rules — no LLM call is
ever the last step before a write here (Stage 3/5 add the human-answer and
case-closure callers, both deterministic code, never an LLM directly).
Publish/archive require the configured human role. The only exception is the
immutable, source-controlled built-in diagnostic-package seeder below; it
publishes a stable reviewed version once and refuses in-place content changes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections import Counter
from pathlib import Path

import system_db as s
from kb_convert import ConversionError, convert_to_markdown

BACKEND_ROOT = Path(__file__).resolve().parent
KB_STORAGE_ROOT = Path(os.environ.get("KB_STORAGE_DIR") or (BACKEND_ROOT / "kb_storage")).resolve()

DEFAULT_SHELF_LIFE_MONTHS = 12
KNOWLEDGE_CATEGORIES = {"structure", "lineage", "domain_fact", "ownership", "case_history"}
TRUST_LEVELS = {"human_confirmed", "inferred"}
LIFECYCLE_STATES = {"draft", "pending_review", "published", "expired", "superseded",
                    "under_suspicion", "archived"}

# contracts.md §5 — enforced server-side in list_eligible_rules(), not by
# agent self-restraint. Extended as later stages add more requesting agents;
# an agent not listed here gets no categories (fail closed).
AGENT_CATEGORY_MATRIX = {
    "intake": {"structure", "lineage", "domain_fact", "ownership", "case_history"},
    "opening_looks": {"structure", "lineage", "domain_fact"},
    "planner": {"structure", "lineage", "domain_fact"},
    "reader": {"structure", "lineage", "domain_fact"},
    "coverage_challenge_pass1": set(),
    "coverage_challenge_pass2": {"case_history"},
    "composer": set(),
    "judge": set(),
    "fix_advisor": {"ownership"},
    # Phase 6 (CFR-03) — the cross-field diagnostic engine. Every other key
    # above is an RCA agent; this one is a deterministic engine, and it reads
    # exactly one category: a cross-field rule states a fact about the domain
    # ("EAD cannot be below drawn balance"), so the publish-time convention
    # for a cross-field rule table is category="domain_fact". A rule published
    # under any other category is simply never retrieved by the engine —
    # visible in the KB, not executable, which is the honest outcome.
    "cross_field_engine": {"domain_fact"},
    "row_completeness_engine": {"domain_fact"},
}


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class KbError(Exception):
    pass


class ForbiddenError(KbError):
    pass


# --- Original-bytes storage (content-addressed, dedup by sha256) ------------

def _storage_path(tenant_id: str, sha256: str) -> Path:
    return KB_STORAGE_ROOT / tenant_id / sha256[:2] / sha256

def store_original(tenant_id: str, content: bytes) -> tuple[str, str]:
    sha256 = hashlib.sha256(content).hexdigest()
    path = _storage_path(tenant_id, sha256)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return sha256, str(path.relative_to(KB_STORAGE_ROOT))


def read_original(bytes_ref: str) -> bytes:
    return (KB_STORAGE_ROOT / bytes_ref).read_bytes()


# --- Upload / conversion -----------------------------------------------------

def upload_document(tenant_id: str, filename: str, media_type: str, content: bytes,
                    category_hint: str | None, actor: str, is_synthetic: bool = False) -> dict:
    """Type/signature/size validation -> preserve source -> extract/convert ->
    conversion report (contracts.md §6.2 pipeline, malware-scan seam deferred
    to Stage 6). Raises ConversionError for unsupported/corrupt/image-only
    input; nothing is persisted on that path."""
    conv = convert_to_markdown(filename, media_type, content)  # raises ConversionError
    sha256, bytes_ref = store_original(tenant_id, content)

    document_id = _id("kbdoc")
    s.insert("kb_documents", {
        "document_id": document_id, "tenant_id": tenant_id,
        "title": filename, "category_hint": category_hint,
        "is_synthetic": 1 if is_synthetic else 0,
        "created_by": actor, "created_at": s.now_ist(),
    })
    version = _insert_version(document_id, 1, filename, media_type, sha256, bytes_ref,
                              len(content), conv, actor)
    return {"document_id": document_id, "version": version}


def upload_new_version(tenant_id: str, document_id: str, filename: str, media_type: str,
                       content: bytes, actor: str) -> dict:
    doc = require_document(tenant_id, document_id)
    conv = convert_to_markdown(filename, media_type, content)
    sha256, bytes_ref = store_original(tenant_id, content)
    versions = s.query("kb_document_versions", order_by="version_seq DESC", document_id=document_id)
    next_seq = (versions[0]["version_seq"] + 1) if versions else 1
    version = _insert_version(document_id, next_seq, filename, media_type, sha256, bytes_ref,
                              len(content), conv, actor)
    return {"document_id": document_id, "version": version}


def _insert_version(document_id: str, version_seq: int, filename: str, media_type: str,
                    sha256: str, bytes_ref: str, size: int, conv: dict, actor: str) -> dict:
    version_id = _id("kbver")
    s.insert("kb_document_versions", {
        "version_id": version_id, "document_id": document_id, "version_seq": version_seq,
        "original_filename": filename, "original_media_type": media_type,
        "original_sha256": sha256, "original_bytes_ref": bytes_ref, "original_size": size,
        "converted_markdown": conv["markdown"], "converted_markdown_sha256": conv["markdown_sha256"],
        "converter_name": conv["converter_name"], "converter_version": conv["converter_version"],
        "conversion_warnings_json": conv["warnings"], "conversion_report_json": {
            "warning_count": len(conv["warnings"]), "markdown_chars": len(conv["markdown"]),
        },
        "review_state": "draft", "reviewer": None, "reviewed_at": None,
        "created_by": actor, "created_at": s.now_ist(),
    })
    return s.query_one("kb_document_versions", version_id=version_id)


# --- Read/list ---------------------------------------------------------------

def require_document(tenant_id: str, document_id: str) -> dict:
    row = s.query_one("kb_documents", document_id=document_id)
    if not row or row["tenant_id"] != tenant_id:
        raise KeyError("Unknown knowledge document")
    return row


def list_documents(tenant_id: str, include_synthetic: bool = False) -> list[dict]:
    docs = [d for d in s.query("kb_documents", order_by="created_at DESC", tenant_id=tenant_id)
           if include_synthetic or not d.get("is_synthetic")]
    out = []
    for d in docs:
        versions = s.query("kb_document_versions", order_by="version_seq DESC", document_id=d["document_id"])
        latest = versions[0] if versions else None
        report = (latest or {}).get("conversion_report_json") or {}
        if report.get("source") == "case_closure":
            candidate_rules, _ = _rules_for_version(latest["version_id"])
            if not any(rule.get("lifecycle_state") == "published" for rule in candidate_rules):
                continue
        out.append({**d, "version_count": len(versions),
                   "latest_review_state": (latest or {}).get("review_state")})
    return out


def learning_candidate_version_ids(tenant_id: str, pending_only: bool = False) -> set[str]:
    document_ids = {row["document_id"] for row in s.query("kb_documents", tenant_id=tenant_id)}
    version_ids = set()
    for version in s.query("kb_document_versions"):
        if version["document_id"] not in document_ids:
            continue
        if (version.get("conversion_report_json") or {}).get("source") != "case_closure":
            continue
        if pending_only:
            rules, _ = _rules_for_version(version["version_id"])
            if not any(rule.get("lifecycle_state") in {"draft", "pending_review"} for rule in rules):
                continue
        version_ids.add(version["version_id"])
    return version_ids


def list_learning_candidates(tenant_id: str) -> list[dict]:
    candidates = []
    for version_id in learning_candidate_version_ids(tenant_id):
        version = s.query_one("kb_document_versions", version_id=version_id)
        document = require_document(tenant_id, version["document_id"])
        report = version.get("conversion_report_json") or {}
        rules, _ = _rules_for_version(version_id)
        for rule in rules:
            candidates.append({
                "candidate_id": rule["rule_id"], "case_id": report.get("case_id"),
                "proposal": report.get("proposal") or {}, "document_id": document["document_id"],
                "title": document["title"], "version_id": version_id,
                "rule_text": rule["rule_text"], "category": rule["category"],
                "lifecycle_state": rule["lifecycle_state"],
                "binding_status": rule.get("binding_status"),
                "related_tables": rule.get("related_tables_json") or [],
                "created_by": version.get("created_by"), "created_at": version.get("created_at"),
            })
    candidates.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return candidates


def get_document(tenant_id: str, document_id: str) -> dict:
    doc = require_document(tenant_id, document_id)
    versions = s.query("kb_document_versions", order_by="version_seq DESC", document_id=document_id)
    return {**doc, "versions": versions}


def get_version_preview(tenant_id: str, version_id: str) -> dict:
    version = s.query_one("kb_document_versions", version_id=version_id)
    if not version:
        raise KeyError("Unknown document version")
    require_document(tenant_id, version["document_id"])
    sections = s.query("kb_sections", order_by="order_seq", version_id=version_id)
    rules = []
    for sec in sections:
        rules.extend(s.query("kb_rules", section_id=sec["section_id"]))
    return {**version, "sections": sections, "rules": rules}


# --- Draft extraction: markdown -> sections -> one draft rule per section ----

def _split_sections(markdown: str) -> list[dict]:
    """Split on "## " headings (H2). Content before the first H2 becomes an
    "Overview" section if non-empty. This is Stage 2's deterministic, minimal
    extraction — one section = one governed rule; no LLM involved."""
    lines = markdown.splitlines()
    sections: list[dict] = []
    current_heading = None
    current_body: list[str] = []

    def flush():
        body = "\n".join(current_body).strip()
        if body:
            sections.append({"heading": current_heading or "Overview", "body": body})

    for line in lines:
        m = re.match(r"^##\s+(.+)$", line.strip())
        if m:
            flush()
            current_heading = m.group(1).strip()
            current_body = []
        else:
            current_body.append(line)
    flush()
    return sections


# --- Table-aware extraction (KB-04/07/08/09/11, C-34) ------------------------
# A document of N tabulated rules must yield N rule records. This layer sits
# on top of _split_sections(): each H2 section's body is scanned for GFM pipe
# tables (kb_convert.py renders PDF/DOCX tables as GFM so structure survives
# into the stored Markdown — the single-provenance-artifact principle). A
# recognized table (its header row fuzzy-matches enough of the generic
# id/severity/type/entity/roles/rule/reference vocabulary) explodes into one
# rule record per DATA ROW instead of the section becoming a single rule.
# Everything here is generic structural pattern matching on column HEADER
# WORDS — no rule content, domain vocabulary, or document-specific knowledge
# is hardcoded (KB-01: business_rules.json and everything like it is gone).
#
# A section with no recognized table falls back to the pre-Phase-5 behaviour
# untouched (5-T2 regression guard): the whole section becomes one rule.

_PAGE_MARKER_RE = re.compile(r"^<!--\s*pdf:page=(\d+)\s*-->$")

# A standalone line like "IRB (36 rules)" or "IRB+IFRS9 (2 rules)" preceding
# one or more tables is a generic "labeled group of N rows" convention (not
# specific to any one document's vocabulary — the label text itself is
# whatever the source document uses). It sets the "framework" attributed to
# the next N data rows encountered, carrying across a page break if the
# table itself continues without a new heading in between.
_GROUP_HEADING_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+/_ -]{0,40}?)\s*\(\s*(\d+)\s+rules?\s*\)\s*$",
                               re.IGNORECASE)

# An "<id-like-token> — <note>" line anywhere in a section's leftover prose
# (e.g. an "Encoded exceptions & notes" appendix, or per-rule footnotes) is a
# generic annotation convention: if <id-like-token> matches a source_rule_id
# already recovered from a table in the same section, the note is attached
# to that rule's encoded_exceptions_json (KB-07) — never sub-parsed further.
_NOTE_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]{1,40})\s*[—–-]\s*(.+)$")

# A semantic-role token this long with no separators is very likely PDF text
# extraction collapsing a real multi-word name (KB-11: "exposure_at_default"
# -> "exposureatdefault"). Surfaced for confirmation, never auto-split —
# there is no dictionary to split against, and guessing is forbidden.
_COLLAPSED_ROLE_RE = re.compile(r"^[A-Za-z]{15,}$")

_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")

# Column-header synonym patterns, generic English structural words only —
# checked in this order so a more specific field (e.g. "id") wins over a
# looser one (e.g. "rule", which "Rule id" would otherwise also match).
_HEADER_FIELD_PATTERNS = [
    ("source_rule_id", re.compile(r"\bid\b")),
    ("severity", re.compile(r"\bsev(erity)?\b")),
    ("rule_type", re.compile(r"\btype\b")),
    ("entity", re.compile(r"\bentity\b")),
    ("semantic_roles", re.compile(r"\brole")),
    ("regulatory_ref", re.compile(r"\breg\b|\breference\b|\bcitation\b")),
    ("rule_text", re.compile(r"\brule\b|\bpredicate\b|\bexpression\b")),
]

_SEVERITY_MAP = {
    "crit": "CRITICAL", "critical": "CRITICAL",
    "mate": "MATERIAL", "material": "MATERIAL", "maj": "MATERIAL", "major": "MATERIAL",
    "mino": "MINOR", "minor": "MINOR", "min": "MINOR",
}


def _split_row_cells(line: str) -> list[str]:
    s_ = line.strip()
    if s_.startswith("|"):
        s_ = s_[1:]
    if s_.endswith("|"):
        s_ = s_[:-1]
    parts = re.split(r"(?<!\\)\|", s_)
    return [p.replace("\\|", "|").strip() for p in parts]


def _looks_like_table_row(line: str) -> bool:
    return "|" in line.strip()


def _looks_like_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEP_CELL_RE.match(c.strip()) for c in cells)


def _normalize_header_cell(cell: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (cell or "").lower()).strip()


def _match_header_field(cell: str) -> str | None:
    norm = _normalize_header_cell(cell)
    for field, pattern in _HEADER_FIELD_PATTERNS:
        if pattern.search(norm):
            return field
    return None


def _normalize_severity(raw: str | None) -> str | None:
    if not (raw or "").strip():
        return None
    key = raw.strip().lower()
    return _SEVERITY_MAP.get(key, raw.strip().upper())


def _normalize_rule_type(raw: str | None) -> str | None:
    """"Domain/bound" -> "domain", "Identity/derivation" -> "identity",
    "Date ordering" -> "date_ordering": take the text before the first "/"
    (a common "primary/qualifier" header-cell convention), lowercase, spaces
    to underscores. Not a lookup table of known type names — a structural
    normalization that happens to reproduce the exact 5 category keys this
    document declares, and generalizes to any similarly-shaped "Type" column."""
    if not (raw or "").strip():
        return None
    first = raw.strip().split("/")[0].strip()
    return re.sub(r"\s+", "_", first.lower()) or None


def _normalize_reg_ref(raw: str | None) -> str | None:
    t = (raw or "").strip()
    if not t or t in {"—", "–", "-", "N/A", "NA", "n/a"}:
        return None
    return t


def _normalize_framework_label(raw: str | None) -> str | None:
    if not (raw or "").strip():
        return None
    label = raw.strip()
    if "+" in label or "&" in label or " and " in label.lower():
        return "both"
    return label.upper()


def _detect_role_hazards(roles: list[str]) -> list[dict]:
    hazards = []
    for role in roles:
        token = (role or "").strip()
        if token and _COLLAPSED_ROLE_RE.match(token):
            hazards.append({"role_text": token, "note": "collapsed role name — confirm before use"})
    return hazards


def _build_rule_row(header_cells: list[str], field_map: dict[int, str | None],
                    data_cells: list[str], page: int | None, framework: str | None) -> dict:
    ncols = len(header_cells)
    data_cells = data_cells + [""] * (ncols - len(data_cells))
    raw: dict[str, str] = {}
    for idx, field in field_map.items():
        if field:
            raw[field] = data_cells[idx] if idx < len(data_cells) else ""

    source_rule_id = (raw.get("source_rule_id") or "").strip() or None
    severity = _normalize_severity(raw.get("severity"))
    rule_type = _normalize_rule_type(raw.get("rule_type"))
    entity = (raw.get("entity") or "").strip() or None
    roles = [r.strip() for r in (raw.get("semantic_roles") or "").split(",") if r.strip()]
    rule_text = (raw.get("rule_text") or "").strip() or None
    regulatory_ref = _normalize_reg_ref(raw.get("regulatory_ref"))
    hazards = _detect_role_hazards(roles)

    sep = ["---"] * ncols
    body = "\n".join([
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(sep) + " |",
        "| " + " | ".join(data_cells) + " |",
    ])
    heading = source_rule_id or (rule_text[:60] if rule_text else "Rule")

    return {
        "heading": heading,
        "body": body,
        "fields": {
            "source_rule_id": source_rule_id, "severity": severity, "rule_type": rule_type,
            "entity": entity, "semantic_roles": roles, "rule_text": rule_text,
            "regulatory_ref": regulatory_ref, "source_page": page, "framework": framework,
            "hazards": hazards, "encoded_exceptions": [],
        },
    }


def _extract_table_rows_from_section(body: str) -> list[dict]:
    """Scan one section's body for recognized GFM rule tables and return one
    row-dict per DATA ROW across all of them, in document order. Returns []
    if no table in this section's header row matches enough of the generic
    id/severity/type/entity/role/rule/reference vocabulary to count as a
    rule table (the caller then falls back to the pre-Phase-5 whole-section
    -> one-rule behaviour)."""
    lines = body.splitlines()
    n = len(lines)
    current_page: int | None = None
    group_label: str | None = None
    group_remaining = 0
    row_entries: list[dict] = []

    i = 0
    while i < n:
        line = lines[i]
        stripped = line.strip()

        m_page = _PAGE_MARKER_RE.match(stripped)
        if m_page:
            current_page = int(m_page.group(1))
            i += 1
            continue

        m_group = _GROUP_HEADING_RE.match(stripped)
        if m_group:
            group_label = m_group.group(1).strip()
            group_remaining = int(m_group.group(2))
            i += 1
            continue

        if _looks_like_table_row(line) and i + 1 < n:
            header_cells = _split_row_cells(line)
            sep_cells = _split_row_cells(lines[i + 1]) if _looks_like_table_row(lines[i + 1]) else []
            if len(header_cells) >= 2 and _looks_like_separator_row(sep_cells):
                field_map = {idx: _match_header_field(c) for idx, c in enumerate(header_cells)}
                matched_fields = {v for v in field_map.values() if v}
                j = i + 2
                data_rows = []
                while j < n and _looks_like_table_row(lines[j]):
                    data_rows.append(_split_row_cells(lines[j]))
                    j += 1
                # Recognition bar: a real rule table needs a rule/predicate-
                # like column plus at least one other structural column —
                # otherwise it's just some other table and is left as inert
                # Markdown (not exploded, not counted).
                if "rule_text" in matched_fields and len(matched_fields) >= 2:
                    for data in data_rows:
                        fw = None
                        if group_remaining > 0 and group_label:
                            fw = _normalize_framework_label(group_label)
                            group_remaining -= 1
                        row_entries.append(_build_rule_row(header_cells, field_map, data,
                                                           current_page, fw))
                i = j
                continue
        i += 1

    if not row_entries:
        return []

    by_id = {r["fields"]["source_rule_id"]: r for r in row_entries if r["fields"].get("source_rule_id")}
    for line in lines:
        m = _NOTE_LINE_RE.match(line.strip())
        if not m:
            continue
        target = by_id.get(m.group(1).strip())
        if target is not None:
            target["fields"]["encoded_exceptions"].append(m.group(2).strip())

    return row_entries


def _split_sections_table_aware(markdown: str) -> list[dict]:
    """_split_sections() (H2-based) + table-row explosion within each
    section. A section with a recognized table is REPLACED by its exploded
    rows (KB-04: N tabulated rules -> N rule records, not N+1 with a
    redundant whole-section rule); a section with none keeps the exact
    pre-Phase-5 behaviour."""
    out: list[dict] = []
    for sec in _split_sections(markdown):
        rows = _extract_table_rows_from_section(sec["body"])
        if rows:
            out.extend(rows)
        else:
            out.append({"heading": sec["heading"], "body": sec["body"], "fields": {}})
    return out


def _binding_status_for(fields: dict) -> str:
    """KB-08/09 — conservative by construction: parsing alone never makes a
    rule executable. A rule_type recovered from a table header gets
    'reference-only' (parsed and displayable, no implementation exists in
    this phase); anything else — including every prose/H2-derived rule,
    which never had a structured type to begin with — defaults to
    'unparsed'. 'bound' is never assigned by this module: it requires a
    later phase's primitive-signature binder or a human confirmation, which
    do not exist yet (Phase 6). Never execute a predicate recovered from
    prose (KB-09)."""
    return "reference-only" if fields.get("rule_type") else "unparsed"


def submit_for_review(tenant_id: str, version_id: str, actor: str,
                      category: str | None = None) -> dict:
    """Extract sections/draft rules from the converted Markdown and move the
    version into pending_review. Idempotent: re-submitting re-extracts (drops
    and recreates draft-only sections/rules; published rules from a PRIOR
    version are never touched, since each version has its own section/rule
    rows)."""
    version = s.query_one("kb_document_versions", version_id=version_id)
    if not version:
        raise KeyError("Unknown document version")
    require_document(tenant_id, version["document_id"])
    existing_sections = s.query("kb_sections", version_id=version_id)
    for sec in existing_sections:
        rules = s.query("kb_rules", section_id=sec["section_id"])
        if any(r["lifecycle_state"] != "draft" for r in rules):
            raise KbError("Cannot re-extract: this version already has non-draft rules.")
        for r in rules:
            s.delete("kb_rules", rule_id=r["rule_id"])
        s.delete("kb_sections", section_id=sec["section_id"])

    is_synthetic_doc = 1 if s.query_one("kb_documents", document_id=version["document_id"]).get("is_synthetic") else 0
    for i, entry in enumerate(_split_sections_table_aware(version["converted_markdown"])):
        section_id = _id("kbsec")
        s.insert("kb_sections", {
            "section_id": section_id, "version_id": version_id,
            "heading": entry["heading"], "body_markdown": entry["body"], "order_seq": i,
        })
        fields = entry.get("fields") or {}
        source_rule_id = fields.get("source_rule_id")
        rule_text = fields.get("rule_text") or entry["body"]
        hash_basis = f"{source_rule_id}|{rule_text}" if source_rule_id else entry["body"]
        rule_id = _id("kbrule")
        s.insert("kb_rules", {
            "rule_id": rule_id, "section_id": section_id, "tenant_id": tenant_id,
            "document_id": version["document_id"], "version_id": version_id,
            "rule_hash": hashlib.sha256(hash_basis.encode("utf-8")).hexdigest(),
            "rule_text": rule_text, "category": category,
            "trust_level": "inferred", "lifecycle_state": "draft",
            "effective_date": None, "last_confirmed_date": None, "shelf_life_months": None,
            "owner": None, "reviewer": None, "approver": None,
            "related_tables_json": [], "related_columns_json": [],
            "related_tables_schema_hash_json": {},
            "superseded_by_rule_id": None, "under_suspicion_reason": None,
            "is_synthetic": is_synthetic_doc,
            # KB-07 rule-record fields, recovered by the table-aware extractor
            # above; all None for a plain prose/H2 section (unchanged shape).
            "source_rule_id": source_rule_id,
            "severity": fields.get("severity"),
            "rule_type": fields.get("rule_type"),
            "entity": fields.get("entity"),
            "semantic_roles_json": fields.get("semantic_roles") or [],
            "regulatory_ref": fields.get("regulatory_ref"),
            "encoded_exceptions_json": fields.get("encoded_exceptions") or [],
            "source_page": fields.get("source_page"),
            # KB-08/09 — conservative default; 'bound' is never assigned here.
            "binding_status": _binding_status_for(fields),
            "binding_primitive": None, "binding_params_json": None,
            "parse_hazards_json": fields.get("hazards") or [],
            "framework": fields.get("framework"),
            "created_at": s.now_ist(), "updated_at": s.now_ist(),
        })
    s.update("kb_document_versions", {"version_id": version_id}, {"review_state": "pending_review"})
    return get_version_preview(tenant_id, version_id)


# --- Publish / archive (human-role-gated; nothing here is LLM-reachable) ----

def _require_role(roles: list[str], role: str) -> None:
    if role not in (roles or []):
        raise ForbiddenError(f"Requires the '{role}' role.")


def _shelf_life_for(category: str | None) -> int:
    row = s.query_one("kb_shelf_life_defaults", category=category) if category else None
    return row["months"] if row else DEFAULT_SHELF_LIFE_MONTHS


def publish_rule(tenant_id: str, rule_id: str, actor: str, roles: list[str],
                 category: str, related_tables: list[str] | None = None,
                 related_columns: list[str] | None = None,
                 trust_level: str = "human_confirmed",
                 shelf_life_months: int | None = None, owner: str | None = None) -> dict:
    _require_role(roles, "kb_reviewer")
    rule = s.query_one("kb_rules", rule_id=rule_id)
    if not rule or rule["tenant_id"] != tenant_id:
        raise KeyError("Unknown knowledge rule")
    if rule["lifecycle_state"] not in {"draft", "pending_review"}:
        raise KbError(f"Cannot publish a rule in lifecycle state {rule['lifecycle_state']!r}.")
    if category not in KNOWLEDGE_CATEGORIES:
        raise ValueError(f"Invalid knowledge category: {category!r}")
    if trust_level not in TRUST_LEVELS:
        raise ValueError(f"Invalid trust level: {trust_level!r}")
    now = s.now_ist()
    related_tables = related_tables or []
    hashes = {t: _table_schema_hash(t) for t in related_tables}
    s.update("kb_rules", {"rule_id": rule_id}, {
        "category": category, "trust_level": trust_level, "lifecycle_state": "published",
        "effective_date": now, "last_confirmed_date": now,
        "shelf_life_months": shelf_life_months or _shelf_life_for(category),
        "owner": owner, "reviewer": actor, "approver": actor,
        "related_tables_json": related_tables, "related_columns_json": related_columns or [],
        "related_tables_schema_hash_json": hashes, "updated_at": now,
    })
    s.insert("transaction_log", {"ts": now, "actor": actor, "event": "knowledge_publish",
                                 "payload": {"rule_id": rule_id, "tenant_id": tenant_id}})
    _attempt_binding(rule_id, actor)
    return s.query_one("kb_rules", rule_id=rule_id)


def _attempt_binding(rule_id: str, actor: str) -> None:
    """KB-08/CFR-06 — every publish is the one, automatic trigger for
    signature-exact binding (no separate step a human must remember to
    run). Purely mechanical: the binder only ever matches a rule against a
    registered primitive's exact shape (KB-09) — never a guess — so
    running it unconditionally at publish time is safe. A rule the binder
    cannot match stays 'reference-only'/'unparsed', displayable and
    honestly non-executable; that is not an error here, so any exception
    from the binder is swallowed after being logged, never surfaced to
    the publishing user as a publish failure."""
    from dq_diagnostics.engines.cross_field import binder  # noqa: PLC0415 — avoid a light-import-surface cost in kb.py for every caller
    try:
        rule = s.query_one("kb_rules", rule_id=rule_id)
        if not rule or rule.get("parse_hazards_json"):
            return
        proposal = binder.propose_binding(rule)
        binder.bind_rule(rule_id, proposal["primitive"], proposal["params"], actor)
    except binder.BinderRefusal:
        pass
    except Exception:  # noqa: BLE001 — binding is best-effort at publish time, never blocks publish
        import logging
        logging.getLogger(__name__).exception(
            "Cross-field binder failed on rule %s at publish time", rule_id)


def archive_rule(tenant_id: str, rule_id: str, actor: str, roles: list[str], reason: str) -> dict:
    _require_role(roles, "kb_reviewer")
    if not (reason or "").strip():
        raise ValueError("A reason is required to archive a rule.")
    rule = s.query_one("kb_rules", rule_id=rule_id)
    if not rule or rule["tenant_id"] != tenant_id:
        raise KeyError("Unknown knowledge rule")
    s.update("kb_rules", {"rule_id": rule_id}, {"lifecycle_state": "archived", "updated_at": s.now_ist()})
    s.insert("transaction_log", {"ts": s.now_ist(), "actor": actor, "event": "knowledge_archive",
                                 "payload": {"rule_id": rule_id, "reason": reason.strip()}})
    return s.query_one("kb_rules", rule_id=rule_id)


def mark_under_suspicion(tenant_id: str, rule_id: str, reason: str) -> dict:
    """Blame-back primitive (contracts.md §6.5) — triggered by Stage 5 closure
    outcomes; the primitive itself is deterministic code, callable by any
    internal service, never by an LLM directly."""
    rule = s.query_one("kb_rules", rule_id=rule_id)
    if not rule or rule["tenant_id"] != tenant_id:
        raise KeyError("Unknown knowledge rule")
    s.update("kb_rules", {"rule_id": rule_id}, {
        "lifecycle_state": "under_suspicion", "under_suspicion_reason": reason, "updated_at": s.now_ist(),
    })
    return s.query_one("kb_rules", rule_id=rule_id)


# --- Shelf life + schema-change invalidation ---------------------------------

def _months_between(iso_a: str, iso_b: str) -> float:
    from datetime import datetime
    a = datetime.fromisoformat(iso_a)
    b = datetime.fromisoformat(iso_b)
    return abs((b - a).days) / 30.436875


def check_shelf_life(tenant_id: str) -> int:
    """Past shelf life, a human_confirmed rule drops to inferred — it stays
    published/effective and challengeable, per WF §3a. Re-confirmation happens
    only when a case needs it (Stage 3+), not a bulk re-certification here."""
    now = s.now_ist()
    flipped = 0
    for rule in s.query("kb_rules", tenant_id=tenant_id, lifecycle_state="published", trust_level="human_confirmed"):
        if not rule.get("last_confirmed_date") or not rule.get("shelf_life_months"):
            continue
        if _months_between(rule["last_confirmed_date"], now) >= rule["shelf_life_months"]:
            s.update("kb_rules", {"rule_id": rule["rule_id"]}, {"trust_level": "inferred", "updated_at": now})
            flipped += 1
    return flipped


def _table_schema_hash(table_name: str) -> str:
    rows = s.query("table_metadata", table=table_name)
    if not rows:
        return ""
    row = rows[0]
    basis = f"{row.get('columns')}|{row.get('datatypes')}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def check_schema_invalidation(tenant_id: str) -> int:
    """A schema change to any related table also drops a published
    human_confirmed rule back to inferred (WF §3a). Re-hashes after flipping
    so the same already-detected change doesn't re-trigger every call."""
    flipped = 0
    for rule in s.query("kb_rules", tenant_id=tenant_id, lifecycle_state="published", trust_level="human_confirmed"):
        related = rule.get("related_tables_json") or []
        stored_hashes = rule.get("related_tables_schema_hash_json") or {}
        if not related:
            continue
        current = {t: _table_schema_hash(t) for t in related}
        if current != stored_hashes:
            s.update("kb_rules", {"rule_id": rule["rule_id"]}, {
                "trust_level": "inferred", "related_tables_schema_hash_json": current,
                "updated_at": s.now_ist(),
            })
            flipped += 1
    return flipped


# --- Case-closure draft write (Stage 3) ---------------------------------------
# contracts.md §6.3: exactly two code paths ever write kb_rules — the human-
# answer path (Stage 4's human gate) and this one. Both land as 'draft' and
# require the configured human approval (publish_rule) before entering
# retrieval. No LLM call is ever the last step before this INSERT.

def draft_rule_from_case_closure(tenant_id: str, category: str, rule_text: str,
                                 related_tables: list[str] | None, source_case_id: str,
                                 actor: str, proposal_metadata: dict | None = None) -> dict:
    if category not in KNOWLEDGE_CATEGORIES:
        raise ValueError(f"Invalid knowledge category: {category!r}")
    now = s.now_ist()
    document_id = _id("kbdoc")
    s.insert("kb_documents", {
        "document_id": document_id, "tenant_id": tenant_id,
        "title": f"Reusable knowledge proposal ({source_case_id})", "category_hint": category,
        "is_synthetic": 0, "created_by": actor, "created_at": now,
    })
    version_id = _id("kbver")
    s.insert("kb_document_versions", {
        "version_id": version_id, "document_id": document_id, "version_seq": 1,
        "original_filename": None, "original_media_type": "text/markdown",
        "original_sha256": None, "original_bytes_ref": None, "original_size": len(rule_text),
        "converted_markdown": rule_text, "converted_markdown_sha256": hashlib.sha256(rule_text.encode("utf-8")).hexdigest(),
        "converter_name": "case-closure", "converter_version": "1", "conversion_warnings_json": [],
        "conversion_report_json": {"source": "case_closure", "case_id": source_case_id,
                                   "proposal": proposal_metadata or {}},
        "review_state": "pending_review", "reviewer": None, "reviewed_at": None,
        "created_by": actor, "created_at": now,
    })
    section_id = _id("kbsec")
    s.insert("kb_sections", {
        "section_id": section_id, "version_id": version_id,
        "heading": "Reusable knowledge candidate", "body_markdown": rule_text, "order_seq": 0,
    })
    rule_id = _id("kbrule")
    s.insert("kb_rules", {
        "rule_id": rule_id, "section_id": section_id, "tenant_id": tenant_id,
        "document_id": document_id, "version_id": version_id,
        "rule_hash": hashlib.sha256(rule_text.encode("utf-8")).hexdigest(),
        "rule_text": rule_text, "category": category,
        "trust_level": "inferred", "lifecycle_state": "draft",
        "effective_date": None, "last_confirmed_date": None, "shelf_life_months": None,
        "owner": None, "reviewer": None, "approver": None,
        "related_tables_json": related_tables or [], "related_columns_json": [],
        "related_tables_schema_hash_json": {},
        "superseded_by_rule_id": None, "under_suspicion_reason": None, "is_synthetic": 0,
        "created_at": now, "updated_at": now,
    })
    return s.query_one("kb_rules", rule_id=rule_id)


# --- Retrieval ----------------------------------------------------------------

def list_eligible_rules(tenant_id: str, requesting_agent: str, case_id: str | None = None,
                        categories: list[str] | None = None, require_bound: bool = False) -> dict:
    """contracts.md §6.6 / docs/0.4.0/04-kb-contract.md §4 retrieval order:
    authorize tenant (caller resolves Principal) -> published/effective/non-
    archived -> category-restrict by requesting agent -> (Phase 5) optionally
    require binding_status='bound' -> persist manifest. ``require_bound``
    defaults False for backward compatibility with existing callers; the
    cross-field engine (Phase 6, CFR-03) is the caller that passes True —
    its rules come only from bound, published rules (KB-09: execution never
    reaches an unbound rule). Tag/table matching and ranking are added when a
    real caller needs them; this is the foundation."""
    allowed = AGENT_CATEGORY_MATRIX.get(requesting_agent, set())
    if categories:
        allowed = allowed & set(categories)
    eligible = []
    reasons = {}
    if allowed:
        for rule in s.query("kb_rules", tenant_id=tenant_id, lifecycle_state="published"):
            if rule["category"] not in allowed:
                continue
            if require_bound and rule.get("binding_status") != "bound":
                continue
            eligible.append(rule)
            reason = f"category_match:{rule['category']}"
            if require_bound:
                reason += ":bound"
            reasons[rule["rule_id"]] = reason
    manifest_id = _id("kbretr")
    s.insert("kb_retrieval_manifests", {
        "manifest_id": manifest_id, "tenant_id": tenant_id, "requesting_agent": requesting_agent,
        "case_id": case_id, "rule_ids_json": [r["rule_id"] for r in eligible],
        "eligibility_reasons_json": reasons, "ranked": 0, "created_at": s.now_ist(),
    })
    return {"manifest_id": manifest_id, "rules": eligible}


def _system_rule_identity(package: dict, rule: dict) -> tuple[str, str]:
    suffix = rule["rule_id"].lower().replace("-", "_")
    version = package["version_seq"]
    return f"kbrule_{suffix}_v{version}", f"kbsec_{suffix}_v{version}"


def _system_rule_params(package: dict, rule: dict) -> tuple[dict, str]:
    params = {**rule, "diagnostic_id": package["diagnostic_id"],
              "contract_version": package["contract_version"],
              "methodology_version": package["methodology_version"]}
    rule_hash = hashlib.sha256(json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    return params, rule_hash


def _insert_system_rule(package: dict, rule: dict, tenant_id: str, actor: str, now: str) -> None:
    rule_id, section_id = _system_rule_identity(package, rule)
    rule_text = f"{rule['title']}. {rule['user_help']}"
    params, rule_hash = _system_rule_params(package, rule)
    if not s.query_one("kb_sections", section_id=section_id):
        s.insert("kb_sections", {"section_id": section_id, "version_id": package["version_id"],
            "heading": f"{rule['rule_id']} — {rule['title']}",
            "body_markdown": rule_text, "order_seq": rule["display_order"]})
    if s.query_one("kb_rules", rule_id=rule_id):
        return
    s.insert("kb_rules", {
        "rule_id": rule_id, "section_id": section_id, "tenant_id": tenant_id,
        "document_id": package["document_id"], "version_id": package["version_id"],
        "rule_hash": rule_hash, "rule_text": rule_text, "category": "domain_fact",
        "trust_level": "human_confirmed", "lifecycle_state": "draft",
        "effective_date": now, "last_confirmed_date": now, "shelf_life_months": 120,
        "owner": "Data Quality", "reviewer": actor, "approver": actor,
        "related_tables_json": [], "related_columns_json": [],
        "related_tables_schema_hash_json": {}, "superseded_by_rule_id": None,
        "under_suspicion_reason": None, "is_synthetic": 0,
        "created_at": now, "updated_at": now, "source_rule_id": rule["rule_id"],
        "severity": rule["severity"], "rule_type": "row_completeness",
        "entity": "facility_period", "semantic_roles_json": rule["required_roles"],
        "regulatory_ref": None, "encoded_exceptions_json": [], "source_page": None,
        "binding_status": "unparsed", "binding_primitive": None,
        "binding_params_json": {}, "parse_hazards_json": [],
        "framework": "DataWorkbench T2D6",
    })


def _finish_system_diagnostic_activation(package: dict, actor: str) -> None:
    """Bind drafts, then publish the complete version in one DB transaction."""
    from dq_diagnostics.engines.cross_field.binder import bind_registered_rule

    rows = s.query("kb_rules", version_id=package["version_id"])
    states = {row["lifecycle_state"] for row in rows}
    if states == {"published"}:
        if any(row.get("binding_status") != "bound" for row in rows):
            raise KbError("published system diagnostic package contains an unbound rule")
        return
    if states != {"draft"}:
        # Archived/superseded packages are governance decisions, not seed damage.
        return
    by_id = {row["rule_id"]: row for row in rows}
    for rule in package["rules"]:
        rule_id, _ = _system_rule_identity(package, rule)
        params, expected_hash = _system_rule_params(package, rule)
        installed = by_id.get(rule_id)
        if installed is None or installed.get("rule_hash") != expected_hash:
            raise KbError(f"system KB rule {rule['rule_id']} is missing or has changed content")
        if installed.get("binding_status") == "bound":
            if installed.get("binding_primitive") != rule["primitive"]:
                raise KbError(f"system KB rule {rule['rule_id']} has the wrong primitive binding")
            continue
        bind_registered_rule(rule_id, rule["primitive"], params, actor,
                             registry="row_completeness")
    now = s.now_ist()
    with s.get_conn() as conn:
        cur = conn.execute(
            "UPDATE kb_rules SET lifecycle_state = 'published', updated_at = ? "
            "WHERE version_id = ? AND lifecycle_state = 'draft' AND binding_status = 'bound'",
            (now, package["version_id"]),
        )
        if cur.rowcount != len(package["rules"]):
            raise KbError("system diagnostic package activation was incomplete")
        conn.commit()


def ensure_system_diagnostic_package(package: dict, tenant_id: str = "bootstrap",
                                     actor: str = "system-kb-seed") -> dict:
    """Install an immutable, reviewed built-in diagnostic package once.

    This is the sole exceptional publication path for source-controlled system
    knowledge. It never edits or republishes an existing version: a different
    payload under the same stable version ID fails closed.
    """
    document_id, version_id = package["document_id"], package["version_id"]
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    package_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    existing_version = s.query_one("kb_document_versions", version_id=version_id)
    if existing_version:
        report = existing_version.get("conversion_report_json") or {}
        if report.get("package_hash") != package_hash:
            raise KbError(f"system KB version {version_id} exists with different content")
        installed_rules = s.query("kb_rules", version_id=version_id)
        if len(installed_rules) > len(package["rules"]):
            raise KbError(
                f"system KB version {version_id} contains unexpected additional rules"
            )
        for rule in package["rules"]:
            _insert_system_rule(package, rule, tenant_id, actor, s.now_ist())
        _finish_system_diagnostic_activation(package, actor)
        return {"document_id": document_id, "version_id": version_id,
                "package_hash": package_hash, "inserted": False}

    now = s.now_ist()
    if not s.query_one("kb_documents", document_id=document_id):
        s.insert("kb_documents", {
            "document_id": document_id, "tenant_id": tenant_id,
            "title": f"Test 2, Diagnostic 6 — {package['diagnostic']['name']}",
            "category_hint": "domain_fact", "is_synthetic": 0,
            "created_by": actor, "created_at": now,
        })
    markdown = "\n\n".join([
        f"# Test 2, Diagnostic 6 — {package['diagnostic']['name']}",
        package["methodology"],
        *[f"## {rule['rule_id']} — {rule['title']}\n\n{rule['user_help']}"
          for rule in package["rules"]],
    ])
    s.insert("kb_document_versions", {
        "version_id": version_id, "document_id": document_id,
        "version_seq": package["version_seq"],
        "original_filename": "row_completeness_v1.json",
        "original_media_type": "application/json", "original_sha256": package_hash,
        "original_bytes_ref": None, "original_size": len(canonical.encode("utf-8")),
        "converted_markdown": markdown,
        "converted_markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "converter_name": "system-diagnostic-package", "converter_version": "1",
        "conversion_warnings_json": [],
        "conversion_report_json": {"source": "source_controlled_system_package",
                                   "package_hash": package_hash,
                                   "schema_version": package["schema_version"]},
        "review_state": "approved", "reviewer": actor, "reviewed_at": now,
        "created_by": actor, "created_at": now,
    })
    for rule in package["rules"]:
        _insert_system_rule(package, rule, tenant_id, actor, now)
    _finish_system_diagnostic_activation(package, actor)
    return {"document_id": document_id, "version_id": version_id,
            "package_hash": package_hash, "inserted": True}


def retrieve_diagnostic_package_rules(tenant_id: str, diagnostic_id: int,
                                      version_id: str, case_id: str | None = None) -> dict:
    """Retrieve and record the exact governed rules used to build a run."""
    eligible = []
    reasons = {}
    for rule in s.query("kb_rules", tenant_id=tenant_id, lifecycle_state="published"):
        params = rule.get("binding_params_json") or {}
        if (rule.get("version_id") != version_id
                or params.get("diagnostic_id") != diagnostic_id
                or rule.get("trust_level") != "human_confirmed"
                or rule.get("binding_status") != "bound"):
            continue
        eligible.append(rule)
        reasons[rule["rule_id"]] = "published:human_confirmed:bound:diagnostic_version_match"
    eligible.sort(key=lambda row: (row.get("binding_params_json") or {}).get("display_order", 999))
    manifest_id = _id("kbretr")
    s.insert("kb_retrieval_manifests", {
        "manifest_id": manifest_id, "tenant_id": tenant_id,
        "requesting_agent": "row_completeness_engine", "case_id": case_id,
        "rule_ids_json": [row["rule_id"] for row in eligible],
        "eligibility_reasons_json": reasons, "ranked": 1, "created_at": s.now_ist(),
    })
    return {"manifest_id": manifest_id, "rules": eligible}


# --- Parse report + playback summary (KB-03/KB-10) ---------------------------

def _source_ref_for(rule: dict, section: dict | None = None) -> str:
    section = section or {}
    if rule.get("source_rule_id") and rule.get("source_page"):
        return f"{rule['source_rule_id']} (page {rule['source_page']})"
    if rule.get("source_rule_id"):
        return rule["source_rule_id"]
    if section.get("heading"):
        return f"section '{section['heading']}'"
    return rule["rule_id"]


def _reason_for_rule(rule: dict) -> str:
    status = rule.get("binding_status")
    if status == "bound":
        prim = rule.get("binding_primitive")
        return f"Matched registered primitive '{prim}'." if prim else "Bound to a registered primitive."
    if status == "reference-only":
        return (f"Parsed from a recognized rule table (type={rule.get('rule_type') or '?'}); "
               "no executable primitive is registered for it yet — displayable, not executable.")
    return ("No rule-table header was recognized for this content — captured as a single "
           "prose section; no structured rule_type could be recovered.")


def _rules_for_version(version_id: str) -> tuple[list[dict], dict[str, dict]]:
    sections = s.query("kb_sections", order_by="order_seq", version_id=version_id)
    sections_by_id = {sec["section_id"]: sec for sec in sections}
    rules = []
    for sec in sections:
        rules.extend(s.query("kb_rules", section_id=sec["section_id"]))
    return rules, sections_by_id


def parse_report(tenant_id: str, version_id: str) -> dict:
    """KB-10 / D-06 — a first-class artifact: per-rule parse status + reason
    for EVERY rule extracted from this version (draft or not), so an
    uploader can see exactly what happened and revise/re-upload (5-T5)."""
    version = s.query_one("kb_document_versions", version_id=version_id)
    if not version:
        raise KeyError("Unknown document version")
    require_document(tenant_id, version["document_id"])
    rules, sections_by_id = _rules_for_version(version_id)
    rows = []
    for rule in rules:
        section = sections_by_id.get(rule["section_id"])
        rows.append({
            "rule_id": rule["rule_id"], "source_rule_id": rule.get("source_rule_id"),
            "heading": (section or {}).get("heading"),
            "status": rule.get("binding_status"),
            "reason": _reason_for_rule(rule),
            "source_page": rule.get("source_page"),
            "source_ref": _source_ref_for(rule, section),
        })
    return {"document_id": version["document_id"], "version_id": version_id,
           "rules_total": len(rows), "rules": rows}


def playback_summary(tenant_id: str, version_id: str) -> dict:
    """KB-03 — the short, formatted confirmation shown right after a
    submit-for-review: what kind of knowledge was found, how many rules,
    breakdown by framework/type/severity, semantic roles referenced, binding
    status, unparsed items, and extraction hazards needing confirmation.
    Field shape matches docs/0.4.0/04-kb-contract.md §5 exactly."""
    version = s.query_one("kb_document_versions", version_id=version_id)
    if not version:
        raise KeyError("Unknown document version")
    doc = require_document(tenant_id, version["document_id"])
    rules, sections_by_id = _rules_for_version(version_id)

    by_framework: Counter = Counter()
    by_type: Counter = Counter()
    by_severity: Counter = Counter()
    binding: Counter = Counter()
    roles_referenced: set[str] = set()
    unparsed: list[dict] = []
    hazards: list[dict] = []

    for rule in rules:
        if rule.get("framework"):
            by_framework[rule["framework"]] += 1
        if rule.get("rule_type"):
            by_type[rule["rule_type"]] += 1
        if rule.get("severity"):
            by_severity[rule["severity"]] += 1
        for role in (rule.get("semantic_roles_json") or []):
            roles_referenced.add(role)
        status = rule.get("binding_status") or "unparsed"
        binding[status] += 1
        if status == "unparsed":
            unparsed.append({"source_ref": _source_ref_for(rule, sections_by_id.get(rule["section_id"])),
                            "reason": _reason_for_rule(rule)})
        for hz in (rule.get("parse_hazards_json") or []):
            hazards.append({"rule_id": rule["rule_id"], **hz})

    return {
        "document": {"document_id": doc["document_id"], "title": doc["title"]},
        "version": {"version_id": version_id, "version_seq": version.get("version_seq")},
        "rules_total": len(rules),
        "by_framework": dict(by_framework),
        "by_type": dict(by_type),
        "by_severity": dict(by_severity),
        "roles_referenced": sorted(roles_referenced),
        "binding": {
            "bound": binding.get("bound", 0),
            "reference_only": binding.get("reference-only", 0),
            "unparsed": binding.get("unparsed", 0),
        },
        "unparsed": unparsed,
        "hazards": hazards,
    }
