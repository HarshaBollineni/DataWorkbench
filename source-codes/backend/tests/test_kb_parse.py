"""Phase 5 (KB-01..KB-16) — table-aware parse behavioral tests.

See docs/0.4.0/04-kb-contract.md. Standalone-runnable
(``python -m unittest tests.test_kb_parse``); same SYSTEM_DB_PATH/
KB_STORAGE_DIR-at-import-time sandboxing convention as test_kb.py.

5-T1 — S9 (synthetic-kb/KB_cross_field_reference_2.pdf) produces exactly 49
       rule records, with framework/type/severity breakdowns matching an
       INDEPENDENT oracle computed directly from the raw PDF in this file
       (a second, from-scratch pdfplumber read — deliberately NOT reusing
       kb.py's own extraction code — plus a check against the specific
       numbers docs/0.4.0/04-kb-contract.md §1.6 publishes).
5-T2 — a prose document with "## " headings still parses one rule per
       section, unaffected by the table-aware changes (regression guard).
5-T5 — the parse report lists every rule (49 for S9) with status + reason,
       retrievable via kb.parse_report() (routers/v3.py wires the API).
5-T7 — three distinct, clear rejections: scanned/image PDF, corrupt DOCX,
       oversized file.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from collections import Counter
from pathlib import Path

_TMP_DB = Path(tempfile.gettempdir()) / "archimedes-test-kb-parse.db"
_TMP_KB_STORAGE = Path(tempfile.gettempdir()) / "archimedes-test-kb-parse-storage"
if _TMP_DB.exists():
    _TMP_DB.unlink()
if _TMP_KB_STORAGE.exists():
    shutil.rmtree(_TMP_KB_STORAGE)
os.environ["SYSTEM_DB_PATH"] = str(_TMP_DB)
os.environ["KB_STORAGE_DIR"] = str(_TMP_KB_STORAGE)
os.environ.pop("SYSTEM_DB_BACKUP_PATH", None)

import system_db as s  # noqa: E402
import kb  # noqa: E402
from kb_convert import ConversionError, convert_to_markdown  # noqa: E402
from seeds.taxonomy_seed import BOOTSTRAP_TENANT, seed_platform, seed_taxonomy  # noqa: E402

TENANT = BOOTSTRAP_TENANT
BACKEND_ROOT = Path(__file__).resolve().parent.parent
S9_PDF = (BACKEND_ROOT.parent / "synthetic-kb" / "KB_cross_field_reference_2.pdf").resolve()

# docs/0.4.0/04-kb-contract.md §1.6 — "For S9 the summary must reach exactly:
# 49 rules · IFRS9 11 / IRB 36 / both 2 · conditional 13, date-ordering 3,
# domain 26, identity 3, inequality 4 · CRITICAL 6, MATERIAL 21, MINOR 22".
CONTRACT_TOTAL = 49
CONTRACT_BY_FRAMEWORK = {"IRB": 36, "IFRS9": 11, "both": 2}
CONTRACT_BY_TYPE = {"conditional": 13, "date_ordering": 3, "domain": 26, "identity": 3, "inequality": 4}
CONTRACT_BY_SEVERITY = {"CRITICAL": 6, "MATERIAL": 21, "MINOR": 22}


# --- Independent oracle: a second, from-scratch pdfplumber read ------------
# Deliberately does NOT import anything from kb.py/kb_convert.py — this is a
# separate extraction path so a bug shared between kb.py's parser and this
# test wouldn't produce a false pass. Mirrors the *general shape* of the
# document (tables with a Sev/Type/Reg-reference-style header, "<label> (N
# rules)" group headings) without hardcoding any of the 49 rules' content.
def _oracle_counts(pdf_path: Path) -> dict:
    import pdfplumber

    severity_map = {"crit": "CRITICAL", "mate": "MATERIAL", "mino": "MINOR"}
    group_re = re.compile(r"^([A-Za-z][A-Za-z0-9+/_ -]{0,40}?)\s*\(\s*(\d+)\s+rules?\s*\)\s*$")

    by_framework: Counter = Counter()
    by_type: Counter = Counter()
    by_severity: Counter = Counter()
    total = 0
    group_label = None
    group_remaining = 0

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            tables = sorted((t for t in page.find_tables() if len(t.rows) >= 2),
                            key=lambda t: t.bbox[1])
            cursor_top = 0.0
            for t in tables:
                top = t.bbox[1]
                # A group heading ("IRB (36 rules)") is a standalone text
                # line positioned ABOVE this table and below the previous
                # one — read the vertical band strictly between them so a
                # later heading on the same page (e.g. page 3's "IRB+IFRS9
                # (2 rules)") never gets attributed to an earlier table.
                if top > cursor_top:
                    band = (page.crop((0, cursor_top, page.width, top)).extract_text() or "")
                    for line in band.splitlines():
                        m = group_re.match(line.strip())
                        if m:
                            group_label, group_remaining = m.group(1).strip(), int(m.group(2))
                rows = t.extract()
                header = [(c or "").strip().lower() for c in (rows[0] if rows else [])]
                if "sev" in header and "type" in header:
                    sev_idx, type_idx = header.index("sev"), header.index("type")
                    for row in rows[1:]:
                        total += 1
                        sev_raw = re.sub(r"\s+", "", (row[sev_idx] or "")).strip().lower()
                        by_severity[severity_map.get(sev_raw, sev_raw.upper())] += 1
                        type_raw = re.sub(r"\s+", " ", (row[type_idx] or "")).strip()
                        norm_type = re.sub(r"\s+", "_", type_raw.split("/")[0].strip().lower())
                        by_type[norm_type] += 1
                        if group_remaining > 0 and group_label:
                            fw = "both" if ("+" in group_label or "&" in group_label) else group_label.upper()
                            by_framework[fw] += 1
                            group_remaining -= 1
                cursor_top = max(cursor_top, t.bbox[3])

    return {"total": total, "by_framework": dict(by_framework),
            "by_type": dict(by_type), "by_severity": dict(by_severity)}


@unittest.skipUnless(S9_PDF.exists(), f"S9 fixture not found at {S9_PDF}")
class S9ParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()
        cls.oracle = _oracle_counts(S9_PDF)
        content = S9_PDF.read_bytes()
        upload = kb.upload_document(TENANT, S9_PDF.name, "application/pdf", content, None, "tester")
        cls.version_id = upload["version"]["version_id"]
        cls.preview = kb.submit_for_review(TENANT, cls.version_id, "tester", "domain_fact")
        cls.playback = kb.playback_summary(TENANT, cls.version_id)

    def test_oracle_matches_the_contract_doc(self):
        """Sanity check on the oracle itself before trusting it as a test
        fixture: it must reproduce the contract doc's own published numbers."""
        self.assertEqual(self.oracle["total"], CONTRACT_TOTAL)
        self.assertEqual(self.oracle["by_framework"], CONTRACT_BY_FRAMEWORK)
        self.assertEqual(self.oracle["by_type"], CONTRACT_BY_TYPE)
        self.assertEqual(self.oracle["by_severity"], CONTRACT_BY_SEVERITY)

    def test_exactly_49_rule_records_produced(self):
        self.assertEqual(len(self.preview["rules"]), CONTRACT_TOTAL)
        self.assertEqual(self.playback["rules_total"], CONTRACT_TOTAL)
        self.assertEqual(self.playback["rules_total"], self.oracle["total"])

    def test_by_framework_matches_oracle_and_contract(self):
        self.assertEqual(self.playback["by_framework"], self.oracle["by_framework"])
        self.assertEqual(self.playback["by_framework"], CONTRACT_BY_FRAMEWORK)

    def test_by_type_matches_oracle_and_contract(self):
        self.assertEqual(self.playback["by_type"], self.oracle["by_type"])
        self.assertEqual(self.playback["by_type"], CONTRACT_BY_TYPE)

    def test_by_severity_matches_oracle_and_contract(self):
        self.assertEqual(self.playback["by_severity"], self.oracle["by_severity"])
        self.assertEqual(self.playback["by_severity"], CONTRACT_BY_SEVERITY)

    def test_all_rows_carry_a_source_rule_id_and_source_page(self):
        rules = s.query("kb_rules", version_id=self.version_id)
        self.assertEqual(len(rules), CONTRACT_TOTAL)
        for r in rules:
            self.assertTrue(r["source_rule_id"], f"missing source_rule_id: {r['rule_id']}")
            self.assertIsNotNone(r["source_page"], f"missing source_page: {r['rule_id']}")
            self.assertIn(r["source_page"], (1, 2, 3))  # every table row is on pages 1-3

    def test_rule_hashes_are_unique(self):
        rules = s.query("kb_rules", version_id=self.version_id)
        self.assertEqual(len({r["rule_hash"] for r in rules}), CONTRACT_TOTAL)

    def test_parse_report_lists_every_rule_with_status_and_reason(self):
        """5-T5 — retrievable via kb.parse_report(); routers/v3.py exposes
        GET /v3/knowledge/versions/{version_id}/parse-report."""
        report = kb.parse_report(TENANT, self.version_id)
        self.assertEqual(report["rules_total"], CONTRACT_TOTAL)
        self.assertEqual(len(report["rules"]), CONTRACT_TOTAL)
        for row in report["rules"]:
            self.assertTrue(row["status"])
            self.assertTrue((row["reason"] or "").strip())
            self.assertTrue(row["source_ref"])
        statuses = {row["status"] for row in report["rules"]}
        self.assertTrue(statuses <= {"bound", "reference-only", "unparsed"})


