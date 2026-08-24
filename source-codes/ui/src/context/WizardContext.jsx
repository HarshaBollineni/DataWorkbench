import { createContext, useContext, useMemo, useState } from "react";

import { testCatalog } from "@/data/catalog";

const WizardContext = createContext(null);

const defaultSelected = () =>
  Object.fromEntries(testCatalog.map((t) => [t.id, t.defaultSelected]));

export function WizardProvider({ children }) {
  const [selectedUseCase, setSelectedUseCase] = useState(null); // legacy (pre-P3)
  const [contract, setContract] = useState(null); // legacy backend contract row
  // Plan 3 assessment scope: a logical DB + its selected tables.
  const [scope, setScope] = useState({ logicalDb: null, tables: [], activeTable: null });
  const [selected, setSelected] = useState(defaultSelected); // {testId: bool}
  const [aiRules, setAiRules] = useState([]); // accepted Layer-1 rules
  const [validationResults, setValidationResults] = useState(null); // {success, results}
  // A Suggested Test Hypothesis (from the AI Summary) handed to Define Test Plan
  // to pre-fill table + fields. Consumed + cleared by DefineTestPlan on arrival.
  const [pendingHypothesis, setPendingHypothesis] = useState(null);

  const selectedTests = useMemo(
    () => Object.entries(selected).filter(([, v]) => v).map(([k]) => k),
    [selected]
  );

  const toggleTest = (id) => setSelected((s) => ({ ...s, [id]: !s[id] }));
  const reset = () => {
    setSelected(defaultSelected());
    setAiRules([]);
    setValidationResults(null);
  };

  const updateScope = (next) =>
    setScope((current) => {
      const value = typeof next === "function" ? next(current) : next;
      return { ...current, ...value };
    });

  const value = {
    selectedUseCase, setSelectedUseCase,
    contract, setContract,
    scope, setScope: updateScope,
    selected, toggleTest, selectedTests,
    aiRules, setAiRules,
    validationResults, setValidationResults,
    pendingHypothesis, setPendingHypothesis,
    reset,
  };
  return <WizardContext.Provider value={value}>{children}</WizardContext.Provider>;
}

export function useWizard() {
  const ctx = useContext(WizardContext);
  if (!ctx) throw new Error("useWizard must be used within a WizardProvider");
  return ctx;
}
