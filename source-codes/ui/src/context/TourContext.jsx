import { createContext, useContext, useState } from "react";

const TourContext = createContext(null);

const KEY_ENABLED = "tourEnabled";
const KEY_SEEN = "hasSeenTour";

export function TourProvider({ children }) {
  const [enabled, setEnabledState] = useState(
    () => localStorage.getItem(KEY_ENABLED) !== "false" // default ON
  );
  const [hasSeen, setHasSeenState] = useState(
    () => localStorage.getItem(KEY_SEEN) === "true"
  );

  const setEnabled = (v) => {
    setEnabledState(v);
    localStorage.setItem(KEY_ENABLED, String(v));
  };
  const setHasSeen = (v) => {
    setHasSeenState(v);
    localStorage.setItem(KEY_SEEN, String(v));
  };

  return (
    <TourContext.Provider value={{ enabled, setEnabled, hasSeen, setHasSeen }}>
      {children}
    </TourContext.Provider>
  );
}

export function useTour() {
  const ctx = useContext(TourContext);
  if (!ctx) throw new Error("useTour must be used within a TourProvider");
  return ctx;
}
