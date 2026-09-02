"""
agent.py - the AI override layer.

Sits on top of the regex predictions in derivation.py and does two passes:

  Pass 1  TAG      given the role definitions, a data dictionary (if supplied)
                   and a compact profile of every column, propose a column for
                   each role.
  Pass 2  REVIEW   given pass 1, the regex prediction and the same evidence,
                   decide which bindings are WRONG and correct them.

Pass 2 exists because pass 1 sees roles in isolation and will happily hand the
same column to two roles, or bind a forward-looking label to a status role. The
review pass sees the whole assignment at once and is what catches those.

Both passes are strictly advisory. The human still confirms every binding in
bind_cli.py; the agent only changes what is shown as the prediction.

Usage
-----
    python agent.py --folder .                 # print what the agent would do
    python agent.py --folder . --dry-run       # print the prompt, call nothing
    python bind_cli.py --folder . --agent      # use it inside the CLI

Offline
-------
With no API key configured, the agent falls back to a deterministic pass that
uses the data dictionary text only (no model call). That is weaker than the
model but strictly better than nothing when a dictionary exists, and it means
this layer never becomes a hard dependency on network access.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

# The product's embedded Python uses an isolated ``._pth`` configuration and
# does not automatically add a script's directory. Keep this standalone
# experiment able to resolve its sibling modules without changing that shared
# environment.
_EXPERIMENT_ROOT = str(Path(__file__).resolve().parent)
if _EXPERIMENT_ROOT not in sys.path:
    sys.path.insert(0, _EXPERIMENT_ROOT)

import derivation as D

# ============================================================================
#  CREDENTIALS
# ============================================================================
# This file holds NO credentials or endpoints. All connection values are read,
# in order of precedence, from:
#   1. environment variables (KB_AGENT_API_KEY / _ENDPOINT / _MODEL / _STYLE)
#   2. agent_config.json sitting next to this file
# The blanks below are last-resort fallbacks only, so the module imports cleanly
# when neither source is present; they carry no values.

API_KEY = ""                # env: KB_AGENT_API_KEY   / config: api_key
ENDPOINT = ""               # env: KB_AGENT_ENDPOINT  / config: endpoint
MODEL = ""                  # env: KB_AGENT_MODEL     / config: model
API_STYLE = "openai"        # env: KB_AGENT_STYLE     / config: style  (behaviour flag, not a secret)
#   "openai"    -> chat/completions, header Authorization: Bearer
#                  (also use this for Azure OpenAI and most internal gateways)
#   "anthropic" -> /v1/messages, header x-api-key + anthropic-version

MAX_TOKENS = 4000
TIMEOUT_SECONDS = 120
EXTRA_HEADERS: dict[str, str] = {}                    # e.g. {"x-gateway-tenant": "risk"}

# ============================================================================


_CONFIG_FILE = "agent_config.json"


def _file_config() -> dict:
    """Optional agent_config.json beside this file. Missing or unreadable -> {}."""
    path = Path(__file__).with_name(_CONFIG_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_config() -> dict:
    f = _file_config()

    def pick(env_name: str, file_key: str, fallback: str) -> str:
        return str(os.environ.get(env_name) or f.get(file_key) or fallback).strip()

    return {
        "api_key": pick("KB_AGENT_API_KEY", "api_key", API_KEY),
        "endpoint": pick("KB_AGENT_ENDPOINT", "endpoint", ENDPOINT),
        "model": pick("KB_AGENT_MODEL", "model", MODEL),
        "style": pick("KB_AGENT_STYLE", "style", API_STYLE).lower(),
    }


def configured() -> bool:
    c = resolve_config()
    return bool(c["api_key"] and c["endpoint"] and c["model"])


# ----------------------------------------------------------------------------
# 1. DATA DICTIONARY
# ----------------------------------------------------------------------------

_NAME_HEADERS = ["column", "column_name", "field", "field_name", "name", "variable",
                 "attribute", "physical_name"]
_DESC_HEADERS = ["description", "definition", "meaning", "business_definition",
                 "comment", "notes", "label", "long_name"]


def _pick(cols: list[str], wanted: list[str]) -> str | None:
    norm = {re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_"): c for c in cols}
    for w in wanted:
        if w in norm:
            return norm[w]
    for k, orig in norm.items():
        if any(w in k for w in wanted):
            return orig
    return None


def load_dictionary(folder: str | Path, dataset_name: str | None = None) -> dict[str, str]:
    """
    Find a data dictionary and return {column_name: description}.

    Looked for, in order:
      1. a sheet in the dataset workbook whose name mentions 'dict'
      2. any *dict*.csv / *dict*.xlsx / *dict*.json / *dict*.md in the folder
    Returns {} when there is none, which is a supported state.
    """
    folder = Path(folder)

    if dataset_name and (folder / dataset_name).suffix.lower() in (".xlsx", ".xlsm"):
        try:
            xl = pd.ExcelFile(folder / dataset_name)
            for sheet in xl.sheet_names:
                if "dict" in sheet.lower() or "glossar" in sheet.lower():
                    return _frame_to_dict(xl.parse(sheet))
        except Exception:
            pass

    for p in sorted(folder.iterdir()):
        n = p.name.lower()
        if "dict" not in n and "glossar" not in n and "schema" not in n:
            continue
        try:
            if p.suffix.lower() == ".json":
                raw = json.loads(p.read_text())
                if isinstance(raw, dict):
                    return {str(k): str(v) for k, v in raw.items()}
                if isinstance(raw, list):
                    return _frame_to_dict(pd.DataFrame(raw))
            elif p.suffix.lower() == ".csv":
                return _frame_to_dict(pd.read_csv(p))
            elif p.suffix.lower() in (".xlsx", ".xlsm"):
                return _frame_to_dict(pd.read_excel(p))
            elif p.suffix.lower() in (".md", ".markdown"):
                found = _md_to_dict(p.read_text(encoding="utf-8", errors="replace"))
                if found:
                    return found
        except Exception:
            continue
    return {}


def _frame_to_dict(df: pd.DataFrame) -> dict[str, str]:
    name_col = _pick(list(df.columns), _NAME_HEADERS)
    desc_col = _pick(list(df.columns), _DESC_HEADERS)
    if not name_col:
        return {}
    if not desc_col:
        others = [c for c in df.columns if c != name_col]
        desc_col = others[0] if others else name_col
    out = {}
    for _, row in df.iterrows():
        k = str(row[name_col]).strip()
        v = str(row[desc_col]).strip()
        if k and k.lower() != "nan":
            out[k] = v if v.lower() != "nan" else ""
    return out


def _header_index(header: list[str], wanted: list[str]) -> int | None:
    """Position of the first header cell matching one of the wanted names."""
    norm = [re.sub(r"[^a-z0-9]+", "_", str(c).lower()).strip("_") for c in header]
    for w in wanted:
        if w in norm:
            return norm.index(w)
    for w in wanted:
        for i, h in enumerate(norm):
            if w in h:
                return i
    return None


def _md_to_dict(text: str) -> dict[str, str]:
    """
    Harvest {column: description} from every markdown pipe-table in the text.
    A table's name/description columns are found from its header row, so this
    works for any 'column | type | notes' style schema. A non-empty description
    is preferred when the same column appears in more than one table.
    """
    out: dict[str, str] = {}

    def flush(block: list[list[str]]) -> None:
        if len(block) < 2:
            return
        name_i = _header_index(block[0], _NAME_HEADERS)
        desc_i = _header_index(block[0], _DESC_HEADERS)
        if name_i is None:
            return
        for cells in block[2:]:          # skip the header and the |---| separator
            if name_i >= len(cells):
                continue
            name = cells[name_i].strip()
            desc = cells[desc_i].strip() if desc_i is not None and desc_i < len(cells) else ""
            if not name or set(name) <= {"-", ":"}:
                continue
            if name not in out or (desc and not out[name]):
                out[name] = desc

    block: list[list[str]] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|"):
            block.append([c.strip() for c in s.strip("|").split("|")])
        elif block:
            flush(block)
            block = []
    flush(block)
    return out


# ----------------------------------------------------------------------------
# 2. EVIDENCE THE AGENT SEES
# ----------------------------------------------------------------------------

def build_summary(df: pd.DataFrame, profiles: dict, dictionary: dict[str, str],
                  max_values: int = 6) -> list[dict]:
    """One compact record per column. Kept small so the whole schema fits in one call."""
    rows = []
    for col, p in profiles.items():
        rec = {
            "column": col,
            "dtype": p.dtype,
            "distinct": p.n_distinct,
            "null_pct": round(p.null_frac * 100, 1),
            "examples": p.sample[:3],
        }
        if dictionary.get(col):
            rec["dictionary"] = dictionary[col][:300]
        if p.n_distinct <= max_values and p.n_distinct > 0:
            vc = df[col].dropna().value_counts().head(max_values)
            rec["all_values"] = {str(k): int(v) for k, v in vc.items()}
        rows.append(rec)
    return rows


def role_spec_for_prompt(roles: list[str], kb: dict) -> list[dict]:
    """Role definitions, plus the KB's own words about what each rule reads."""
    look_at = {r["id"]: r.get("look_at", "") for r in kb.get("rules", [])}
    out = []
    for role in roles:
        used_by = [rid for rid, la in look_at.items()
                   if role in D.roles_for_rule({"look_at": la})]
        out.append({
            "role": role,
            "label": D.ROLES[role]["label"],
            "kind": D.ROLES[role]["kind"],
            "used_by_rules": used_by,
        })
    return out


