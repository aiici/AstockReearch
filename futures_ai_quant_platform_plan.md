# Futures AI Quant Platform

## 期货 AI 量化交易平台技术实施计划（交付给 AI 编程助手）

> 文档版本：1.0\
> 目标：指导 AI
> 编程助手按阶段设计、实现、测试一套可扩展的期货量化交易平台。\
> 核心架构：**一合约一 Contract Agent + 品种级 Commodity Agent +
> 全局市场情报中心 + 组合决策中心 + 独立风控与交易执行引擎**。\
> 首期原则：先完成数据、研究、回测和模拟交易；实盘自动交易必须经过独立验收与人工授权，禁止默认启用。

------------------------------------------------------------------------

# 0. 给 AI 编程助手的总指令

你是本项目的资深量化交易系统架构师、Python/Go
工程师和测试负责人。请严格按照本计划开发，不要一次性生成一个无法运行的庞大代码仓库，也不要擅自省略风控、数据质量、回测真实性和交易状态管理。

## 0.1 开发规则

1.  **先阅读本计划，再检查现有仓库。**
    如果仓库已有代码，先分析目录、依赖、接口和已有实现，优先增量修改，不得无理由推倒重写。
2.  每个阶段先输出：设计说明、目录变更、数据模型、接口契约、风险点和测试计划；确认设计自洽后再编码。
3.  每次只实现一个可运行的垂直切片，完成单元测试、集成测试和启动验证后再进入下一阶段。
4.  所有关键业务逻辑必须有类型定义、日志、异常处理和测试；禁止只给伪代码或空函数。
5.  所有时间统一使用带时区的时间戳存储；交易日、自然日、夜盘归属必须分开建模。
6.  所有金额、价格、数量、手续费、保证金和乘数使用 Decimal
    或明确的定点数方案，禁止用二进制浮点数直接进行资金结算。
7.  行情、信号、订单、成交、持仓、风控决策必须有唯一标识、来源、时间戳和审计记录。
8.  AI 输出只能形成研究结论或交易建议。**AI
    不得绕过确定性风控直接调用交易柜台。**
9.  初始配置必须是 `paper` / `simulation`
    模式；实盘模式默认关闭，并设置独立的显式授权开关。
10. 不得假设任何行情源、CTP 字段、交易所规则或外部 API
    的细节。以实际接口文档和适配器契约为准；缺少凭证时提供 Mock
    Adapter。
11. 不得把模型输出当作事实。新闻、基本面和第三方指标必须保存来源、发布时间、采集时间、可信度和有效期。
12. 禁止在日志、Prompt、异常信息和测试样例中泄露账户密码、Token、柜台认证信息等秘密。
13. 不得宣称策略能够稳定盈利。策略必须通过样本外、滚动验证、成本敏感性和模拟交易检验。
14. 不得自动在线修改实盘策略参数。AI
    可以提出候选变更，候选参数必须经过回测、审批和版本发布。
15. 每个阶段结束时提供：变更文件清单、启动命令、测试命令、测试结果、已知问题和下一阶段建议。

## 0.2 默认技术选型

首期采用模块化单体优先、服务边界清晰、后续可拆微服务的方式，避免过早引入复杂分布式系统。

  ----------------------------------------------------------------------------------------------------
  领域                    首选技术                                     说明
  ----------------------- -------------------------------------------- -------------------------------
  后端 API                Python 3.11+、FastAPI、Pydantic 2            首期统一业务 API

  量化计算                NumPy、Polars、SciPy                         指标、特征、统计分析

  Agent 编排              LangGraph                                    状态图、任务编排、Checkpoint

  LLM 接口                统一 Model Gateway                           支持多模型供应商，可替换

  数据库                  PostgreSQL                                   配置、信号、订单、交易、审计

  时序/行情分析           ClickHouse（优先）                           Tick、Bar、特征历史和分析查询

  缓存                    Redis                                        实时状态、缓存、轻量队列

  消息总线                Kafka 或 Redpanda                            事件驱动；开发环境可先用 Redis
                                                                       Streams

  工作流                  首期 LangGraph Checkpoint；复杂长任务再引入  不要同时引入多个重复编排系统
                          Temporal                                     

  前端                    Vue 3 + TypeScript + ECharts                 交易工作台和监控

  交易接口                CTP Adapter（按实际柜台 API 版本适配）       通过接口隔离供应商

  可观测性                OpenTelemetry + Prometheus +                 指标、追踪、LLM 调用
                          Grafana；Langfuse 可选                       

  部署                    Docker Compose 开发；Kubernetes 生产         首期不要求 K8s 才能运行

  测试                    pytest、Hypothesis、Testcontainers（可选）   单元、属性、集成和回测测试
  ----------------------------------------------------------------------------------------------------

说明： - Go/C++ 不作为首期强制要求。先以 Python
建立正确的业务边界和可验证系统。 -
当性能测试证明行情接入或订单链路成为瓶颈，再将对应模块迁移到 Go/C++。 -
不要求一开始使用 MCP、A2A、Neo4j、Agent Swarm。首期先实现内部 Tool
Registry 和事件契约；后续再按真实集成需求接入协议。 -
不要求每个合约创建独立进程或容器。采用逻辑独立的 Actor/Agent
实例，由共享 Runtime 调度。

------------------------------------------------------------------------

# 1. 项目目标与范围

## 1.1 项目目标

建设一套面向期货市场的 AI 量化研究与交易平台，实现：

1.  多交易所、多品种、多月份合约的统一管理。
2.  实时行情接入、清洗、标准化、存储和质量监测。
3.  Tick 聚合为多周期 K 线，计算技术指标和量化特征。
4.  自动采集新闻、宏观、产业链、库存、仓单等外部信息。
5.  采用"一合约一 Agent"模式，为每个启用合约创建独立研究单元。
6.  建立品种级 Agent，处理同一品种的跨期结构、主力切换和合约比较。
7.  建立全局市场情报、组合决策和独立风险控制。
8.  支持策略研究、历史回测、滚动样本外验证和模拟交易。
9.  建立 OMS/EMS 订单执行闭环，首期通过模拟交易适配器验证。
10. 提供可观测、可审计、可恢复、可扩展的运行体系。

