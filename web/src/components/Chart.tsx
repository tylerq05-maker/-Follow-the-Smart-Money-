import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent, GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent,
  TooltipComponent, VisualMapComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, ScatterChart, HeatmapChart, GridComponent, TooltipComponent, LegendComponent,
  DataZoomComponent, MarkLineComponent, MarkAreaComponent, VisualMapComponent, CanvasRenderer]);

export const C = {
  text: "#a3abb9", dim: "#6b7486", line: "#222936", grid: "#1a2029", pos: "#3fb67f", neg: "#e05d5d",
  gold: "#c9a96e", blue: "#8fa8d6", violet: "#a58bd6", gray: "#5d6575",
};

const baseOption = {
  backgroundColor: "transparent",
  textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: C.text },
  animationDuration: 400,
  grid: { left: 56, right: 18, top: 24, bottom: 30 },
  tooltip: {
    trigger: "axis", backgroundColor: "#171c25", borderColor: "#2c3443", textStyle: { color: "#e6e9ef", fontSize: 12 },
    axisPointer: { lineStyle: { color: "#3a4352" } },
  },
};

export const axis = (extra: object = {}) => ({
  axisLine: { lineStyle: { color: C.line } }, axisTick: { show: false },
  axisLabel: { color: C.dim, fontSize: 11, fontFamily: "JetBrains Mono, monospace" },
  splitLine: { lineStyle: { color: C.grid } }, ...extra,
});

export default function Chart({ option, height = 280 }: { option: object; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const inst = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    inst.current = echarts.init(ref.current, undefined, { renderer: "canvas" });
    const ro = new ResizeObserver(() => inst.current?.resize());
    ro.observe(ref.current);
    return () => { ro.disconnect(); inst.current?.dispose(); };
  }, []);
  useEffect(() => { inst.current?.setOption({ ...baseOption, ...option }, true); }, [option]);
  return <div ref={ref} style={{ height, width: "100%" }} />;
}