CONTEXT = """You are binding columns of a credit risk dataset to the semantic roles that a
data-quality rulebook reads. The rulebook never names a column; it names roles.

Rules that matter for your judgement:
- A role that no column genuinely fills must be left null. A wrong binding is far
  worse than an absent one, because an absent role makes the affected rules return
  NOT-APPLICABLE, which is a correct and informative outcome, whereas a wrong
  binding produces a confident and false verdict.
- 'default status' means the CURRENT default state of the facility. A
  forward-looking modelling target (default_12m, default_next_quarter, a PD
  estimate) is NOT a default status.
- 'default date' means the date a default event occurred. A maturity date, a
  reporting date, or an origination date is NOT a default date. If no column
  records when a default happened, return null.
- 'closure marker' means the flag saying a facility has left the panel legitimately
  (paid off, redeemed). It is not the same as workout status.
- 'workout status' means the recovery-process state of a defaulted case
  (in workout / cured / written off / paid off).
- 'segment' is the partition used both to stratify results and to define coverage
  cells. Prefer a column with a useful number of categories over a binary one.
- 'period' is the observation date of the panel row, not the origination or
  maturity date.
- A constant column (one distinct value) cannot be a status or a marker, because it
  never marks anything.
- Two different roles must not be bound to the same column unless that column
  genuinely serves both.

Answer with JSON only. No prose, no markdown fences."""