## 1.2 首期明确不做

-   不做高频做市或微秒级交易。
-   不允许 LLM 直接读取任意数据库并自行下单。
-   不允许 AI 自主修改实盘策略和风控参数。
-   不将"主力资金流入"等推断指标冒充交易所原始数据。
-   不默认支持所有海外交易所；先通过适配器预留扩展。
-   不以历史回测收益作为实盘盈利保证。
-   不在未验证行情授权、数据许可和交易接口权限前接入真实账户。

## 1.3 首期建议范围

-   市场：国内期货，按实际数据和交易权限接入交易所及相关市场。
-   合约：先选择 10～20
    个流动性较好的合约作为试点，架构支持扩展到全市场。
-   周期：Tick、1m、5m、15m、30m、1h、日线；具体可用周期由行情数据决定。
-   策略：趋势跟踪、突破、回踩、区间均值回归作为研究模板，不预设其有效性。
-   交易：先历史回测，再实时仿真/模拟盘，最后小规模人工授权实盘。

------------------------------------------------------------------------

# 2. 总体架构

``` text
┌────────────────────────────────────────────────────────────┐
│                        前端交易工作台                        │
│ 行情 / 合约 / Agent 状态 / 信号 / 回测 / 订单 / 风险 / 审计   │
└────────────────────────────┬───────────────────────────────┘
                             │ REST / WebSocket
┌────────────────────────────▼───────────────────────────────┐
│                         API Gateway                         │
│ 认证鉴权 / 用户权限 / 限流 / 请求追踪 / API 版本管理           │
└────────────────────────────┬───────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────┐
│                    Agent & Decision Layer                   │
│ Global Market Intelligence                                  │
│ Commodity Agents                                            │
│ Contract Agents（每个合约一个逻辑实例）                        │
│ Portfolio Decision                                          │
│ Strategy Research / Signal Evaluation                       │
└───────────────┬───────────────────────────┬────────────────┘
                │                           │
┌───────────────▼────────────┐  ┌───────────▼────────────────┐
│     Quant & Feature Engine │  │     Context / Knowledge    │
│ Bar / Indicator / Regime   │  │ News / RAG / Memory        │
│ Strategy / Backtest        │  │ Fundamental / Provenance    │
└───────────────┬────────────┘  └───────────┬────────────────┘
                │                           │
┌───────────────▼───────────────────────────▼────────────────┐
│                  Event Bus / Internal Tool Registry         │
│ Market Events / Feature Events / Signal Events / Task Events│
└───────────────┬───────────────────────────┬────────────────┘
                │                           │
┌───────────────▼────────────┐  ┌───────────▼────────────────┐
│       Market Data Layer    │  │    External Data Layer     │
│ CTP Market Adapter / Mock  │  │ News / Macro / Industry    │
│ Tick / Bar / Contract Info │  │ Inventory / Warehouse Data │
└────────────────────────────┘  └────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│                  Independent Trading Control Plane          │
│ Portfolio Risk → Pre-trade Risk → OMS → EMS → Broker Adapter│
│       ↑                                         │            │
│       └──── Account / Order / Trade / Position Reconciliation┘
└────────────────────────────────────────────────────────────┘

横向能力：配置中心、权限、审计、监控、告警、密钥管理、故障恢复
```

## 2.1 关键边界

-   Agent 层只生成分析结果和标准化交易建议。
-   Quant Engine 负责确定性数值计算。
-   Risk Engine 独立于 Agent，拥有最终否决权。
-   OMS/EMS 是唯一订单状态和交易执行入口。
-   Broker Adapter 是唯一允许访问真实交易柜台的模块。
-   数据采集、AI 分析、交易执行相互解耦。
-   每个事件都要有 `event_id`、`event_time`、`ingest_time`、`source` 和
    `schema_version`。

------------------------------------------------------------------------

# 3. Agent 体系设计

## 3.1 Agent 层级

``` text
Global Market Intelligence
          |
          +--------------------+
          |                    |
    Commodity Agent       Macro/News Analysis
          |
    +-----+------+------+
    |            |      |
 Contract A  Contract B Contract C
    |            |      |
    +------------+------+
                 |
        Portfolio Decision
                 |
          Independent Risk
                 |
             OMS / EMS
```

### Global Market Agent

职责： - 汇总宏观、政策、跨市场信息。 -
识别全市场风险事件和交易时段状态。 - 为品种 Agent
提供有来源和有效期的市场背景。 - 不直接产生可执行订单。

### Commodity Agent

每个品种一个逻辑实例，例如 AU、AG、CU、RB、SC 等。

职责： - 管理品种级基本面和产业链信息。 - 汇总该品种不同月份合约。 -
分析期限结构、基差、跨期价差和主力合约变化。 - 向子合约 Agent
发布品种级上下文。 - 识别品种层面的集中风险。

### Contract Agent

每个具体交易合约一个逻辑实例，例如 `SHFE.au2612`。

职责： - 接收该合约的行情和特征事件。 -
维护自己的运行状态、策略启用状态和记忆摘要。 - 识别市场环境。 -
运行适用策略。 - 生成结构化交易信号。 -
记录信号依据、数据时间和失效条件。 -
管理该合约的研究状态；不能直接操作交易柜台。

### Portfolio Decision Agent

职责： - 收集所有合约的候选信号。 -
检查信号冲突、相关性和重复风险暴露。 -
结合账户资金、保证金和风险预算提出组合目标。 - 生成组合调仓建议。 -
不能绕过 Risk Engine。

### Risk Supervisor

首期应主要是确定性规则服务，而不是 LLM Agent。

职责： - 账户级风险预算。 - 单笔、单合约、单品种和组合风险限制。 -
保证金检查。 - 日内亏损限制。 - 数据异常、行情陈旧和交易时段检查。 -
交易所规则约束。 - 熔断、暂停开仓和紧急停止。 -
对所有交易建议给出明确的通过/拒绝原因。

## 3.2 Contract Agent 运行方式

不要为每个合约启动一个独立模型进程或容器。使用共享 Runtime +
独立状态实例：

