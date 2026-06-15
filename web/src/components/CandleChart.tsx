import { type IChartApi, type IPriceLine, type ISeriesApi, createChart } from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Candle } from "../types";

/** Candlestick chart (TradingView Lightweight Charts) fed by our OHLC bars,
 *  with the trade plan drawn as price lines (entry / stop / target / trail). */
export default function CandleChart({
  candles, entry, stop, target, trailStop,
}: {
  candles: Candle[];
  entry?: number;
  stop?: number;
  target?: number;
  trailStop?: number;
}) {
  const elRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);

  // create once
  useEffect(() => {
    if (!elRef.current) return;
    const chart = createChart(elRef.current, {
      layout: { background: { color: "#0d1117" }, textColor: "#8b949e", fontSize: 11 },
      grid: { vertLines: { color: "#1c2230" }, horzLines: { color: "#1c2230" } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#283040" },
      rightPriceScale: { borderColor: "#283040" },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#34d399", downColor: "#f87171", borderVisible: false,
      wickUpColor: "#34d399", wickDownColor: "#f87171",
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = null; };
  }, []);

  // update data + plan lines
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    if (!series || !chart) return;
    series.setData(candles as never);
    for (const l of linesRef.current) series.removePriceLine(l);
    linesRef.current = [];
    const line = (price: number | undefined, color: string, title: string) => {
      if (price == null) return;
      linesRef.current.push(series.createPriceLine({
        price, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title,
      }));
    };
    line(entry, "#60a5fa", "entry");
    line(target, "#34d399", "target");
    line(stop, "#f87171", "stop");
    line(trailStop, "#fbbf24", "trail");
    if (candles.length) chart.timeScale().fitContent();
  }, [candles, entry, stop, target, trailStop]);

  // The ref'd div must ALWAYS mount, even while candles is empty — the create
  // effect runs once on mount and bails permanently if the element isn't there.
  // So we keep the container present and overlay the empty-state on top of it.
  return (
    <div className="relative h-full w-full">
      <div ref={elRef} style={{ width: "100%", height: "100%" }} />
      {candles.length === 0 && (
        <div className="absolute inset-0 grid place-items-center text-[12px]" style={{ color: "var(--muted)" }}>
          no bars for this symbol / timeframe
        </div>
      )}
    </div>
  );
}