def tag_prompt(role_specs: list[dict], summary: list[dict], kb_id: str) -> str:
    return f"""{CONTEXT}

PASS 1 of 2 - TAG.
Knowledge base: {kb_id}

ROLES TO FILL:
{json.dumps(role_specs, indent=1)}

COLUMNS AVAILABLE (with data dictionary text where present, and full value lists
for low-cardinality columns):
{json.dumps(summary, indent=1, default=str)}

Return exactly this JSON shape:
{{"bindings": [{{"role": "...", "column": "..." or null,
                "confidence": "high"|"medium"|"low",
                "reason": "one short sentence citing the dictionary or the values"}}]}}"""


def review_prompt(role_specs: list[dict], summary: list[dict], regex_binding: dict,
                  agent_binding: dict, kb_id: str) -> str:
    return f"""{CONTEXT}

PASS 2 of 2 - REVIEW.
Knowledge base: {kb_id}

Two independent passes have proposed bindings. The first is a pattern matcher on
column names; the second is your own pass 1. Where they disagree, or where either
looks wrong against the dictionary and the values, decide the correct answer.

PATTERN MATCHER PROPOSED:
{json.dumps(regex_binding, indent=1)}

YOUR PASS 1 PROPOSED:
{json.dumps(agent_binding, indent=1)}

ROLES:
{json.dumps(role_specs, indent=1)}

COLUMNS:
{json.dumps(summary, indent=1, default=str)}

List ONLY the roles whose binding should change from the pattern matcher's answer.
Leave everything else out. Return exactly this JSON shape:
{{"corrections": [{{"role": "...", "from": "..." or null, "to": "..." or null,
                   "confidence": "high"|"medium"|"low",
                   "reason": "one short sentence saying why the old binding was wrong"}}]}}"""