``` python
class ContractAgent:
    contract_id: str
    product_id: str
    state: ContractAgentState
    strategy_registry: StrategyRegistry
    memory_repository: AgentMemoryRepository

    async def on_market_event(self, event): ...
    async def on_feature_event(self, event): ...
    async def analyze(self, context): ...
    async def generate_signal(self, context): ...
    async def update_state(self, event): ...
```

要求： - 同一合约事件按序处理，防止状态竞争。 - 不同合约可并行处理。 -
同一个事件重复投递时必须幂等。 - Agent 状态可持久化和恢复。 - Agent
关闭或合约到期时保留历史数据。 - Agent
资源按活跃程度调度，不要求所有合约持续调用 LLM。

## 3.3 Agent 状态机

建议初版状态：

-   `OBSERVING`：观察。
-   `ANALYZING`：分析中。
-   `WAITING`：等待条件。
-   `CANDIDATE`：发现候选机会。
-   `PROPOSED`：提交交易建议。
-   `APPROVED`：风险层批准。
-   `EXECUTING`：订单执行中。
-   `POSITION_HOLDING`：有持仓。
-   `EXITING`：退出中。
-   `COOLDOWN`：冷却。
-   `DISABLED`：禁用。
-   `ERROR`：异常，需告警和恢复。

状态转换必须通过明确事件触发，不能由 LLM 随意修改。

------------------------------------------------------------------------

# 4. 行情与数据工程

## 4.1 数据源适配器

定义统一接口：

``` python
class MarketDataAdapter(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self, instruments: list[str]) -> None: ...
    async def unsubscribe(self, instruments: list[str]) -> None: ...
    async def stream(self) -> AsyncIterator[MarketTick]: ...
    async def close(self) -> None: ...
```

实现： - `MockMarketDataAdapter`：开发和测试必须提供。 -
`CTPMarketDataAdapter`：按实际 CTP API 版本和柜台文档实现。 -
后续可以增加其他行情供应商适配器。

交易接口同样采用 `BrokerTradingAdapter` 抽象，首期先完成
`PaperTradingAdapter`，实盘 CTP Adapter 独立实现并默认关闭。

## 4.2 标准行情模型

Tick 至少包含：

``` json
{
  "event_id": "uuid",
  "instrument_id": "SHFE.au2612",
  "exchange": "SHFE",
  "trading_day": "2026-11-12",
  "event_time": "2026-11-12T10:15:01.123+08:00",
  "ingest_time": "2026-11-12T10:15:01.130+08:00",
  "last_price": "923.40",
  "volume": 123456,
  "open_interest": "12345",
  "bid_price_1": "923.30",
  "bid_volume_1": 12,
  "ask_price_1": "923.40",
  "ask_volume_1": 8,
  "source": "mock",
  "schema_version": 1
}
```

价格和资金字段的实际精度按合约规则处理。示例只展示字段形态，不代表某个真实合约的最新行情。

要求： - 区分交易所事件时间和本地接收时间。 -
处理乱序、重复、断流、重连和缺失。 -
校验价格、成交量、持仓量和盘口字段。 - 对陈旧行情设置 `stale` 标记。 -
行情异常时禁止产生新开仓建议。 - 记录行情源、授权范围和数据质量。

## 4.3 合约元数据

合约信息必须来自可信的交易所/柜台元数据或经过核验的数据供应商，不允许硬编码。

至少包括： - `instrument_id` - `exchange` - `product_id` -
`underlying` - `contract_multiplier` - `tick_size` - `listed_date` -
`last_trade_date` - `delivery_month` - `volume_unit` - `price_unit` -
`margin_rate`（区分交易所和经纪商口径） - `commission_rule` -
`trading_session_id` - `position_limit` - `status`

所有规则都应带 `effective_from`、`effective_to` 和来源，支持历史版本。

## 4.4 K 线与特征

首期生成： - 1m、5m、15m、30m、1h、日线。 - OHLCV、持仓量及其变化。 -
VWAP、EMA、ATR、ADX、RSI、ROC。 -
波动率、成交量分位数、盘口不平衡（数据可用时）。 -
基差、近远月价差、期限结构。 - 交易时段、夜盘归属和交易日字段。

要求： - K 线只能使用当时已到达的数据生成。 - 未收盘 K 线与已收盘 K
线必须区分。 - 回测禁止读取未来数据。 -
指标计算必须处理预热窗口和缺失数据。 - 每个特征记录
`feature_time`、`computed_at`、`feature_version`。

## 4.5 外部信息采集

统一采集： - 财经新闻和政策。 - 宏观数据。 - 产业链供需。 -
库存、仓单、产量、开工率、进出口。 - 现货价格和基差。 -
海外关联市场数据。

每条信息必须有： - 原始 URL 或数据源 ID。 - 发布机构/作者。 -
发布时间。 - 采集时间。 - 适用品种。 - 事件类型。 -
原文或原始数据快照（遵守数据授权）。 - AI 摘要。 - 情绪/方向标签。 -
可信度与证据。 - 有效期和过期策略。

禁止将 AI 推断值当作原始事实。不同来源冲突时保留各自证据，不得静默覆盖。

------------------------------------------------------------------------

# 5. 量化研究与策略引擎

## 5.1 市场环境识别

首期建立确定性 Regime Engine，识别： - `TREND_UP` - `TREND_DOWN` -
`RANGE` - `HIGH_VOLATILITY` - `LOW_VOLATILITY` - `EVENT_RISK` -
`UNKNOWN`

可使用趋势强度、波动率分位数、ATR、ADX、区间宽度等特征。阈值必须可配置、可版本化并通过回测验证。

市场环境识别不应仅依赖 LLM。

## 5.2 策略接口

``` python
class Strategy(Protocol):
    strategy_id: str
    version: str

    def warmup(self, history: MarketHistory) -> None: ...
    def on_bar(self, context: StrategyContext) -> StrategyDecision: ...
    def on_tick(self, context: StrategyContext) -> StrategyDecision | None: ...
```

首期研究策略： 1. 趋势跟踪。 2. 突破。 3. 趋势回踩。 4. 区间均值回归。
5. 跨期价差研究（先研究和回测，不默认自动交易）。

