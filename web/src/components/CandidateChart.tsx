import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { Point } from "../types";

/** Intraday price + VWAP with the trade conditions drawn as horizontal lines.
 *  entry/stop/target come from the risk plan; `trailStop` (when in a position)
 *  ratchets up live with each tick. */
export default function CandidateChart({
  points, entry, stop, target, trailStop,
}: {
  points: Point[];
  entry?: number;
  stop?: number;
  target?: number;
  trailStop?: number;
}) {
  const data = points.map((p, i) => ({ i, last: p.last, vwap: p.vwap }));
  if (data.length < 2) {
    return (
      <div className="grid place-items-center h-full text-[12px]" style={{ color: "var(--muted)" }}>
        collecting price history…
      </div>
    );
  }
  // y-domain spans the price AND every plan level, so entry/stop/target/trail
  // lines are always visible (not clipped outside the price's auto-zoom).
  const vals = [
    ...data.flatMap((d) => [d.last, d.vwap]),
    ...[entry, stop, target, trailStop].filter((v): v is number => v != null),
  ];
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const pad = (hi - lo) * 0.06 || 0.1;
  const domain: [number, number] = [+(lo - pad).toFixed(2), +(hi + pad).toFixed(2)];
  const ref = (y: number | undefined, color: string, label: string) =>
    y == null ? null : (
      <ReferenceLine
        y={y}
        stroke={color}
        strokeDasharray="4 3"
        strokeWidth={1.2}
        label={{ value: `${label} ${y.toFixed(2)}`, position: "right", fill: color, fontSize: 10 }}
      />
    );

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 8, right: 64, bottom: 4, left: 4 }}>
        <CartesianGrid stroke="#232b3a" strokeDasharray="2 4" />
        <XAxis dataKey="i" hide />
        <YAxis
          domain={domain}
          stroke="#8b949e"
          tick={{ fontSize: 10, fontFamily: "JetBrains Mono" }}
          width={48}
          tickFormatter={(v) => v.toFixed(2)}
        />
        <Tooltip
          contentStyle={{ background: "#161b22", border: "1px solid #283040", fontFamily: "JetBrains Mono", fontSize: 11 }}
          labelFormatter={() => ""}
          formatter={(v: number, n: string) => [v.toFixed(3), n]}
        />
        {ref(entry, "#60a5fa", "entry")}
        {ref(target, "#34d399", "target")}
        {ref(stop, "#f87171", "stop")}
        {ref(trailStop, "#fbbf24", "trail")}
        <Line type="monotone" dataKey="last" name="price" stroke="#e6edf3" dot={false} strokeWidth={1.6} isAnimationActive={false} />
        <Line type="monotone" dataKey="vwap" name="vwap" stroke="#8b5cf6" dot={false} strokeWidth={1.1} strokeDasharray="3 3" isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
