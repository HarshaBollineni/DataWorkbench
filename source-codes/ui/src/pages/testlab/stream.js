import { useState } from "react";

export function useAgentStream() {
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);

  const run = (url, onDone, onEvent) => new Promise((resolve) => {
    setEvents([]);
    setRunning(true);
    const es = new EventSource(url);
    const finish = (event) => {
      setRunning(false);
      es.close();
      onDone?.(event);
      resolve(event);
    };
    es.onmessage = (msg) => {
      const event = JSON.parse(msg.data);
      if (event.phase !== "heartbeat") setEvents((prev) => [...prev, event]);
      onEvent?.(event);
      if (event.phase === "done" || event.phase === "error") {
        finish(event);
      }
    };
    es.onerror = () => {
      finish({ phase: "error", thought: "Connection to the server was lost." });
    };
  });

  return { events, running, run };
}
