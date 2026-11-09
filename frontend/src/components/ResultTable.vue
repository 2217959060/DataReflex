<script setup lang="ts">
import type { ReportTable } from "../types";
import {
  formatTableTitle,
  isIntermediateTable,
} from "../utils/resultMeta";

defineProps<{
  tables: ReportTable[];
}>();

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function rowCount(table: ReportTable): number {
  return table.row_count ?? table.rows.length;
}
</script>

<template>
  <div v-if="tables.length" class="result-tables">
    <h3>结果表格</h3>
    <template v-for="(table, idx) in tables" :key="idx">
      <details
        v-if="isIntermediateTable(table.sub_task_id, idx, tables.length)"
        class="table-fold"
      >
        <summary class="table-title">
          {{ formatTableTitle(table.sub_task_id, idx + 1) }}
          <span class="muted">
            （中间步骤 · {{ rowCount(table) }} 行 · 点击展开）
          </span>
        </summary>
        <div class="table-wrap">
          <table v-if="table.columns?.length">
            <thead>
              <tr>
                <th v-for="col in table.columns" :key="col">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, rIdx) in table.rows" :key="rIdx">
                <td v-for="col in table.columns" :key="col">
                  {{ formatCell(row[col]) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </details>
      <div v-else class="table-wrap">
        <div class="table-title">
          {{ formatTableTitle(table.sub_task_id, idx + 1) }}
          <span class="muted">({{ rowCount(table) }} 行)</span>
        </div>
        <table v-if="table.columns?.length">
          <thead>
            <tr>
              <th v-for="col in table.columns" :key="col">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, rIdx) in table.rows" :key="rIdx">
              <td v-for="col in table.columns" :key="col">
                {{ formatCell(row[col]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
