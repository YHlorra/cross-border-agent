import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";

echarts.use([LineChart]);

/** ECharts 迷你趋势线(按需注册,容器自适应)。序列为空时不渲染。 */
export default function Sparkline({
  series,
  height = 40,
}: {
  series: number[];
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  if (!series.length) return null;
  return <SparklineInner series={series} height={height} domRef={ref} />;
}

function SparklineInner({
  series,
  height,
  domRef,
}: {
  series: number[];
  height: number;
  domRef: React.RefObject<HTMLDivElement | null>;
}) {
  useEffect(() => {
    if (!domRef.current) return;
    // jsdom 无 canvas getContext — 初始化失败静默跳过(仅测试环境)
    let chart: echarts.ECharts | undefined;
    try {
      chart = echarts.init(domRef.current, undefined, { height });
      chart.setOption({
        grid: { left: 2, right: 2, top: 4, bottom: 2 },
        xAxis: { type: "category", show: false },
        yAxis: { type: "value", show: false },
        series: [{ type: "line", data: series, smooth: true, symbol: "none" }],
      });
    } catch {
      return;
    }
    return () => chart?.dispose();
  }, [series, height, domRef]);
  return <div ref={domRef} style={{ width: "100%", height }} />;
}
