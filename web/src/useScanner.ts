import { useEffect, useRef, useState } from "react";
import type { ScanMessage } from "./types";

export type ConnState = "connecting" | "live" | "down";

/** Subscribes to the backend's WebSocket scan stream, with auto-reconnect.
 *  Returns the latest message and the connection state. */
export function useScanner() {
  const [data, setData] = useState<ScanMessage | null>(null);
  const [state, setState] = useState<ConnState>("connecting");
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let closed = false;

    const connect = () => {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/signals`);
      wsRef.current = ws;
      setState("connecting");

      ws.onopen = () => setState("live");
      ws.onmessage = (e) => {
        try {
          setData(JSON.parse(e.data) as ScanMessage);
          setState("live");
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        if (closed) return;
        setState("down");
        retryRef.current = setTimeout(connect, 1500); // reconnect
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      closed = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close();
    };
  }, []);

  return { data, state };
}
