# 智能运营决策分析平台

面向用户行为序列分析、RFM分层、流失预警、智能推荐、归因建模、活动效果评估与实时运营决策的数据分析工程平台。

这是一个前后端分离的管理平台：前端 React + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 React + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为序列 | `sequence` | 序列模式 | 模式编号、用户分群、事件序列 |
| RFM分层 | `rfm` | 分层规则 | 规则编号、最近消费天界、消费频次界 |
| 流失预警 | `churn` | 预警模型 | 模型编号、模型名称、特征变量 |
| 智能推荐 | `recommend` | 推荐策略 | 策略编号、推荐场景、召回算法 |
| 归因建模 | `attrmodel` | 归因模型 | 模型编号、模型名称、归因逻辑 |
| 活动评估 | `activity` | 活动方案 | 方案编号、活动名称、活动类型 |
| 实时决策 | `realtime` | 决策规则 | 规则编号、触发事件、决策条件 |
| 预算分配 | `budget` | 预算方案 | 方案编号、预算总额、渠道分配比例 |
| LTV估算 | `lifetime` | LTV模型 | 模型编号、模型名称、估算方法 |
| 实验变体 | `abvariant` | 变体方案 | 变体编号、关联实验、变体名称 |
| 运营看板 | `dashboard` | 看板视图 | 视图编号、视图名称、指标组合 |
| 评分卡 | `scorecard` | 评分卡 | 评分编号、评分对象、特征变量组 |
| 聚类分群 | `cluster` | 聚类任务 | 任务编号、聚类算法、特征维度 |
| 对比分析 | `comparison` | 对比组 | 对比编号、对比名称、实验组筛选 |
| 数据漂移 | `datadrift` | 漂移检测 | 检测编号、监控数据集、基线版本 |
| 推送计划 | `pushplan` | 推送方案 | 方案编号、推送内容、目标人群 |
| 指标归因 | `metricdrill` | 归因分析 | 分析编号、目标指标、波动方向 |
| 策略库 | `tacticlib` | 运营策略 | 策略编号、策略名称、策略类型 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 动作请求体既支持 `{"values": {"action": "..."}}`，也支持历史的顶层 `{"action": "..."}`；
  路由统一用 `payload.merged_values()` 取值，避免动作静默失效。
- `/export`、`/rule` 这类固定路径必须声明在 `/{entry_id}` 之前，否则会被整数路径参数吃掉。

## 归因建模 ↔ 指标下钻（核验码 11500547 / 口径 113b54c4）

归因模型生命周期与指标根因是一条链，规则集中在：

- `backend/app/services/attribution_rules.py`：规范口径（单触点允许范围、权重合计阈值、触点数上限）
  与贡献度重算；归因建模和指标下钻共用这一份判定。
- `backend/app/services/attribution_ledger.py`：数据集编号、落库凭证、处理终点、失效作废的公共写入。
- `backend/app/services/attrmodel.py`：模型状态机、下线回执与向指标下钻的传播。
- `backend/app/services/metricdrill.py`：指标分析状态机、数据集保存/落定、重新分析、验证核对。
- `backend/app/services/reconcile.py`：启动自检，清掉「已下线模型仍在支撑当前根因」的历史残留。

关键口径：

| 约束 | 表现 |
| --- | --- |
| 评审中不能直接发布 | `发布模型` 需要「模型评审中」且评审结论为「通过」 |
| 下线传播 | 生成 `ATRC-*` 回执，引用该模型的指标分析转「来源已失效」，标待选模型、保留来源失效时间 |
| 已下线模型 | 不能保存新数据集、不能重新分析、不能确认结论；当前根因让位到 `失效前根因` |
| 原告警时点 | `原直接贡献率` / `原间接贡献率` 只在首次记录时写入，之后不再改写 |
| 单一处理终点 | 同一指标分析只有一个 `已选处理终点`，`处理终点历史` 记录被取代的终点 |
| 有效数据集唯一 | 每条指标分析最多一个 `有效标记=true` 的数据集，作废的保留凭证 |
| 数据编号复查 | `GET /api/metricdrill/datasets/{数据编号}` 返回是否仍支撑当前根因、作废凭证与原告警时点贡献率 |

接口：

- `GET /api/attrmodel/rule`、`GET /api/attrmodel/receipts`
- `GET /api/metricdrill/rule`、`GET /api/metricdrill/datasets`、`GET /api/metricdrill/datasets/{数据编号}`
- `GET /api/metricdrill/{id}/detail`（当前根因、有效数据集、待选模型、异常批次、落库凭证）
- `POST /api/metricdrill/{id}/actions`（`重新分析`、`保存归因明细数据集`、`标记来源失效`、`核对验证结果`）
- `POST /api/metricdrill/{id}/datasets/settle`（把待处理数据集落定）

前端指标下钻详情页在 `/metricdrill/:id`，列表页的排名与「下钻明细」都进入同一页；
归因建模页展示下线回执，两个页面不会出现一侧有新根因、另一侧还挂着已下线模型结论的错位。

领域：数据工程
子方向：数据分析与数据应用