每个策略必须： - 有明确入场、退出、止损和失效条件。 - 有适用市场环境。 -
有交易成本模型。 - 有参数版本。 - 有单元测试。 - 不允许读取未来数据。 -
支持禁用、灰度和回滚。

## 5.3 信号模型

统一信号对象：

``` json
{
  "signal_id": "uuid",
  "contract_id": "SHFE.au2612",
  "strategy_id": "trend_pullback_v1",
  "strategy_version": "1.0.0",
  "created_at": "2026-11-12T10:15:00+08:00",
  "valid_until": "2026-11-12T10:20:00+08:00",
  "market_regime": "TREND_UP",
  "direction": "LONG",
  "signal_strength": 0.78,
  "entry_type": "LIMIT",
  "entry_price": "923.40",
  "stop_price": "918.40",
  "take_profit_levels": ["933.40"],
  "risk_reward": "2.0",
  "evidence_refs": ["feature:uuid", "news:uuid"],
  "data_quality_score": 0.98,
  "reason_codes": ["TREND_CONFIRMED", "PULLBACK_SUPPORT"],
  "status": "PROPOSED"
}
```

注意： - `signal_strength` 是评分，不等于概率。 -
如输出概率，必须用独立历史样本校准并记录校准版本。 -
信号必须有过期时间；过期信号不能执行。 -
价格必须按合约最小变动价位取整。 - 交易建议必须说明无效条件和风险。

------------------------------------------------------------------------

# 6. AI 与 LLM 设计

## 6.1 LLM 使用边界

适合 LLM： - 新闻分类、摘要、事件抽取。 - 产业链关系解释。 -
多来源信息归纳。 - 研究报告生成。 - 对量化信号进行证据解释。 -
提出待验证的策略假设。

不适合直接交给 LLM： - Tick 级实时计算。 -
精确资金、保证金和盈亏结算。 - 订单状态管理。 - 交易所规则最终判定。 -
风险限额最终判断。 - 直接向真实柜台发送委托。

## 6.2 Model Gateway

实现统一模型接口： - `chat()` - `structured_output()` - `embed()` -
`health_check()`

支持不同供应商和本地模型，通过配置切换。记录模型名称、版本、Prompt
版本、输入输出 Token、耗时、成本和错误。

## 6.3 Prompt 与结构化输出

所有 Agent Prompt 放在版本化文件中，不要散落在代码中。

要求： - 使用 Pydantic Schema 验证模型输出。 - JSON
解析失败时有限次数重试。 - 外部新闻和网页内容视为不可信输入，防止 Prompt
Injection。 - 模型输出必须附证据引用。 - 低置信度或证据不足时输出
`INSUFFICIENT_DATA`，而不是编造结论。 - LLM
调用应有超时、预算、限流和熔断。

## 6.4 Context 与 Memory

记忆分层： - Working Memory：当前任务和 Agent 状态，Redis/数据库。 -
Episodic Memory：历史分析、交易复盘和事件，PostgreSQL。 - Semantic
Memory：知识和文档索引，pgvector。 - Procedural
Memory：策略说明、操作规程和 Workflow 版本。

不要将全部历史 Tick、K
线或完整新闻塞进上下文。根据当前合约、任务、时间范围和证据相关性动态检索。

------------------------------------------------------------------------

# 7. 组合决策与仓位管理

## 7.1 Portfolio Decision 输入

-   所有未过期候选信号。
-   当前账户权益、可用资金和保证金。
-   当前订单、成交和持仓。
-   合约乘数、最小价位、手续费和保证金规则。
-   合约/品种相关性和风险暴露。
-   市场环境和事件风险。
-   风险预算及策略限额。

## 7.2 组合决策输出

输出目标仓位或交易建议，不直接下单：

``` json
{
  "decision_id": "uuid",
  "account_id": "paper-account-001",
  "created_at": "2026-11-12T10:15:00+08:00",
  "targets": [
    {
      "contract_id": "SHFE.au2612",
      "target_direction": "LONG",
      "target_volume": 1,
      "source_signal_ids": ["uuid"],
      "risk_budget": "500.00"
    }
  ],
  "rejected_signals": [],
  "portfolio_risk_summary": {},
  "requires_risk_approval": true
}
```

## 7.3 仓位算法

可以研究 ATR 风险预算、波动率目标、相关性约束和组合风险贡献。

示例公式：

`contracts = floor(risk_budget / (stop_distance * contract_multiplier + estimated_cost_per_contract))`

实际计算必须进一步考虑： - 最小变动价位。 - 手续费和滑点。 -
保证金占用。 - 涨跌停与流动性。 - 单笔风险上限。 -
单合约、单品种和组合风险上限。 - 账户可用资金。 - 相关品种集中暴露。

公式计算结果还必须通过 Risk Engine 校验，不得直接作为订单数量。

------------------------------------------------------------------------

# 8. 风控系统（最高优先级）

## 8.1 风控必须独立

风控服务不能依赖 Agent 是否正常，也不能依赖 LLM 返回结果。

任何新订单或改单都必须经过 Pre-trade Risk。Risk Engine
不可用时，默认拒绝新增风险敞口。

## 8.2 风控规则

至少实现： 1. 单笔最大风险。 2. 单合约最大手数。 3. 单品种最大风险。 4.
账户总风险上限。 5. 保证金占用上限。 6. 日内最大亏损。 7. 最大回撤阈值。
8. 连续亏损冷却。 9. 行情陈旧/断流禁止开仓。 10.
异常价差和异常波动禁止开仓。 11. 流动性不足限制。 12. 交易时段校验。 13.
合约到期和交割风险限制。 14. 交易所持仓限额和其他规则校验。 15.
重复委托和订单频率限制。 16. 全局 Kill Switch。

每条规则都应： - 有配置版本。 - 有启用状态。 - 有明确拒绝码。 -
有审计日志。 - 有测试覆盖。 - 记录规则生效时间。

## 8.3 风控决策模型