class ProseRegressionTests(unittest.TestCase):
    """5-T2 — a heading-structured prose document (no tables at all) still
    parses one rule per H2 section, unaffected by the table-aware changes."""

    @classmethod
    def setUpClass(cls):
        s.init_schema()
        seed_platform()
        seed_taxonomy()

    def test_prose_document_still_produces_one_rule_per_heading(self):
        body = ("# Title\nSome overview text.\n\n"
               "## First rule\nA facility must have a unique identifier.\n\n"
               "## Second rule\nA settlement date must not precede its trade date.\n")
        upload = kb.upload_document(TENANT, "prose.md", "text/markdown", body.encode("utf-8"),
                                    None, "tester")
        preview = kb.submit_for_review(TENANT, upload["version"]["version_id"], "tester", "domain_fact")
        # "Overview" (pre-first-H2 content) + 2 named sections = 3 sections/rules.
        self.assertEqual(len(preview["sections"]), 3)
        self.assertEqual(len(preview["rules"]), 3)
        headings = {sec["heading"] for sec in preview["sections"]}
        self.assertEqual(headings, {"Overview", "First rule", "Second rule"})
        for rule in preview["rules"]:
            self.assertIsNone(rule["source_rule_id"])
            self.assertIsNone(rule["rule_type"])
            self.assertEqual(rule["binding_status"], "unparsed")  # no table -> no recovered type

    def test_prose_document_playback_summary_is_all_zero_breakdowns(self):
        body = "## Only rule\nBody text with no table structure at all.\n"
        upload = kb.upload_document(TENANT, "prose2.md", "text/markdown", body.encode("utf-8"),
                                    None, "tester")
        version_id = upload["version"]["version_id"]
        kb.submit_for_review(TENANT, version_id, "tester", "domain_fact")
        playback = kb.playback_summary(TENANT, version_id)
        self.assertEqual(playback["rules_total"], 1)
        self.assertEqual(playback["by_framework"], {})
        self.assertEqual(playback["by_type"], {})
        self.assertEqual(playback["binding"], {"bound": 0, "reference_only": 0, "unparsed": 1})
        self.assertEqual(len(playback["unparsed"]), 1)


