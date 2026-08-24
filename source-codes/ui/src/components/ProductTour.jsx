import { useEffect, useRef } from "react";
import { driver } from "driver.js";
import "driver.js/dist/driver.css";

import { tourSteps } from "@/config/tourSteps";
import { useTour } from "@/context/TourContext";

// Plan 3 / D9.7 — spotlight tour (driver.js; React-19 safe). Runs on first load
// when enabled & not yet seen; deactivates immediately when enabled flips off.
export default function ProductTour() {
  const { enabled, hasSeen, setHasSeen } = useTour();
  const driverRef = useRef(null);

  useEffect(() => {
    // Deactivate instantly if disabled.
    if (!enabled) {
      driverRef.current?.destroy();
      driverRef.current = null;
      return;
    }
    if (hasSeen) return;

    // Defer so target elements (sidebar + landing page) are mounted.
    const timer = setTimeout(() => {
      const steps = tourSteps
        .filter((s) => document.querySelector(s.target))
        .map((s) => ({ element: s.target, popover: { description: s.content } }));
      if (steps.length === 0) return;

      const d = driver({
        showProgress: true,
        allowClose: true,
        nextBtnText: "Next",
        prevBtnText: "Back",
        doneBtnText: "Done",
        steps,
        onDestroyed: () => { setHasSeen(true); driverRef.current = null; },
      });
      driverRef.current = d;
      d.drive();
    }, 600);

    return () => clearTimeout(timer);
  }, [enabled, hasSeen, setHasSeen]);

  return null;
}
