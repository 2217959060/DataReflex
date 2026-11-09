/** Display labels and intermediate-step hints for known v1 sub-task ids. */

const TABLE_NOTES: Record<string, string> = {
  "st-load-join": "订单与退货关联明细",
  "st-rate-topn": "商品退货率 TopN",
  "st-agg-channel": "按渠道汇总",
  "st-compare-channel": "渠道对比结果",
  "st-agg-time": "按时间汇总",
  "st-series-trend": "时间趋势序列",
  "st-join-users": "订单与用户关联",
  "st-agg-segment": "用户分层汇总",
  "st-agg-promo": "按促销标记汇总",
  "st-compare-promo": "促销对比结果",
  "st-user-orders": "用户订单明细",
  "st-repurchase": "复购率结果",
  "st-probe": "单步探测结果",
};

/** First-step / join / load tables — folded by default in the UI. */
const INTERMEDIATE_IDS = new Set([
  "st-load-join",
  "st-agg-channel",
  "st-agg-time",
  "st-join-users",
  "st-agg-promo",
  "st-user-orders",
  "st-probe",
]);

export function tableNote(subTaskId: string | undefined | null): string {
  if (!subTaskId) return "";
  return TABLE_NOTES[subTaskId] || "";
}

export function formatTableTitle(
  subTaskId: string | undefined | null,
  fallbackIndex: number,
): string {
  const id = subTaskId?.trim() || `table-${fallbackIndex}`;
  const note = tableNote(subTaskId);
  return note ? `${id}（${note}）` : id;
}

export function isIntermediateTable(
  subTaskId: string | undefined | null,
  index: number,
  total: number,
): boolean {
  if (subTaskId && INTERMEDIATE_IDS.has(subTaskId)) return true;
  // Heuristic: with multiple tables, treat all but the last as intermediate.
  if (total > 1 && index < total - 1) return true;
  return false;
}

export function isKnownIntermediateId(subTaskId: string | undefined | null): boolean {
  return Boolean(subTaskId && INTERMEDIATE_IDS.has(subTaskId));
}