``` json
{
  "risk_check_id": "uuid",
  "decision_id": "uuid",
  "approved": false,
  "reason_codes": ["MARGIN_LIMIT_EXCEEDED"],
  "checked_at": "2026-11-12T10:15:01+08:00",
  "risk_config_version": "1.0.0"
}
```

任何无法确定的风险检查都应 fail-closed（拒绝新增风险），而不是默认通过。

## 8.4 Kill Switch

至少支持： - 全局禁止开仓。 - 单账户禁止开仓。 - 单合约禁止开仓。 -
取消未成交开仓委托（按明确策略执行）。 -
保留止损/减仓/平仓能力的独立规则。 - 紧急平仓需要单独授权、确认和审计。

"停止开仓"与"全部平仓"必须是两个不同操作，不能混为一谈。

------------------------------------------------------------------------

# 9. OMS / EMS 与交易执行

## 9.1 交易适配器

定义统一接口：

``` python
class BrokerTradingAdapter(Protocol):
    async def connect(self) -> None: ...
    async def query_account(self): ...
    async def query_positions(self): ...
    async def query_orders(self): ...
    async def submit_order(self, order): ...
    async def cancel_order(self, order_id): ...
    async def stream_order_updates(self): ...
    async def stream_trade_updates(self): ...
    async def close(self) -> None: ...
```

先实现： - `PaperTradingAdapter`。 - `MockBrokerAdapter`。

真实 CTP
适配器必须在单独阶段实现，且通过配置和权限双重关闭默认实盘能力。

## 9.2 订单状态机

至少支持： - `CREATED` - `RISK_REJECTED` - `RISK_APPROVED` -
`SUBMITTING` - `SUBMITTED` - `PARTIALLY_FILLED` - `FILLED` -
`CANCEL_PENDING` - `CANCELED` - `REJECTED` - `UNKNOWN` - `ERROR`

必须处理： - 部分成交。 - 撤单与成交竞争。 -
网络超时但柜台实际已收到委托。 - 重复回报。 - 柜台断线重连。 -
交易日切换。 - 本地状态与柜台状态不一致。

## 9.3 幂等与对账

-   每个业务委托有内部唯一 `client_order_id`。
-   重试不得产生重复订单。
-   订单状态以柜台回报和查询结果为事实来源。
-   重连后先查询账户、持仓、委托和成交，再恢复执行。
-   建立定时对账任务，发现差异立即告警。
-   不允许在状态不确定时盲目重发委托。

## 9.4 实盘保护

实盘模式必须同时满足： - 配置显式启用。 - 账户白名单。 -
用户/操作者具备授权。 - 独立风险配置通过审核。 - Kill Switch 可用。 -
行情和交易连接健康。 - 订单/持仓对账正常。 - 当日交易时段合法。 -
模拟和故障演练验收通过。

------------------------------------------------------------------------

# 10. 回测与模拟交易

## 10.1 回测要求

回测引擎必须模拟： - 手续费。 - 滑点。 - 买卖价差。 - 合约乘数。 -
最小变动价位。 - 保证金。 - 涨跌停和无法成交情形（数据可用时）。 -
夜盘交易日归属。 - 合约到期和换月。 -
部分成交或成交概率模型（按回测精度逐步完善）。

必须防止： - 未来函数。 - 数据泄漏。 - 使用未收盘 K 线提前交易。 -
用主力连续合约价格直接模拟真实合约成交。 -
忽略手续费、滑点和换月成本。 - 参数反复试验导致的过拟合。

## 10.2 验证流程

1.  训练/研究区间。
2.  验证区间。
3.  严格样本外区间。
4.  Walk-forward 滚动验证。
5.  参数敏感性测试。
6.  成本增加压力测试。
7.  极端行情测试。
8.  多品种组合测试。
9.  实时模拟交易。
10. 小规模人工授权实盘观察。

## 10.3 绩效指标

-   净收益。
-   年化收益（注明计算口径）。
-   最大回撤。
-   Sharpe。
-   Sortino。
-   Calmar。
-   胜率。
-   盈亏比。
-   Profit Factor。
-   平均持仓时间。
-   换手率。
-   手续费占比。
-   最大连续亏损。
-   保证金峰值。
-   不同市场环境下的表现。

不能只凭胜率或总收益评估策略。

------------------------------------------------------------------------

# 11. 数据库初版设计

先建立以下逻辑实体，字段可以在实施阶段细化。

## 11.1 基础数据

-   `exchanges`
-   `products`
-   `contracts`
-   `trading_sessions`
-   `contract_rule_versions`
-   `data_sources`

## 11.2 行情与特征

-   `market_ticks`（建议落 ClickHouse）
-   `market_bars`
-   `market_features`
-   `market_data_quality_events`
-   `continuous_contract_mappings`

## 11.3 外部信息

-   `news_items`
-   `macro_indicators`
-   `fundamental_observations`
-   `inventory_observations`
-   `market_events`
-   `knowledge_documents`
-   `evidence_sources`

## 11.4 Agent 与策略

-   `agent_instances`
-   `agent_states`
-   `agent_memory_records`
-   `agent_tasks`
-   `agent_execution_traces`
-   `strategies`
-   `strategy_versions`
-   `strategy_configs`
-   `strategy_backtest_runs`
-   `agent_signals`

## 11.5 组合、风控和交易

-   `accounts`
-   `portfolio_decisions`
-   `risk_configs`
-   `risk_config_versions`
-   `risk_checks`
-   `risk_events`
-   `orders`
-   `order_events`
-   `trades`
-   `positions`
-   `account_snapshots`
-   `reconciliation_runs`
-   `audit_logs`

要求： - 所有业务表有主键和必要索引。 -
重要记录保留创建时间、更新时间和版本。 -
订单、成交、审计记录不得随意物理删除。 -
资金和交易记录保留完整变更历史。 - 需要定义数据保留和归档策略。 -
ClickHouse 与 PostgreSQL
的数据一致性通过事件和可重建流程保证，不做跨库强事务假设。

------------------------------------------------------------------------

# 12. 事件总线与事件契约

首期定义以下事件：

