import { useEffect, useRef, useState } from "react";
import { Sparkles, Check } from "lucide-react";
import { agentLabel } from "@/lib/agents";

// Feedback R4.2 — a Claude-style "thinking" view that blends with the page:
// no dark terminal, concise plain-language captions (not raw JSON tokens), an
// agent + live timer header, a contained auto-scroll with the older text faded,
// and a "Thought for Xs · N tokens" footer when done.

// Only plain-language status frames feed the visible stream; raw streamed tokens
// (often JSON) and bookkeeping frames (pass/usage) are intentionally suppressed.
const CAPTION_PHASES = new Set(["start", "thinking", "done", "error"]);

function captions(events) {
  const out = [];
  for (const e of events) {
    if (CAPTION_PHASES.has(e.phase) && e.thought) {
      out.push({ agent: e.agent, text: e.thought, error: e.phase === "error" });
    }
  }
  return out;
}

export default function AgentConsole({ events = [], running = false }) {
  const scrollRef = useRef(null);
  const agentRef = useRef(null);        // active agent (for per-agent timer reset)
  const agentStartRef = useRef(null);   // when the active agent started thinking
  const firstStartRef = useRef(null);   // when the whole run started
  const [agentSecs, setAgentSecs] = useState(0);  // live per-agent elapsed (display)
  const [totalSecs, setTotalSecs] = useState(0);  // frozen total once done (display)

  const lines = captions(events);
  const activeAgent = lines.length ? lines[lines.length - 1].agent
    : (events.length ? events[events.length - 1].agent : null);

  // Run timer: start on first running, freeze total when it stops.
  useEffect(() => {
    if (running) {
      if (!firstStartRef.current) firstStartRef.current = Date.now();
    } else if (firstStartRef.current && !totalSecs) {
      setTotalSecs(Math.max(1, Math.round((Date.now() - firstStartRef.current) / 1000)));
    }
  }, [running, totalSecs]);

  // Reset the per-agent timer when the active agent changes.
  useEffect(() => {
    if (running && activeAgent && agentRef.current !== activeAgent) {
      agentRef.current = activeAgent;
      agentStartRef.current = Date.now();
      setAgentSecs(0);
    }
  }, [activeAgent, running]);

  // Tick the live per-agent timer while running.
  useEffect(() => {
    if (!running) return undefined;
    const id = setInterval(() => {
      if (agentStartRef.current) {
        setAgentSecs(Math.max(0, Math.round((Date.now() - agentStartRef.current) / 1000)));
      }
    }, 300);
    return () => clearInterval(id);
  }, [running]);

  // Auto-scroll WITHIN the box (never the page).
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length, running]);

  const tokens = events.reduce((a, e) => a + (e.phase === "usage" ? (e.total_tokens || 0) : 0), 0);
  const estimated = events.some((e) => e.phase === "usage" && e.estimated);

  return (
    <div className="text-sm">
      {running ? (
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 animate-pulse text-dq-purple" />
          <span className="font-medium text-slate-600">
            {activeAgent ? agentLabel(activeAgent) : "Agent"} · thinking {agentSecs}s
          </span>
        </div>
      ) : (lines.length > 0 || tokens > 0) ? (
        <div className="flex items-center gap-2 text-slate-500">
          <Check className="h-3.5 w-3.5 text-emerald-500" />
          <span>
            {totalSecs ? `Thought for ${totalSecs}s` : "Done"}
            {tokens ? ` · ${estimated ? "~" : ""}${tokens.toLocaleString()} tokens` : ""}
          </span>
        </div>
      ) : null}

      <div
        ref={scrollRef}
        className="mt-2 max-h-40 overflow-y-auto pr-1 leading-relaxed"
        style={{
          maskImage: "linear-gradient(to bottom, transparent 0, black 2.25rem)",
          WebkitMaskImage: "linear-gradient(to bottom, transparent 0, black 2.25rem)",
        }}
      >
        {lines.length === 0 && running && <p className="italic text-slate-400">Preparing…</p>}
        {lines.map((ln, i) => {
          const newAgent = i === 0 || lines[i - 1].agent !== ln.agent;
          const last = i === lines.length - 1;
          return (
            <p key={i} className={last ? "text-slate-600" : "text-slate-400"}>
              {newAgent && ln.agent && (
                <span className="mr-1 font-medium text-slate-500">{agentLabel(ln.agent)}:</span>
              )}
              <span className={ln.error ? "text-red-500" : ""}>{ln.text}</span>
            </p>
          );
        })}
      </div>
    </div>
  );
}
