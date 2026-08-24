import { createContext, useCallback, useContext, useMemo, useState } from "react";

const WORKFLOW_CONTEXT_KEY = "archimedes.workflow-context";
const WorkflowContext = createContext(null);

function readStored() {
  try {
    const value = sessionStorage.getItem(WORKFLOW_CONTEXT_KEY);
    return value ? JSON.parse(value) : null;
  } catch {
    return null;
  }
}

export function WorkflowContextProvider({ children }) {
  const [asset, setAssetState] = useState(readStored);
  const setAsset = useCallback((next) => {
    setAssetState(next || null);
    try {
      if (next) sessionStorage.setItem(WORKFLOW_CONTEXT_KEY, JSON.stringify(next));
      else sessionStorage.removeItem(WORKFLOW_CONTEXT_KEY);
    } catch {
      // Session storage can be unavailable in a privacy-restricted browser;
      // the visible bar still makes the in-memory context explicit.
    }
  }, []);
  const clear = useCallback(() => setAsset(null), [setAsset]);
  const value = useMemo(() => ({ asset, setAsset, clear }), [asset, clear, setAsset]);
  return <WorkflowContext.Provider value={value}>{children}</WorkflowContext.Provider>;
}

export function useWorkflowContext() {
  const value = useContext(WorkflowContext);
  if (!value) throw new Error("useWorkflowContext must be used inside WorkflowContextProvider");
  return value;
}
