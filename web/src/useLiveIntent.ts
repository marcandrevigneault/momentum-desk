import { useEffect, useState } from "react";
import { getLiveIntent, type LiveIntent } from "./api";

/** One shared poller for /api/live/intent — the header P&L, the Cockpit badge
 *  and the Engine page all subscribe to the same 8s fetch instead of running
 *  three concurrent intervals against the same endpoint. */
const POLL_MS = 8000;
let current: LiveIntent | null = null;
let timer: number | null = null;
const subs = new Set<(li: LiveIntent) => void>();

async function tick() {
  try {
    current = await getLiveIntent();
    subs.forEach((fn) => fn(current!));
  } catch {
    /* keep the last value; retry on the next tick */
  }
}

export function useLiveIntent(): LiveIntent | null {
  const [li, setLi] = useState<LiveIntent | null>(current);
  useEffect(() => {
    subs.add(setLi);
    if (timer == null) {
      void tick();
      timer = window.setInterval(tick, POLL_MS);
    } else if (current) {
      setLi(current);
    }
    return () => {
      subs.delete(setLi);
      if (subs.size === 0 && timer != null) {
        clearInterval(timer);
        timer = null;
      }
    };
  }, []);
  return li;
}