-   `MarketTickReceived`
-   `MarketBarClosed`
-   `MarketDataStale`
-   `FeatureComputed`
-   `NewsCollected`
-   `FundamentalUpdated`
-   `MarketRegimeChanged`
-   `AgentAnalysisRequested`
-   `AgentSignalProposed`
-   `PortfolioDecisionCreated`
-   `RiskCheckApproved`
-   `RiskCheckRejected`
-   `OrderSubmitted`
-   `OrderUpdated`
-   `TradeFilled`
-   `PositionUpdated`
-   `AgentStateChanged`
-   `KillSwitchActivated`

每个事件包含： - `event_id` - `event_type` - `schema_version` -
`event_time` - `ingest_time` - `correlation_id` - `causation_id` -
`source` - `payload`

消费者必须幂等。重要事件需要持久化和重放能力。

------------------------------------------------------------------------

# 13. API 初版

按 REST API + WebSocket 设计，所有 API 需要鉴权、权限和统一错误码。

## 13.1 合约

-   `GET /api/v1/contracts`
-   `GET /api/v1/contracts/{contract_id}`
-   `POST /api/v1/contracts/{contract_id}/enable`
-   `POST /api/v1/contracts/{contract_id}/disable`

## 13.2 Agent

-   `GET /api/v1/agents`
-   `GET /api/v1/agents/{agent_id}`
-   `GET /api/v1/agents/{agent_id}/state`
-   `POST /api/v1/agents/{agent_id}/analyze`
-   `GET /api/v1/agents/{agent_id}/signals`

## 13.3 行情和特征

-   `GET /api/v1/market/{contract_id}/ticks`
-   `GET /api/v1/market/{contract_id}/bars`
-   `GET /api/v1/market/{contract_id}/features`
-   `GET /api/v1/market/{contract_id}/quality`

## 13.4 策略和回测

-   `GET /api/v1/strategies`
-   `POST /api/v1/backtests`
-   `GET /api/v1/backtests/{run_id}`
-   `GET /api/v1/backtests/{run_id}/metrics`

## 13.5 决策、风控和交易

-   `GET /api/v1/signals`
-   `GET /api/v1/portfolio/decisions`
-   `GET /api/v1/risk/status`
-   `POST /api/v1/trading/paper/orders`
-   `GET /api/v1/orders`
-   `POST /api/v1/orders/{order_id}/cancel`
-   `GET /api/v1/positions`
-   `GET /api/v1/accounts`
-   `POST /api/v1/risk/kill-switch`

实盘订单 API 不应在首期开放。所有会改变交易状态的 API
都必须具备幂等键、权限校验和审计记录。

## 13.6 WebSocket

-   `/ws/market`
-   `/ws/agents`
-   `/ws/signals`
-   `/ws/orders`
-   `/ws/risk`

------------------------------------------------------------------------

# 14. 前端功能规划

首期前端至少包含：

1.  **总览仪表盘**
    -   账户权益、可用资金、保证金占用。
    -   当日盈亏、累计盈亏、回撤。
    -   活跃合约数、运行 Agent 数。
    -   行情连接和交易连接状态。
    -   风控状态和告警。
2.  **合约监控**
    -   合约列表、主力标记、流动性。
    -   多周期 K 线。
    -   成交量、持仓量、指标。
    -   市场环境。
    -   当前信号和失效时间。
3.  **Agent 控制台**
    -   每个合约 Agent 的状态。
    -   最近分析任务。
    -   运行日志和异常。
    -   策略启停。
    -   记忆摘要和证据来源。
4.  **市场情报**
    -   新闻、政策、宏观数据。
    -   产业链和库存变化。
    -   事件影响品种。
    -   信息来源与可信度。
5.  **策略研究**
    -   策略列表和版本。
    -   参数配置。
    -   回测任务。
    -   收益曲线和风险指标。
    -   样本外验证结果。
6.  **组合决策**
    -   所有候选信号。
    -   信号冲突。
    -   目标仓位。
    -   组合风险暴露。
    -   决策证据。
7.  **交易与风控**
    -   委托、成交、持仓。
    -   订单状态流转。
    -   风控拒绝原因。
    -   Kill Switch。
    -   对账结果和审计日志。

------------------------------------------------------------------------

# 15. 安全、权限与合规

必须实现： - API 身份认证和 RBAC。 -
账户、交易、策略和系统管理权限分离。 - 密钥通过环境变量或 Secret Manager
管理。 - 不在代码仓库提交真实账户凭证。 - 交易操作审计。 - LLM
工具白名单和参数 Schema。 - 外部文档 Prompt Injection 防护。 -
代码执行沙箱（若未来支持 CodeAct）。 -
数据供应商授权和数据使用范围管理。 - 用户确认和高风险操作二次审批。 -
交易所规则、经纪商规则和监管要求核验。

禁止： - 让 LLM 执行任意 SQL 写操作。 - 让 LLM 直接访问 CTP 交易接口。 -
让 MCP 工具绕过风险层下单。 - 在日志中输出密码、Token
或完整敏感账户信息。 - 将新闻中的指令当作系统指令执行。

------------------------------------------------------------------------

# 16. 可观测性与运维

## 16.1 系统指标

-   行情接收延迟。
-   行情断流次数。
-   Tick 丢失/重复率。
-   K 线生成延迟。
-   特征计算耗时。
-   Kafka 消费积压。
-   Agent 任务耗时和失败率。
-   LLM 调用次数、Token 和成本。
-   信号生成数量和过期率。
-   风控拒绝数量及原因。
-   订单提交延迟。
-   成交回报延迟。
-   订单/持仓对账差异。
-   数据库连接和查询性能。

## 16.2 日志与追踪

每次完整决策使用同一个 `correlation_id` 串联： 行情事件 → 特征 → Agent
分析 → 信号 → 组合决策 → 风控 → 订单 → 成交 → 持仓更新。

日志应结构化，区分： - INFO：正常业务事件。 -
WARN：数据质量或风险异常。 - ERROR：任务或服务失败。 -
AUDIT：交易、配置和权限变更。

## 16.3 故障恢复

