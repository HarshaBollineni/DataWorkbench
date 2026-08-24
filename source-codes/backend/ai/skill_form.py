"""Plan 8 / Aspect (a) — the governed skill .md FORM.

A skill .md is no longer a blank page; it is a FORM with two blocks:

  SYSTEM-OWNED  (locked, validated at load): ROLE CONTRACT, I/O SCHEMA,
                BREAK CONDITIONS, TOOL WHITELIST, OUTPUT FORMAT.
  USER-OWNED    (free text): DOMAIN EXPECTATIONS, EXAMPLES, PRIORITIES,
                ACCEPTANCE CRITERIA.

The .py control plane owns the contract; the user owns intent. Malformed files
(missing/empty required SYSTEM sections, or missing block markers) are REJECTED
at load (strict). ``save_prompt`` accepts edits to the USER block only — the
SYSTEM block is immutable from the UI.
"""
from __future__ import annotations

SYS_BEGIN = "<!-- SYSTEM-OWNED:BEGIN (locked — validated at load; do not edit) -->"
SYS_END = "<!-- SYSTEM-OWNED:END -->"
USER_BEGIN = "<!-- USER-OWNED:BEGIN (free text — edit domain intent here) -->"
USER_END = "<!-- USER-OWNED:END -->"

SYSTEM_REQUIRED = ["ROLE CONTRACT", "I/O SCHEMA", "BREAK CONDITIONS",
                   "TOOL WHITELIST", "OUTPUT FORMAT"]
USER_SECTIONS = ["DOMAIN EXPECTATIONS", "EXAMPLES", "PRIORITIES", "ACCEPTANCE CRITERIA"]


class SkillFormError(ValueError):
    """Raised when a skill .md violates the governed FORM contract."""


def _between(text: str, begin: str, end: str) -> str | None:
    i = text.find(begin)
    j = text.find(end)
    if i == -1 or j == -1 or j < i:
        return None
    return text[i + len(begin):j].strip()


def split(text: str) -> tuple[str, str]:
    """Return (system_block, user_block). Raises if the markers are missing."""
    sys_block = _between(text, SYS_BEGIN, SYS_END)
    usr_block = _between(text, USER_BEGIN, USER_END)
    if sys_block is None or usr_block is None:
        raise SkillFormError("missing SYSTEM-OWNED / USER-OWNED block markers")
    return sys_block, usr_block


def sections(block: str) -> dict[str, str]:
    """Parse '## NAME' headers in a block into {NAME: body}."""
    out: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in block.splitlines():
        if line.startswith("## "):
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = line[3:].strip().upper()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def validate(text: str) -> None:
    """Strict load-time check. Raises SkillFormError on any violation."""
    sys_block, _ = split(text)
    secs = sections(sys_block)
    missing = [s for s in SYSTEM_REQUIRED if not secs.get(s)]
    if missing:
        raise SkillFormError(f"SYSTEM-OWNED block missing/empty section(s): {missing}")


def system_block(text: str) -> str:
    return split(text)[0]


def user_block(text: str) -> str:
    return split(text)[1]


def compose(sys_block: str, usr_block: str) -> str:
    return (f"{SYS_BEGIN}\n{sys_block.strip()}\n{SYS_END}\n\n"
            f"{USER_BEGIN}\n{usr_block.strip()}\n{USER_END}\n")


def is_governed_form(text: str) -> bool:
    try:
        validate(text)
        return True
    except SkillFormError:
        return False


def _tool_whitelist(agent_key: str) -> str:
    """List the registry tools this role is granted (source of truth = registry)."""
    try:
        from ai import tool_registry as tr
        # ai.test_manager / ai.dict_ingest (which self-registered extra tools
        # here) were deleted in Phase 3 with the verify_plan8.py rebuild; the
        # catalogue is complete once ai.tool_registry itself has imported.
        ids = [t["tool_id"] for t in tr.list_tools(agent_key)]
    except Exception:  # noqa: BLE001
        ids = []
    if not ids:
        return "- (none granted — this role calls no registry tools)"
    return "\n".join(f"- `{i}`" for i in ids)


def wrap_default(agent_key: str, role: str, raw_prompt: str) -> str:
    """Build a governed FORM .md from an agent's existing prompt text.

    The original prompt is preserved verbatim under USER-OWNED DOMAIN
    EXPECTATIONS so behaviour is unchanged; the SYSTEM-OWNED contract is
    generated from authoritative sources (role + tool registry).
    """
    sys_block = (
        f"## ROLE CONTRACT\n{role}. This role's model, temperature and effort are "
        f"system-owned (see ai/control_plane.py); they are NOT editable here.\n\n"
        f"## I/O SCHEMA\nInputs and outputs are enforced by the Python call site. "
        f"This agent must respond in the STRICT structured form its caller parses "
        f"(see OUTPUT FORMAT); malformed output is rejected/repaired by the harness.\n\n"
        f"## BREAK CONDITIONS\nLoop control is owned by the orchestrator (Gauss / "
        f"Feynman): bounded iterations, consensus, and the hard LLM-pass budget. "
        f"This agent does not decide when the loop stops.\n\n"
        f"## TOOL WHITELIST\nTools this role may call (granted by the control plane; "
        f"the .py registry enforces the grant):\n{_tool_whitelist(agent_key)}\n\n"
        f"## OUTPUT FORMAT\nReturn ONLY the structured payload the caller expects "
        f"(typically STRICT JSON). No prose outside the structure."
    )
    usr_block = (
        f"## DOMAIN EXPECTATIONS\n{raw_prompt.strip()}\n\n"
        f"## EXAMPLES\n(Add concrete worked examples of good output here.)\n\n"
        f"## PRIORITIES\n(State what matters most for this role — accuracy, "
        f"coverage, conservatism, etc.)\n\n"
        f"## ACCEPTANCE CRITERIA\n(Describe, in plain language, what a correct "
        f"result looks like.)"
    )
    return compose(sys_block, usr_block)


def merge_user_edit(current_text: str, edited_text: str) -> str:
    """Protect the SYSTEM block: keep it from ``current_text``, take the USER
    block from ``edited_text``. Raises if either side is malformed."""
    cur_sys, _ = split(current_text)
    edited_sys, edited_usr = split(edited_text)
    if edited_sys.strip() != cur_sys.strip():
        raise SkillFormError(
            "SYSTEM-OWNED block is read-only and cannot be modified from the editor.")
    return compose(cur_sys, edited_usr)
