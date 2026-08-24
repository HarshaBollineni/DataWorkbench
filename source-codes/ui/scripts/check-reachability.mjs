#!/usr/bin/env node
/**
 * ui/scripts/check-reachability.mjs — RET-04 frontend reachability gate (0.5.0).
 *
 * A genuinely new mechanism (not a port of backend/tests/test_reachability.py,
 * which is purely textual/regex over main.py + routers/*.py — the frontend has
 * no "mounted router" list to regex out). This is an import-graph reachability
 * walk over the real module graph:
 *
 *   Roots  = ui/index.html's module entry (-> src/main.jsx), every ui/e2e/**\/*.js
 *            spec file, and ui/vite.config.* / ui/tailwind.config.* if present.
 *   Walk   = static `import ... from`, `export ... from`, `export * from`, and
 *            dynamic `import()` — resolved through the `@/` -> ui/src/ alias
 *            and vite's extension/index resolution (bare, .js, .jsx, .ts, .tsx,
 *            .mjs, .cjs, or a directory's index.*).
 *
 * Two blocking failure classes:
 *   1. UNREACHABLE FILE  — a file under ui/src/** that no reachable module
 *      imports (statically or dynamically), with no keep-reason.
 *   2. UNCALLED EXPORT   — a named export of a reachable module that no
 *      reachable module imports, with no keep-reason. Applied, deliberately
 *      narrow for this release (widening further is a later decision, not a
 *      silent scope grab), to ui/src/api/client.js and everything under
 *      ui/src/lib/**.
 *
 * The keep-list (./keep-reasons.json) is a single JSON map:
 *   "src/relative/path.jsx"          -> reason string   (exempts an unreachable file)
 *   "src/relative/path.js#exportName" -> reason string   (exempts an uncalled export)
 * A reason must be a non-empty string. A keep-reason naming a path or export
 * that no longer exists fails the gate (the keep-list must not rot). Every
 * keep-reason must also be recorded in docs/0.5.0/01-ret-census.md, mirroring
 * the backend's test_keep_reasons_are_recorded_in_the_helper_doc.
 *
 * A third, non-blocking-but-loud class: an import/export specifier this walker
 * could not resolve (excluding bare/external specifiers, which are simply not
 * followed). A resolver that silently drops a specifier it can't resolve would
 * report false orphans, which is worse than no walker at all — so these fail
 * the gate too, not just warn.
 *
 * Usage:
 *   node scripts/check-reachability.mjs             # human-readable report
 *   node scripts/check-reachability.mjs --json       # machine-readable dump
 *   node scripts/check-reachability.mjs --explain <path-relative-to-ui>
 *       Prints whether <path> is reachable and, if so, one import chain from
 *       a root that reaches it (or "not reachable" otherwise). Diagnostic only.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "@babel/parser";
import traverseModule from "@babel/traverse";

const traverse = traverseModule.default || traverseModule;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const UI_ROOT = path.resolve(__dirname, "..");
const SRC_ROOT = path.join(UI_ROOT, "src");
const E2E_ROOT = path.join(UI_ROOT, "e2e");
const REPO_ROOT = path.resolve(UI_ROOT, "..");
const CENSUS_DOC = path.join(REPO_ROOT, "docs", "0.5.0", "01-ret-census.md");
const KEEP_REASONS_PATH = path.join(__dirname, "keep-reasons.json");

const CODE_EXTS = [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"];

// Deliberately narrow "uncalled export" scope for 0.5.0 (RET-04 architecture
// note in archimedes-0.5.0-plan.md §7.2): client.js always; ui/src/lib/** always.
const EXPORT_CHECK_CLIENT = path.join(SRC_ROOT, "api", "client.js");
const EXPORT_CHECK_LIB_DIR = path.join(SRC_ROOT, "lib");

function toRel(p) {
  return path.relative(UI_ROOT, p).split(path.sep).join("/");
}
function isCodeFile(p) {
  return CODE_EXTS.includes(path.extname(p));
}
function inExportCheckScope(p) {
  return p === EXPORT_CHECK_CLIENT || (p + path.sep).startsWith(EXPORT_CHECK_LIB_DIR + path.sep);
}

// ---------------------------------------------------------------------------
// Filesystem universe: every file under ui/src/**
// ---------------------------------------------------------------------------
function walkDir(dir, out) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walkDir(full, out);
    else out.push(full);
  }
}
const ALL_SRC_FILES = [];
walkDir(SRC_ROOT, ALL_SRC_FILES);

function listCodeFiles(dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  const stack = [dir];
  while (stack.length) {
    const d = stack.pop();
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (isCodeFile(full)) out.push(full);
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// Module resolution — vite-equivalent: "@/" -> ui/src/, relative to importer,
// then bare / extension-probed / directory-index resolution.
// ---------------------------------------------------------------------------
function resolveSpecifier(spec, fromFile) {
  let base;
  if (spec.startsWith("@/")) {
    base = path.join(SRC_ROOT, spec.slice(2));
  } else if (spec.startsWith("./") || spec.startsWith("../")) {
    base = path.resolve(path.dirname(fromFile), spec);
  } else {
    return { external: true };
  }
  if (fs.existsSync(base) && fs.statSync(base).isFile()) return { abs: base };
  for (const ext of CODE_EXTS) {
    if (fs.existsSync(base + ext)) return { abs: base + ext };
  }
  if (fs.existsSync(base) && fs.statSync(base).isDirectory()) {
    for (const ext of CODE_EXTS) {
      const idx = path.join(base, "index" + ext);
      if (fs.existsSync(idx)) return { abs: idx };
    }
  }
  return { unresolved: true };
}

// ---------------------------------------------------------------------------
// Per-file static analysis (top-level import/export statements are, by spec,
// always at Program top level; dynamic import() can appear anywhere, so that
// alone needs a real traversal).
// ---------------------------------------------------------------------------
const moduleCache = new Map();
const parseErrors = [];

function collectPatternNames(node, out) {
  if (!node) return;
  if (node.type === "Identifier") out.push(node.name);
  else if (node.type === "ObjectPattern") {
    for (const p of node.properties) {
      if (p.type === "RestElement") collectPatternNames(p.argument, out);
      else collectPatternNames(p.value, out);
    }
  } else if (node.type === "ArrayPattern") {
    for (const el of node.elements) if (el) collectPatternNames(el, out);
  } else if (node.type === "AssignmentPattern") {
    collectPatternNames(node.left, out);
  }
}
function collectDeclarationNames(decl) {
  const names = [];
  if (decl.type === "VariableDeclaration") {
    for (const d of decl.declarations) collectPatternNames(d.id, names);
  } else if (decl.type === "FunctionDeclaration" || decl.type === "ClassDeclaration") {
    if (decl.id) names.push(decl.id.name);
  }
  return names;
}

function analyzeFile(abs) {
  if (moduleCache.has(abs)) return moduleCache.get(abs);
  const info = {
    exists: fs.existsSync(abs),
    exports: new Map(), // exportedName -> { kind }
    imports: [], // { raw, kind, importedName?, localName? }
    reExportFrom: [], // { exportedName, raw, sourceName }
    reExportAll: [], // { raw, asName }
    dynamicImports: [], // raw specifiers
    parseError: null,
  };
  if (!info.exists || !isCodeFile(abs)) {
    moduleCache.set(abs, info);
    return info;
  }
  const code = fs.readFileSync(abs, "utf8");
  let ast;
  try {
    ast = parse(code, { sourceType: "module", plugins: ["jsx"] });
  } catch (err) {
    info.parseError = err.message;
    parseErrors.push({ file: abs, error: err.message });
    moduleCache.set(abs, info);
    return info;
  }

  for (const node of ast.program.body) {
    if (node.type === "ImportDeclaration") {
      const raw = node.source.value;
      if (node.specifiers.length === 0) {
        info.imports.push({ raw, kind: "sideEffect" });
      }
      for (const spec of node.specifiers) {
        if (spec.type === "ImportDefaultSpecifier") {
          info.imports.push({ raw, kind: "default", localName: spec.local.name });
        } else if (spec.type === "ImportNamespaceSpecifier") {
          info.imports.push({ raw, kind: "namespace", localName: spec.local.name });
        } else if (spec.type === "ImportSpecifier") {
          const importedName =
            spec.imported.type === "Identifier" ? spec.imported.name : spec.imported.value;
          info.imports.push({ raw, kind: "named", importedName, localName: spec.local.name });
        }
      }
    } else if (node.type === "ExportNamedDeclaration") {
      if (node.source) {
        for (const spec of node.specifiers) {
          const exportedName =
            spec.exported.type === "Identifier" ? spec.exported.name : spec.exported.value;
          const sourceName = spec.local.type === "Identifier" ? spec.local.name : spec.local.value;
          info.exports.set(exportedName, { kind: "reexport" });
          info.reExportFrom.push({ exportedName, raw: node.source.value, sourceName });
        }
      } else {
        if (node.declaration) {
          for (const n of collectDeclarationNames(node.declaration)) info.exports.set(n, { kind: "value" });
        }
        for (const spec of node.specifiers || []) {
          const exportedName =
            spec.exported.type === "Identifier" ? spec.exported.name : spec.exported.value;
          info.exports.set(exportedName, { kind: "value" });
        }
      }
    } else if (node.type === "ExportDefaultDeclaration") {
      info.exports.set("default", { kind: "value" });
    } else if (node.type === "ExportAllDeclaration") {
      const asName = node.exported ? node.exported.name || node.exported.value : null;
      info.reExportAll.push({ raw: node.source.value, asName });
      if (asName) info.exports.set(asName, { kind: "reexport-namespace" });
    }
  }

  try {
    traverse(ast, {
      Import(p) {
        const call = p.parentPath.node;
        const arg = call.arguments && call.arguments[0];
        if (arg && arg.type === "StringLiteral") info.dynamicImports.push(arg.value);
      },
    });
  } catch {
    /* a traversal failure on dynamic-import detection doesn't invalidate the
       static edges already collected above */
  }

  moduleCache.set(abs, info);
  return info;
}