class RejectionTests(unittest.TestCase):
    """5-T7 — three distinct, clear rejection messages."""

    def _make_pdf_bytes(self, text: str | None) -> bytes:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.add_page()
        if text:
            pdf.set_font("Helvetica", "", 12)
            pdf.multi_cell(0, 8, text)
        return bytes(pdf.output())

    def test_scanned_image_only_pdf_is_rejected(self):
        content = self._make_pdf_bytes(None)  # blank page, no text layer
        with self.assertRaises(ConversionError) as ctx:
            convert_to_markdown("scan.pdf", "", content)
        self.assertIn("scanned", str(ctx.exception).lower())

    def test_corrupt_docx_is_rejected(self):
        garbage = b"not a real docx file, just garbage bytes" * 20
        with self.assertRaises(ConversionError) as ctx:
            convert_to_markdown("broken.docx", "", garbage)
        self.assertIn("corrupt", str(ctx.exception).lower())

    def test_oversized_file_is_rejected(self):
        from kb_convert import MAX_BYTES
        oversized = b"a" * (MAX_BYTES + 1)
        with self.assertRaises(ConversionError) as ctx:
            convert_to_markdown("huge.txt", "text/plain", oversized)
        self.assertIn("mb", str(ctx.exception).lower())

    def test_all_three_rejection_messages_are_distinct(self):
        msgs = set()
        try:
            convert_to_markdown("scan.pdf", "", self._make_pdf_bytes(None))
        except ConversionError as exc:
            msgs.add(str(exc))
        try:
            convert_to_markdown("broken.docx", "", b"garbage" * 20)
        except ConversionError as exc:
            msgs.add(str(exc))
        try:
            from kb_convert import MAX_BYTES
            convert_to_markdown("huge.txt", "text/plain", b"a" * (MAX_BYTES + 1))
        except ConversionError as exc:
            msgs.add(str(exc))
        self.assertEqual(len(msgs), 3)  # three genuinely different messages


if __name__ == "__main__":
    unittest.main()
