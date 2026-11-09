# schema.md — v1 固定三表契约

> 本文件为字段名、类型、关联键与六类问题的唯一实现基准。  
> 改字段先改本文档，再改代码与评测金标准。样例数据见 `eval/datasets/fixtures/`。

<a id="D-SCHEMA-01"></a>
> **【实现决策 D-SCHEMA-01｜2026-10-25】**  
> **决定：** 在原 `spec`/`design` 仅写「订单 / 退货 / 用户」而未锁列名的情况下，独立产出本契约 + `eval/datasets/fixtures/*.csv`，作为唯一字段基准。  
> **原因：** 若不写死列名/类型/Join 键，意图解析白名单、金标准、演示数据会各写一套，T10/T20/T40 必然返工；固定三表六类的产品边界也要求 Schema 可核对。  
> **依据：** [risk.md P0-6](risk.md#risk-p0)（支持范围已确认）+ [handoff 实现契约](handoff.md#实现契约文档)；样例规模与枚举取值以可演示、可分层评测为准，而非生产真实分布。

## 1. 数据集约定

| 项 | 约定 |
| :--- | :--- |
| 逻辑表名 | `orders`、`returns`、`users` |
| 演示/评测固定文件名 | `orders.csv`、`returns.csv`、`users.csv` |
| 编码 | UTF-8（无 BOM）；分隔符逗号 |
| 主键 | 各表单列主键，见下表 |
| 关联 | 仅下列 Join 键；导入模块可据此生成 `JoinHint` |
| 时区 | 业务日期按 `Asia/Shanghai`；评测固定时钟见 `docs/defaults.md` |
| 布尔 | CSV 中用 `0`/`1`；导入后映射为布尔或整型，规划层统一按真假语义使用 |

用户上传时文件名可不同，意图解析与字段白名单以**列名集合**是否覆盖本契约为准；演示与评测必须使用本目录 fixtures。

## 2. 表：orders（订单）

| 列名 | 类型 | 可空 | 含义 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| `order_id` | string | 否 | 订单号 | 主键 |
| `user_id` | string | 否 | 用户 ID | FK → `users.user_id` |
| `product_id` | string | 否 | 产品 ID | 与退货表关联 |
| `product_name` | string | 否 | 产品名称 | TopN / 展示用 |
| `channel` | string | 否 | 销售渠道 | 枚举见下 |
| `order_date` | date | 否 | 下单日期 | `YYYY-MM-DD` |
| `amount` | float | 否 | 订单金额 | 单位：元；≥ 0 |
| `quantity` | int | 否 | 购买件数 | ≥ 1 |
| `is_promo` | int | 否 | 是否促销单 | `1` 促销，`0` 非促销 |

**`channel` 枚举（v1 固定）**：`APP` | `官网` | `线下门店` | `第三方平台`

## 3. 表：returns（退货）

| 列名 | 类型 | 可空 | 含义 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| `return_id` | string | 否 | 退货单号 | 主键 |
| `order_id` | string | 否 | 原订单号 | FK → `orders.order_id` |
| `product_id` | string | 否 | 产品 ID | 应与对应订单行一致 |
| `return_date` | date | 否 | 退货日期 | `YYYY-MM-DD`；≥ 对应 `order_date` |
| `return_amount` | float | 否 | 退货金额 | ≥ 0；通常 ≤ 原订单金额 |
| `return_reason` | string | 是 | 退货原因 | 可空；分析可不依赖 |

## 4. 表：users（用户）

| 列名 | 类型 | 可空 | 含义 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| `user_id` | string | 否 | 用户 ID | 主键 |
| `user_name` | string | 否 | 用户昵称 | 展示用 |
| `user_segment` | string | 否 | 用户分层 | 枚举见下 |
| `register_date` | date | 否 | 注册日期 | `YYYY-MM-DD` |
| `city` | string | 是 | 城市 | 可空 |
| `phone` | string | 是 | 手机号 | **敏感字段** |
| `email` | string | 是 | 邮箱 | **敏感字段** |

**`user_segment` 枚举（v1 固定）**：`新客` | `老客` | `VIP`

## 5. Join 键与提示

| left | left_column | right | right_column | confidence |
| :--- | :--- | :--- | :--- | :--- |
| `orders.csv` | `user_id` | `users.csv` | `user_id` | 1.0 |
| `returns.csv` | `order_id` | `orders.csv` | `order_id` | 1.0 |
| `returns.csv` | `product_id` | `orders.csv` | `product_id` | 0.9 |

## 6. 敏感字段（与 defaults 对齐）

输出、日志、审计、图表中必须按脱敏规则处理的列：

- 英文列名：`phone`、`mobile`、`email`、`id_card`、`address`
- 中文同义列名：`手机`、`手机号`、`电话`、`邮箱`、`身份证`、`身份证号`、`地址`

本契约 fixtures 中实际出现的敏感列为 `phone`、`email`。

## 7. 六类问题与字段依赖

| 问题类型 | 标签 | 必需表 | 关键字段 | 典型输出 |
| :--- | :--- | :--- | :--- | :--- |
| 退货率 TopN | `return_rate_topn` | orders, returns | `product_id`/`product_name`，退货关联 `order_id` | 产品维度退货率排序 + 柱状图 |
| 渠道对比 | `channel_compare` | orders | `channel`，指标可用 `amount`/单量 | 分渠道对比表 + 柱状图 |
| 时间趋势 | `time_trend` | orders | `order_date`，可选 join returns | 按日/周/月序列 + 折线图 |
| 用户分层对比 | `segment_compare` | orders, users | `user_segment` via `user_id` | 分层指标对比 + 柱状图 |
| 促销与非促销对比 | `promo_compare` | orders | `is_promo` | 两组对比表 + 柱状图 |
| 复购率 | `repurchase_rate` | orders | `user_id`，`order_date` | 复购率标量/分层 + 表 |

**退货率定义（金标准）**：某产品退货率 = 该产品关联退货订单数 / 该产品订单数；分母为 0 时标为异常空，不得伪造比例。

**复购率定义（金标准）**：在分析时间窗内，下单次数 ≥ 2 的用户数 / 下单用户数；分母为 0 同异常空处理。

## 8. 超范围判定

请求涉及下列任一情形时，意图解析应 `in_scope=false` 并拒绝：

- 需要本契约以外的表或列（且无法映射到上表）
- 不属于上表六类问题标签
- 预测、聚类、写库、删改文件等 v1 排除能力

## 9. Fixtures 路径

| 文件 | 说明 |
| :--- | :--- |
| [`eval/datasets/fixtures/orders.csv`](../eval/datasets/fixtures/orders.csv) | 演示与评测订单样例 |
| [`eval/datasets/fixtures/returns.csv`](../eval/datasets/fixtures/returns.csv) | 演示与评测退货样例 |
| [`eval/datasets/fixtures/users.csv`](../eval/datasets/fixtures/users.csv) | 演示与评测用户样例 |
| [`eval/datasets/core.jsonl`](../eval/datasets/core.jsonl) | 分层评测核心集（字段与泄漏标记见 [D-EVALSET-01](tasks.md#D-EVALSET-01)） |

样例规模：用户 12 人、订单约 30 行、退货约 10 行，覆盖全部渠道、分层、促销标记与「上个月」类时间窗（相对评测固定时钟）。