必须演练： - 行情断线。 - 交易柜台断线。 - Kafka/Redis 短暂不可用。 -
PostgreSQL 重启。 - Agent Worker 崩溃。 - 重复事件。 - 订单回报乱序。 -
本地订单状态与柜台不一致。 - 系统时钟偏差。 - 夜盘跨自然日和交易日切换。

------------------------------------------------------------------------

# 17. 项目阶段与交付验收

按阶段推进，不要一次性开发全部功能。

## Phase 0：仓库初始化与架构骨架

交付： - 项目目录。 - 配置管理。 - 日志和异常体系。 - FastAPI
健康检查。 - PostgreSQL/Redis 开发环境。 - Docker Compose。 - CI
基础测试。

验收： - 一条命令启动开发环境。 - 健康检查可用。 - 自动化测试通过。 -
不包含真实交易凭证。

## Phase 1：行情与合约基础设施

交付： - 合约元数据模型。 - Mock 行情 Adapter。 - CTP 行情 Adapter
接口和适配骨架。 - Tick 标准化。 - K 线生成。 - 特征计算。 -
数据质量监控。 - 行情查询 API。

验收： - Mock 数据可以连续产生 Tick。 - 能生成多周期 K 线。 -
重复、乱序和缺失数据有测试。 - 行情陈旧能触发告警。 -
不存在未来数据泄漏。

## Phase 2：Agent Runtime 与一合约一 Agent

交付： - Contract Agent 基类。 - Commodity Agent。 - Global Market
Agent。 - Agent Manager。 - Agent 状态机。 - 状态持久化。 -
事件订阅和幂等处理。 - Agent 控制台 API。

验收： - 可以注册多个合约 Agent。 - 各 Agent 状态隔离。 -
同一合约事件有序处理。 - Agent 崩溃后能够恢复状态。 -
新增合约无需复制代码。

## Phase 3：策略与信号

交付： - Regime Engine。 - 策略接口。 - 首批研究策略。 - Signal
Schema。 - 信号过期机制。 - 信号解释和证据关联。 - 策略版本管理。

验收： - 同样输入和策略版本产生确定性结果。 - 信号可以复现。 -
信号不直接触发真实委托。 - 每个信号可追溯数据和策略版本。

## Phase 4：AI 情报与分析

交付： - 新闻采集 Adapter。 - 新闻去重和事件分类。 -
宏观和产业数据接口。 - LLM Model Gateway。 - 基本面分析流程。 -
Context/Memory。 - 结构化输出和评估。

验收： - AI 结论带来源和时间。 - 无来源时能明确返回证据不足。 - Prompt
Injection 测试通过。 - LLM 故障不影响行情和风控主链路。 - LLM
调用有预算和超时。

## Phase 5：回测与组合决策

交付： - 历史回测引擎。 - 成本和滑点模型。 - 合约换月处理。 - 样本外与
Walk-forward。 - 组合风险分析。 - Portfolio Decision。 - 回测报告。

验收： - 关键策略通过无未来函数测试。 - 成本变化会反映在结果中。 -
回测可重复。 - 输出完整风险指标。 - 不将回测结果自动发布到实盘。

## Phase 6：模拟交易与 OMS/EMS

交付： - Paper Trading Adapter。 - OMS/EMS。 - 订单状态机。 -
成交模拟。 - 持仓与账户计算。 - 风控规则。 - 对账。 - Kill Switch。

验收： - 重复请求不会重复下单。 - 部分成交、撤单和重连测试通过。 -
风控不可用时拒绝开仓。 - 账户、订单、成交和持仓账实一致。 -
可完整回放一次模拟交易链路。

## Phase 7：实盘接入（单独审批）

交付： - 真实交易柜台适配。 - 账户白名单。 - 生产密钥管理。 -
实盘权限审批。 - 监控告警和故障演练。 - 小规模人工授权交易流程。

验收： - 模拟交易和实盘接口隔离。 - 实盘默认关闭。 -
交易柜台对账准确。 - Kill Switch 经演练验证。 -
风控、权限和审计通过验收。 - 未经明确授权不得自动实盘。

------------------------------------------------------------------------

# 18. 测试清单

AI 编程助手必须持续维护测试，不得只在项目最后补测试。

## 单元测试

-   合约元数据校验。
-   交易日和交易时段。
-   K 线聚合。
-   指标计算。
-   特征计算。
-   策略入场和退出。
-   价格最小变动单位取整。
-   手续费和保证金计算。
-   仓位计算。
-   风控拒绝逻辑。
-   Agent 状态转换。

## 集成测试

-   Mock 行情 → Tick → Bar → Feature。
-   Feature → Contract Agent → Signal。
-   多合约信号 → Portfolio Decision。
-   Decision → Risk → Paper OMS。
-   Order → Trade → Position。
-   Agent Worker 重启后的状态恢复。
-   事件重复投递和幂等。

## 回测测试

-   禁止未来数据。
-   未收盘 K 线处理。
-   夜盘和交易日归属。
-   合约换月。
-   涨跌停与无法成交。
-   手续费、滑点敏感性。
-   样本外测试。
-   参数过拟合检查。

## 故障测试

-   行情断流。
-   交易断线。
-   消息重复和乱序。
-   数据库短暂不可用。
-   Agent 超时。
-   LLM 超时或输出非法 JSON。
-   风控服务不可用。
-   订单状态 UNKNOWN。
-   重连后的订单和持仓对账。

------------------------------------------------------------------------

# 19. 首期默认配置

所有参数集中管理，以下为开发默认值，不代表实盘风险建议：

``` yaml
app:
  name: futures-ai-platform
  environment: development
  timezone: Asia/Shanghai

trading:
  mode: paper
  live_trading_enabled: false
  require_manual_approval: true
  allow_ai_direct_order: false

agents:
  runtime: shared
  one_agent_per_contract: true
  commodity_agent_enabled: true
  max_concurrent_analyses: 10
  llm_only_on_relevant_events: true
  persist_state: true

market_data:
  adapter: mock
  stale_data_protection: true
  bar_intervals: ["1m", "5m", "15m", "30m", "1h", "1d"]
  preserve_exchange_timestamp: true

risk:
  fail_closed: true
  reject_on_stale_market_data: true
  reject_on_unknown_contract_rules: true
  require_pre_trade_check: true
  global_kill_switch_enabled: true

ai:
  model_gateway_enabled: true
  structured_output_required: true
  max_retries: 2
  timeout_seconds: 30
  require_evidence_refs: true

execution:
  adapter: paper
  idempotency_required: true
  reconcile_after_reconnect: true
```

