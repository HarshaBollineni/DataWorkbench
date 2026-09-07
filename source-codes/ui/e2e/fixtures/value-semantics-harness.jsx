// Browser-test entry point only; never imported by the application bundle.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import ScopeGate from "../../src/pages/testlab/ScopeGate";
import ValueSemanticsResults from "../../src/features/test-lab/diagnostics/t2-d08-value-semantics/ValueSemanticsResults";
import "../../src/index.css";

export default function Harness() {
  const [showResults, setShowResults] = useState(false);
  return <main className="p-6">
    <button type="button" onClick={() => setShowResults(true)}>Show fixture results</button>
    {showResults
      ? <ValueSemanticsResults runId="vs-browser" results={window.__valueSemanticsResults} />
      : <ScopeGate runId="vs-browser" onRunStarted={() => {}} />}
  </main>;
}

createRoot(document.getElementById("root")).render(<Harness />);