// ---------------------------------------------------------------------------
// Reachability walk
// ---------------------------------------------------------------------------
const reachable = new Set();
const parentOf = new Map(); // abs -> { from, raw }
const unresolvedEdges = []; // { from, raw }
const usedExports = new Map(); // abs -> Set(exportedName)
const namespaceUsed = new Set(); // abs paths whose exports are all conservatively "used"

function markExportUsed(abs, name) {
  if (!usedExports.has(abs)) usedExports.set(abs, new Set());
  usedExports.get(abs).add(name);
}

function visit(abs, from, raw) {
  if (reachable.has(abs)) return;
  reachable.add(abs);
  if (from && !parentOf.has(abs)) parentOf.set(abs, { from, raw });
  const info = analyzeFile(abs);
  if (info.parseError) return;

  const edges = [
    ...info.imports.map((i) => ({ raw: i.raw, imp: i })),
    ...info.dynamicImports.map((raw) => ({ raw, imp: { kind: "dynamic" } })),
    ...info.reExportFrom.map((r) => ({
      raw: r.raw,
      imp: { kind: "named", importedName: r.sourceName },
    })),
    ...info.reExportAll.map(() => null).filter(Boolean),
  ];
  for (const r of info.reExportAll) edges.push({ raw: r.raw, imp: { kind: "namespace" } });

  for (const { raw: edgeRaw, imp } of edges) {
    const res = resolveSpecifier(edgeRaw, abs);
    if (res.external) continue;
    if (res.unresolved) {
      unresolvedEdges.push({ from: abs, raw: edgeRaw });
      continue;
    }
    visit(res.abs, abs, edgeRaw);
    if (imp.kind === "named") markExportUsed(res.abs, imp.importedName);
    else if (imp.kind === "default") markExportUsed(res.abs, "default");
    else if (imp.kind === "namespace" || imp.kind === "dynamic") namespaceUsed.add(res.abs);
    // "sideEffect" imports (e.g. `import "./App.css"`) imply no export usage.
  }
}