上述并发数、超时和周期仅是开发初始配置，应通过压测和业务需求调整。

------------------------------------------------------------------------

# 20. 第一轮开发任务：从可运行 MVP 开始

不要立即实现整个计划。第一轮只完成以下闭环：

``` text
Mock 行情
   ↓
合约注册
   ↓
一合约一 Contract Agent
   ↓
Tick / K线 / 特征
   ↓
一个确定性示例策略
   ↓
结构化交易信号
   ↓
Risk Engine
   ↓
Paper OMS
   ↓
模拟成交
   ↓
持仓更新
   ↓
前端/接口查询
```

第一轮必须实现：

1.  项目骨架和启动配置。
2.  PostgreSQL、Redis 开发环境。
3.  合约元数据模型和初始化数据。
4.  MockMarketDataAdapter。
5.  Tick 标准模型。
6.  1m/5m K 线生成。
7.  基础指标计算。
8.  ContractAgent 基类和 AgentManager。
9.  一个示例合约 Agent。
10. 一个简单、确定性的趋势策略。
11. Signal Schema。
12. 最小版 Risk Engine。
13. PaperTradingAdapter。
14. 最小 OMS 和订单状态机。
15. 持仓更新。
16. 健康检查和基本监控。
17. 完整自动化测试。

第一轮不要求： - 真实 CTP 实盘下单。 - 全部期货品种。 - 复杂多 Agent
群体协作。 - 自动策略进化。 - 全量新闻和产业链采集。 - 复杂知识图谱。 -
自动实盘参数优化。

## 第一轮验收场景

使用 Mock 数据模拟一个合约的行情：

1.  系统注册一个测试合约。
2.  Agent Manager 创建对应 Contract Agent。
3.  Mock Adapter 持续发送 Tick。
4.  系统生成 1m 和 5m K 线。
5.  Feature Engine 计算特征。
6.  策略在满足条件时产生信号。
7.  Signal 进入 Portfolio/Risk 流程。
8.  Risk Engine 批准或拒绝，并记录原因。
9.  只有批准的建议进入 Paper OMS。
10. Paper Adapter 模拟委托和成交。
11. Position Service 更新持仓。
12. API 可以查询完整链路。
13. 使用 `correlation_id` 能从行情追踪到成交。
14. 重复事件、进程重启和行情断流测试通过。

------------------------------------------------------------------------

# 21. AI 编程助手的执行顺序与输出要求

请严格按以下顺序工作：

**第一步：仓库检查** - 输出当前目录结构。 -
识别已有代码、依赖和启动方式。 - 标记可复用模块和风险。 -
不要直接覆盖现有文件。

**第二步：架构落图** - 输出模块依赖图。 - 输出核心数据流。 -
输出核心状态机。 - 输出数据库 ER 设计。 - 输出 API 契约。 -
输出事件契约。

**第三步：建立 MVP** - 按第 20 节实现最小闭环。 -
每个模块都必须有可运行实现。 - 先 Mock，后真实接口。 -
先模拟交易，后实盘。

**第四步：测试和修复** - 运行全部测试。 - 修复失败项。 -
做一次完整端到端模拟。 - 输出测试报告和已知限制。

**第五步：逐阶段扩展** - 按 Phase 1 到 Phase 7 推进。 -
每阶段先给设计，再给代码。 - 每阶段结束形成可运行版本和变更说明。

每次提交代码时必须输出： 1. 本次实现目标。 2. 新增/修改文件清单。 3.
关键设计说明。 4. 数据库迁移命令。 5. 启动命令。 6.
测试命令和真实测试结果。 7. 未完成事项。 8. 风险和下一步。

**最终交付标准：** 一个可以本地启动、可以用 Mock
行情跑通单合约完整模拟交易闭环、具备可扩展的一合约一 Agent
架构、具备独立风控和完整测试的工程项目。不能用"代码已生成"代替"项目已验证运行"。

------------------------------------------------------------------------

# 附录 A：关键架构决策

  决策项       本项目选择                        原因
  ------------ --------------------------------- ------------------------
  Agent 实例   每合约一个逻辑实例                状态和策略隔离
  Agent 部署   共享 Runtime                      避免每合约独立进程成本
  Agent 编排   LangGraph                         状态化流程和可恢复执行
  数值计算     确定性 Python/NumPy/Polars        可测试、可复现
  LLM 定位     情报理解和研究辅助                不负责精确交易执行
  交易权限     独立 Risk + OMS/EMS               防止 AI 越权
  消息传递     事件驱动                          解耦数据、分析和交易
  数据存储     PostgreSQL + ClickHouse + Redis   业务、行情、缓存分工
  首期交易     Paper Trading                     降低真实资金风险
  部署策略     模块化单体起步                    降低早期运维复杂度
  实盘开关     默认关闭                          必须经过独立验收和授权

# 附录 B：重要术语

-   **Contract Agent**：具体交易合约的独立研究智能体。
-   **Commodity Agent**：品种级智能体，管理多个交割月份合约的关系。
-   **Global Market Agent**：全市场宏观与事件情报中心。
-   **Portfolio Decision**：组合决策，统一协调多合约信号和风险暴露。
-   **Risk Engine**：确定性风控引擎，具有最终否决权。
-   **OMS**：Order Management System，订单管理系统。
-   **EMS**：Execution Management System，交易执行系统。
-   **CTP**：期货交易相关接口体系，实际能力以柜台和接口版本为准。
-   **Regime**：市场环境，如趋势、震荡、高波动。
-   **Walk-forward**：滚动样本外验证方法。
-   **Paper Trading**：模拟交易，不向真实交易柜台发送订单。
-   **Idempotency**：幂等，同一业务请求重复处理不会造成重复副作用。
-   **Kill
    Switch**：紧急停止机制，用于禁止新增风险或按授权执行紧急操作。
