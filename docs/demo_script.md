# 演示脚本（约 15 分钟）

面向答辩 / 求职 demo。默认 **Mock 沙箱** 即可完成；不要求 Docker 与 API Key。

## 启动

```powershell
# 终端 1
.\.venv\Scripts\Activate.ps1
# 演示建议不加 --reload：改 .env/代码会触发重启并清空内存中的任务，前端会一直 404。
uvicorn data_reflex.main:app --host 127.0.0.1 --port 8001

# 终端 2
cd frontend
npm run dev
```

浏览器打开前端（默认 `http://127.0.0.1:5173`）。后端健康检查：`http://127.0.0.1:8001/health` 应含 `status`、`sandbox_backend`，以及 `tracing_enabled`（配了 Langfuse Key 时为 `true`）。

样例数据：`eval/datasets/fixtures/` 下的 `orders.csv` / `returns.csv` / `users.csv`。

## 流程清单

| # | 操作 | 预期现象 |
| :---: | :--- | :--- |
| 1 | 上传三表 fixtures | 显示 `dataset_id`，表导入成功 |
| 2 | 六类问题各问一句（见下） | 进入规划；若开确认则出现计划待批 |
| 3 | 批准计划 | 任务跑完；有轨迹节点与结果表/图 |
| 4 | 问一句超范围（如「预测下周销量」） | 拒绝或澄清，不瞎执行 |
| 5 | （可选）关确认后再跑一题 | 无中断，危险操作仍应被拦 |

## 六类推荐问法

| 标签 | 示例问句 |
| :--- | :--- |
| `return_rate_topn` | 退货率最高的前 5 个商品是哪些？ |
| `channel_compare` | 各渠道的销售额对比怎么样？ |
| `time_trend` | 最近订单金额按天的趋势？ |
| `segment_compare` | 不同用户分层的下单情况对比 |
| `promo_compare` | 促销单和非促销单的金额对比 |
| `repurchase_rate` | 用户复购率是多少？ |

## 说明口径（被问到时）

- **Mock 模式**：链路与安全真实；结果数字是沙箱 Mock / 模板路径的演示值，**不是** fixtures 金标准。
- **真数字**：需 Docker 镜像 + `SANDBOX_BACKEND=docker`（见 `docs/roadmap.md` v1.5）；页头徽章会标明当前沙箱模式。
- **GLM**：`ENABLE_LLM_OPS=true` 且有 Key 时可生成/润色；失败回退模板。连通冒烟：`python scripts/smoke_llm.py`。
- **可观测（可选）**：`.env` 配 Langfuse Cloud Key 后重启 API，跑一题即可在 Cloud UI 看到 `task:…` Trace；冒烟：`python scripts/smoke_langfuse.py`。
