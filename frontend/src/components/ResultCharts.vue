<script setup lang="ts">
import * as echarts from "echarts";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import type { ChartSpec, ReportTable } from "../types";
import {
  formatTableTitle,
  isIntermediateTable,
  tableNote,
} from "../utils/resultMeta";

const props = defineProps<{
  charts: ChartSpec[];
  /** Used to synthesize a bar when API only sent duplicate table charts. */
  tables?: ReportTable[];
}>();

const root = ref<HTMLElement | null>(null);
const instances: echarts.ECharts[] = [];
let lastFingerprint = "";

const PREFERRED_X = [
  "product_name",
  "channel",
  "user_segment",
  "order_date",
  "period",
  "product_id",
];
const PREFERRED_Y = [
  "return_rate",
  "amount_sum",
  "order_amount",
  "amount",
  "repurchase_rate",
  "order_count",
  "count",
  "qty",
  "quantity",
];

/** Skip table charts — they duplicate「结果表格」. Keep bar / line / notes. */
const apiVisualCharts = computed(() =>
  (props.charts || []).filter(
    (c) => c.chart_type !== "table" || Boolean(c.alternative_note),
  ),
);

function isNumericColumn(
  rows: Array<Record<string, unknown>>,
  col: string,
): boolean {
  let seen = 0;
  for (const row of rows) {
    const v = row[col];
    if (v === null || v === undefined || v === "") continue;
    seen += 1;
    if (typeof v === "number") continue;
    if (typeof v === "string" && v.trim() !== "" && !Number.isNaN(Number(v))) {
      continue;
    }
    return false;
  }
  return seen > 0;
}

function synthesizeBarFromTables(): ChartSpec | null {
  const tables = props.tables || [];
  if (!tables.length) return null;
  let primary: ReportTable | null = null;
  for (let i = tables.length - 1; i >= 0; i -= 1) {
    const t = tables[i];
    if (!isIntermediateTable(t.sub_task_id, i, tables.length)) {
      primary = t;
      break;
    }
  }
  if (!primary) primary = tables[tables.length - 1];
  const rows = primary.rows || [];
  const cols =
    primary.columns?.length > 0
      ? [...primary.columns]
      : rows.length
        ? Object.keys(rows[0])
        : [];
  if (!rows.length || cols.length < 2) return null;

  const numericCols = cols.filter((c) => isNumericColumn(rows, c));
  if (!numericCols.length) return null;

  const y =
    PREFERRED_Y.find((c) => numericCols.includes(c)) || numericCols[0];
  const x =
    PREFERRED_X.find((c) => cols.includes(c) && c !== y) ||
    cols.find((c) => c !== y) ||
    cols[0];
  if (!x || x === y) return null;

  return {
    chart_type: "bar",
    title: primary.sub_task_id || "结果图",
    x_field: x,
    y_field: y,
    series: rows as Array<Record<string, unknown>>,
    alternative_note: null,
  };
}

const visualCharts = computed(() => {
  const fromApi = apiVisualCharts.value.filter((c) => !c.alternative_note);
  if (fromApi.length) return fromApi;
  const notes = apiVisualCharts.value.filter((c) => c.alternative_note);
  if (notes.length) return notes;
  const synth = synthesizeBarFromTables();
  return synth ? [synth] : [];
});

/** Stable key so sibling task refreshes do not redraw this chart. */
const dataFingerprint = computed(() =>
  JSON.stringify({
    charts: props.charts,
    tables: (props.tables || []).map((t) => ({
      id: t.sub_task_id,
      cols: t.columns,
      n: t.rows?.length ?? 0,
      // Sample first/last row so content changes still redraw.
      head: t.rows?.[0] ?? null,
      tail: t.rows?.length ? t.rows[t.rows.length - 1] : null,
    })),
  }),
);

function disposeAll() {
  while (instances.length) {
    instances.pop()?.dispose();
  }
}

function chartRows(chart: ChartSpec): Array<Record<string, unknown>> {
  const series = Array.isArray(chart.series) ? chart.series : [];
  if (!series.length) return [];
  const first = series[0] as Record<string, unknown>;
  if (first && Array.isArray(first.rows)) {
    return first.rows as Array<Record<string, unknown>>;
  }
  // series shaped as {x:[], y:[]} from backend bar builder
  if (first && Array.isArray(first.x) && Array.isArray(first.y)) {
    const xs = first.x as unknown[];
    const ys = first.y as unknown[];
    const xKey = chart.x_field || "x";
    const yKey = chart.y_field || "y";
    return xs.map((x, i) => ({ [xKey]: x, [yKey]: ys[i] }));
  }
  return series as Array<Record<string, unknown>>;
}

function chartDisplayTitle(chart: ChartSpec): string {
  const raw = (chart.title || "").trim();
  if (!raw) return chart.chart_type;
  const note = tableNote(raw);
  if (note) return formatTableTitle(raw, 0);
  return raw;
}

function render() {
  const fp = dataFingerprint.value;
  if (fp === lastFingerprint && instances.length > 0) return;
  lastFingerprint = fp;

  disposeAll();
  const host = root.value;
  if (!host) return;
  host.innerHTML = "";
  for (const chart of visualCharts.value) {
    const box = document.createElement("div");
    box.className = "chart-box";
    host.appendChild(box);

    if (chart.alternative_note) {
      box.innerHTML = `<p class="muted">${chartDisplayTitle(chart)}：${chart.alternative_note}</p>`;
      continue;
    }

    const el = document.createElement("div");
    el.style.height = "280px";
    box.appendChild(el);
    const inst = echarts.init(el);
    instances.push(inst);
    const rows = chartRows(chart);
    const xField = chart.x_field || Object.keys(rows[0] || {})[0] || "x";
    const yField = chart.y_field || Object.keys(rows[0] || {})[1] || "y";
    const xs = rows.map((r) => String(r[xField] ?? ""));
    const ys = rows.map((r) => Number(r[yField] ?? 0));
    inst.setOption({
      title: {
        text: chartDisplayTitle(chart),
        left: "center",
        textStyle: { fontSize: 13 },
      },
      tooltip: { trigger: "axis" },
      grid: { left: 48, right: 24, top: 40, bottom: 32 },
      xAxis: {
        type: "category",
        data: xs,
        axisLabel: { interval: 0, rotate: xs.length > 6 ? 30 : 0 },
      },
      yAxis: { type: "value" },
      series: [
        {
          type: chart.chart_type === "line" ? "line" : "bar",
          data: ys,
          name: yField,
        },
      ],
    });
  }
}

onMounted(() => {
  void nextTick(render);
});

watch(dataFingerprint, () => {
  void nextTick(render);
});

onBeforeUnmount(disposeAll);
</script>

<template>
  <div v-if="visualCharts.length" class="result-charts">
    <h3>基础图表</h3>
    <div ref="root" />
  </div>
</template>