# ----------------------------------------------------------------------------
# 3. THE MODEL CALL
# ----------------------------------------------------------------------------

class AgentError(RuntimeError):
    pass


def call_model(prompt: str) -> str:
    cfg = resolve_config()
    if not (cfg["api_key"] and cfg["endpoint"] and cfg["model"]):
        raise AgentError("no credentials configured; fill in the block at the top of agent.py "
                         "or set KB_AGENT_API_KEY / KB_AGENT_ENDPOINT / KB_AGENT_MODEL")

    # Resolve the wire format. A "/responses" endpoint always uses the Responses
    # API schema regardless of the style flag, since chat-completions bodies are
    # rejected there.
    style = cfg["style"]
    if style != "anthropic" and cfg["endpoint"].rstrip("/").endswith("/responses"):
        style = "responses"

    if style == "anthropic":
        body = {"model": cfg["model"], "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json",
                   "x-api-key": cfg["api_key"],
                   "anthropic-version": "2023-06-01"}
    elif style == "responses":
        body = {"model": cfg["model"], "input": prompt,
                "max_output_tokens": MAX_TOKENS}
        headers = {"content-type": "application/json",
                   "authorization": f"Bearer {cfg['api_key']}"}
    else:
        body = {"model": cfg["model"], "max_tokens": MAX_TOKENS,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"content-type": "application/json",
                   "authorization": f"Bearer {cfg['api_key']}"}
    headers.update(EXTRA_HEADERS)

    req = urllib.request.Request(cfg["endpoint"], data=json.dumps(body).encode(),
                                headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
            payload = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise AgentError(f"HTTP {e.code} from {cfg['endpoint']}: {e.read().decode()[:400]}")
    except Exception as e:
        raise AgentError(f"call to {cfg['endpoint']} failed: {e}")

    if style == "anthropic":
        return "".join(b.get("text", "") for b in payload.get("content", [])
                       if b.get("type") == "text")
    if style == "responses":
        return _responses_text(payload)
    return payload["choices"][0]["message"]["content"]


def _responses_text(payload: dict) -> str:
    """Pull the assistant text out of a Responses API reply, skipping reasoning items."""
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]
    chunks = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") in ("output_text", "text") and part.get("text"):
                chunks.append(part["text"])
    return "".join(chunks)


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in fences or add a sentence. Recover the object."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, depth = t.find("{"), 0
    if start < 0:
        raise AgentError(f"no JSON object in model reply: {text[:200]}")
    for i, ch in enumerate(t[start:], start):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return json.loads(t[start:i + 1])
    raise AgentError(f"unterminated JSON in model reply: {text[:200]}")


# ----------------------------------------------------------------------------
# 4. OFFLINE FALLBACK - dictionary text only, no model
# ----------------------------------------------------------------------------

DICT_HINTS = {
    "facility_id": ["facility identifier", "facility id", "loan identifier", "unique loan",
                    "account number", "contract id"],
    "period": ["reporting date", "observation date", "as of date", "snapshot",
               "reporting period", "reporting quarter"],
    "origination_date": ["origination date", "date the loan was originated", "funding date",
                         "disbursement"],
    "default_status": ["currently in default", "default flag", "default indicator",
                       "regulatory default", "non-performing"],
    "workout_status": ["workout", "recovery status", "stage of recovery", "collections status"],
    "closure_marker": ["paid off", "loan has been repaid", "closed", "redeemed", "terminated"],
    "segment": ["segment", "portfolio", "property type", "asset class", "sub-portfolio"],
    "default_date": ["date of default", "default date", "date the default occurred"],
    "realised_loss": ["realised loss", "realized loss", "loss amount", "amount written off"],
    "recovery_amount": ["recovery amount", "amount recovered", "total recoveries"],
    "resolution_date": ["resolution date", "date the case closed", "workout end",
                        "final recovery"],
    "term_maturity_date": ["maturity date", "date the loan matures", "contractual term"],
}

_FORWARD = re.compile(r"\b(next|forward|following|within (the )?(next )?\d+|predicted|forecast|"
                      r"probability)\b", re.I)


def _roles_claimed_by(desc: str) -> set[str]:
    """Which roles' hint phrases this dictionary description matches."""
    low = desc.lower()
    return {role for role, hints in DICT_HINTS.items() if any(h in low for h in hints)}


def _best_column_for(role: str, dictionary: dict[str, str], by_col: dict) -> str | None:
    """
    The column whose dictionary text matches this role. A column that varies is
    preferred over a constant one, but a constant column is still returned as a
    last resort: a default flag that is 0 on every row means "this extract
    contains no defaults", which is a finding worth surfacing, not a reason to
    pretend the column is absent.
    """
    fallback = None
    for col, desc in dictionary.items():
        if col not in by_col or not desc:
            continue
        claimed = _roles_claimed_by(desc)
        if role not in claimed or _FORWARD.search(desc):
            continue
        if by_col[col]["distinct"] <= 1:
            fallback = fallback or col
            continue
        return col
    return fallback


def offline_review(roles: list[str], regex_binding: dict, dictionary: dict[str, str],
                   summary: list[dict]) -> list[dict]:
    """
    Deterministic stand-in for the model, driven by the dictionary text alone.

    Three checks, in order:
      1. the bound column's description is forward-looking, so it cannot be a
         current-state role;
      2. the bound column's description matches a DIFFERENT role and not this
         one, so it has been mis-assigned;
      3. no column is bound but one column's description matches this role.
    In cases 1 and 2 a replacement is proposed where the dictionary supports one,
    and the role is unbound where it does not - an unbound role is a correct
    outcome, a wrong one is not.
    """
    corrections = []
    by_col = {r["column"]: r for r in summary}

    def correction(role, frm, to, reason):
        corrections.append({"role": role, "from": frm, "to": to,
                            "confidence": "medium", "reason": reason})

    for role in roles:
        current = regex_binding.get(role)
        label = D.ROLES[role]["label"]
        desc = dictionary.get(current, "") if current else ""
        replacement = _best_column_for(role, dictionary, by_col)

        if current and desc and _FORWARD.search(desc):
            correction(role, current, replacement,
                       f"dictionary describes '{current}' as forward-looking, which cannot be "
                       f"a {label}"
                       + (f"; '{replacement}' describes the current state instead"
                          if replacement else "; no column describes it, so leave it unbound"))
            continue

        if current and desc:
            claimed = _roles_claimed_by(desc)
            if claimed and role not in claimed:
                other = D.ROLES[sorted(claimed)[0]]["label"]
                correction(role, current, replacement,
                           f"dictionary describes '{current}' as {other}, not {label}"
                           + (f"; '{replacement}' matches instead" if replacement
                              else "; nothing matches, so leave it unbound"))
                continue

        if replacement and replacement != current:
            correction(role, current, replacement,
                       f"dictionary text for '{replacement}' matches {label}")

    return corrections


# ----------------------------------------------------------------------------
# 5. ORCHESTRATION
# ----------------------------------------------------------------------------

@dataclass
class AgentResult:
    kb_id: str
    binding: dict[str, str | None]        # final, after corrections
    corrections: list[dict]               # what changed and why
    mode: str                             # "model" | "offline" | "skipped"
    note: str = ""


def _type_compatible(role: str, column: str, profiles: dict) -> bool:
    """A model override must respect the role's value type, not just exist as a column."""
    p = profiles.get(column)
    if p is None:
        return True
    kind = D.ROLES[role]["kind"]
    if kind == "date" and p.is_numeric and not (p.is_datetime or getattr(p, "is_datelike", False)):
        return False
    if kind == "status" and p.is_datetime:
        return False
    if kind == "amount" and not p.is_numeric:
        return False
    return True


def run_for_kb(kb_id: str, kb: dict, df: pd.DataFrame, profiles: dict,
               regex_binding: dict, dictionary: dict[str, str],
               dry_run: bool = False, verbose: bool = True) -> AgentResult:
    roles = list(regex_binding)
    specs = role_spec_for_prompt(roles, kb)
    summary = build_summary(df, profiles, dictionary)
    final = dict(regex_binding)

    if dry_run:
        print(tag_prompt(specs, summary, kb_id))
        print("\n" + "#" * 78 + "\n")
        print(review_prompt(specs, summary, regex_binding, regex_binding, kb_id))
        return AgentResult(kb_id, final, [], "skipped", "dry run, nothing called")

    if not configured():
        corr = offline_review(roles, regex_binding, dictionary, summary)
        for c in corr:
            final[c["role"]] = c["to"]
        note = ("no credentials configured, ran the offline dictionary pass"
                + ("" if dictionary else " with no dictionary available, so nothing to go on"))
        if verbose:
            print(f"  [{kb_id}] {note}")
        return AgentResult(kb_id, final, corr, "offline", note)

    # Pass 1 - tag
    pass1 = {}
    pass1_ok = pass2_ok = True
    try:
        raw = parse_json(call_model(tag_prompt(specs, summary, kb_id)))
        for b in raw.get("bindings", []):
            if b.get("role") in roles:
                pass1[b["role"]] = b.get("column")
        if verbose:
            print(f"  [{kb_id}] pass 1 tagged {sum(1 for v in pass1.values() if v)}/{len(roles)} roles")
    except AgentError as e:
        pass1_ok = False
        if verbose:
            print(f"  [{kb_id}] pass 1 failed: {e}")
        pass1 = dict(regex_binding)

    # Pass 2 - review against the regex answer
    corrections: list[dict] = []
    try:
        raw = parse_json(call_model(review_prompt(specs, summary, regex_binding, pass1, kb_id)))
        for c in raw.get("corrections", []):
            role, to = c.get("role"), c.get("to")
            if role not in roles:
                continue
            if to is not None and to not in df.columns:
                if verbose:
                    print(f"  [{kb_id}] ignored correction to unknown column {to!r}")
                continue
            if to is not None and not _type_compatible(role, to, profiles):
                if verbose:
                    print(f"  [{kb_id}] rejected {role} -> {to!r}: value type is wrong for this role")
                continue
            c["from"] = regex_binding.get(role)
            if c["from"] == to:
                continue
            corrections.append(c)
            final[role] = to
        if verbose:
            print(f"  [{kb_id}] pass 2 returned {len(corrections)} corrections")
    except AgentError as e:
        pass2_ok = False
        if verbose:
            print(f"  [{kb_id}] pass 2 failed, keeping the pattern-matcher bindings: {e}")

    note = ("" if (pass1_ok or pass2_ok)
            else "model unreachable on both passes; bindings shown are the pattern matcher's, not the agent's")
    return AgentResult(kb_id, final, corrections, "model", note)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder", default=".")
    ap.add_argument("--dataset", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompts and call nothing")
    args = ap.parse_args()

    folder = Path(args.folder)
    kbs = D.load_kbs(folder)
    df, name = D.load_dataset(folder, args.dataset)
    profiles = D.profile(df)
    dictionary = load_dictionary(folder, name)

    cfg = resolve_config()
    print("=" * 78)
    print(f"dataset    {name}   {len(df):,} rows x {len(df.columns)} columns")
    print(f"dictionary {len(dictionary)} of {len(df.columns)} columns described"
          if dictionary else "dictionary NOT FOUND - the agent runs on the data summary alone")
    print(f"model      {cfg['model'] or '(unset)'} via {cfg['endpoint'] or '(unset)'} "
          f"[{cfg['style']}]  credentials: {'set' if cfg['api_key'] else 'NOT SET'}")
    print("=" * 78)

    for kb_id, kb in kbs.items():
        roles = D.roles_for_kb(kb)
        regex_binding = {}
        for r in roles:
            c = D.predict(r, df, profiles)
            regex_binding[r] = c[0].column if c else None

        res = run_for_kb(kb_id, kb, df, profiles, regex_binding, dictionary,
                         dry_run=args.dry_run)
        if args.dry_run:
            continue

        print(f"\n--- {kb_id}  [{res.mode}]")
        if res.note:
            print(f"    note: {res.note}")
        if not res.corrections:
            print("    no overrides; the agent agrees with the pattern matcher"
                  if not res.note else "    (no overrides applied)")
        for c in res.corrections:
            print(f"    {D.ROLES[c['role']]['label']}: {c['from']} -> {c['to']}  "
                  f"({c.get('confidence','?')})")
            print(f"      {c.get('reason','')}")


if __name__ == "__main__":
    main()