function findConfigRoots() {
  const roots = [];
  for (const f of fs.readdirSync(UI_ROOT)) {
    if (/^vite\.config\.(js|mjs|cjs|ts)$/.test(f) || /^tailwind\.config\.(js|mjs|cjs|ts)$/.test(f)) {
      roots.push(path.join(UI_ROOT, f));
    }
  }
  return roots;
}

function findEntryRoot() {
  const html = fs.readFileSync(path.join(UI_ROOT, "index.html"), "utf8");
  const m = html.match(/<script[^>]+type=["']module["'][^>]+src=["'](\/[^"']+)["']/);
  if (!m) throw new Error("check-reachability: could not find the module entry <script> in index.html");
  return path.join(UI_ROOT, m[1].replace(/^\//, ""));
}

const roots = [findEntryRoot(), ...listCodeFiles(E2E_ROOT), ...findConfigRoots()];
for (const r of roots) visit(r, null, null);

// ---------------------------------------------------------------------------
// Keep-reasons
// ---------------------------------------------------------------------------
const keepReasons = JSON.parse(fs.readFileSync(KEEP_REASONS_PATH, "utf8"));
const keepErrors = [];
for (const [key, reason] of Object.entries(keepReasons)) {
  if (typeof reason !== "string" || reason.trim() === "") {
    keepErrors.push(`empty keep-reason for "${key}"`);
    continue;
  }
  const hashIdx = key.indexOf("#");
  if (hashIdx === -1) {
    const abs = path.join(UI_ROOT, key);
    if (!fs.existsSync(abs)) keepErrors.push(`keep-reason names a path that no longer exists: ${key}`);
  } else {
    const filePart = key.slice(0, hashIdx);
    const exportPart = key.slice(hashIdx + 1);
    const abs = path.join(UI_ROOT, filePart);
    if (!fs.existsSync(abs)) {
      keepErrors.push(`keep-reason names a path that no longer exists: ${key}`);
    } else {
      const info = analyzeFile(abs);
      if (!info.exports.has(exportPart)) {
        keepErrors.push(`keep-reason names an export that no longer exists: ${key}`);
      }
    }
  }
}

let censusText = "";
if (fs.existsSync(CENSUS_DOC)) censusText = fs.readFileSync(CENSUS_DOC, "utf8");
const censusMissing = [];
for (const key of Object.keys(keepReasons)) {
  const filePart = key.includes("#") ? key.split("#")[0] : key;
  const base = path.basename(filePart);
  if (!censusText.includes(key) && !censusText.includes(base)) censusMissing.push(key);
}

// ---------------------------------------------------------------------------
// Failure class 1: unreachable file under ui/src/**
// ---------------------------------------------------------------------------
const unreachableFiles = [];
for (const abs of ALL_SRC_FILES) {
  if (reachable.has(abs)) continue;
  const rel = toRel(abs);
  if (keepReasons[rel]) continue;
  unreachableFiles.push(rel);
}

// ---------------------------------------------------------------------------
// Failure class 2: uncalled export of client.js / lib/** (only for reachable
// files — an unreachable file's exports are already covered by class 1)
// ---------------------------------------------------------------------------
const uncalledExports = [];
for (const abs of ALL_SRC_FILES) {
  if (!isCodeFile(abs) || !inExportCheckScope(abs) || !reachable.has(abs)) continue;
  const info = analyzeFile(abs);
  if (info.parseError) continue;
  if (namespaceUsed.has(abs)) continue; // imported via `import *` or dynamic import somewhere — conservative
  const used = usedExports.get(abs) || new Set();
  for (const exportedName of info.exports.keys()) {
    if (used.has(exportedName)) continue;
    const key = `${toRel(abs)}#${exportedName}`;
    if (keepReasons[key]) continue;
    uncalledExports.push(key);
  }
}

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
const jsonMode = process.argv.includes("--json");
const explainIdx = process.argv.indexOf("--explain");

if (explainIdx !== -1) {
  const rel = process.argv[explainIdx + 1];
  const abs = path.join(UI_ROOT, rel);
  if (!reachable.has(abs)) {
    console.log(`${rel}: NOT reachable from any root.`);
  } else {
    const chain = [rel];
    let cur = abs;
    while (parentOf.has(cur)) {
      const { from, raw } = parentOf.get(cur);
      chain.push(`  <- ${toRel(from)}  (import "${raw}")`);
      cur = from;
    }
    console.log(`${rel}: reachable.\n${chain.join("\n")}`);
  }
  process.exit(0);
}

const failed =
  unreachableFiles.length > 0 ||
  uncalledExports.length > 0 ||
  keepErrors.length > 0 ||
  censusMissing.length > 0 ||
  unresolvedEdges.length > 0 ||
  parseErrors.length > 0;

if (jsonMode) {
  console.log(
    JSON.stringify(
      {
        totalSrcFiles: ALL_SRC_FILES.length,
        reachableSrcFiles: ALL_SRC_FILES.filter((f) => reachable.has(f)).length,
        unreachableFiles,
        uncalledExports,
        keepErrors,
        censusMissing,
        unresolvedEdges: unresolvedEdges.map((e) => ({ from: toRel(e.from), raw: e.raw })),
        parseErrors: parseErrors.map((e) => ({ file: toRel(e.file), error: e.error })),
        keepReasonCount: Object.keys(keepReasons).length,
      },
      null,
      2
    )
  );
  process.exit(failed ? 1 : 0);
}

console.log("== RET-04 frontend reachability gate ==");
console.log(`roots: ${roots.length}  |  ui/src files: ${ALL_SRC_FILES.length}  |  reachable: ${ALL_SRC_FILES.filter((f) => reachable.has(f)).length}`);
console.log(`keep-reasons on file: ${Object.keys(keepReasons).length}`);
console.log("");

function section(title, items, fmt = (x) => x) {
  console.log(`-- ${title}: ${items.length} --`);
  for (const it of items) console.log(`  ${fmt(it)}`);
  if (items.length === 0) console.log("  (none)");
  console.log("");
}

section("Unreachable files (no keep-reason)", unreachableFiles);
section("Uncalled exports (no keep-reason)", uncalledExports);
section("Unresolved import/export specifiers", unresolvedEdges, (e) => `${toRel(e.from)} -> "${e.raw}"`);
section("Parse errors", parseErrors, (e) => `${toRel(e.file)}: ${e.error}`);
section("Rotten / empty keep-reasons", keepErrors);
section("Keep-reasons missing from docs/0.5.0/01-ret-census.md", censusMissing);

if (failed) {
  console.log("FAIL: reachability gate found dead code with no recorded keep-reason.");
  process.exit(1);
} else {
  console.log("PASS: every file and export is reachable, or has a recorded keep-reason.");
  process.exit(0);
}
