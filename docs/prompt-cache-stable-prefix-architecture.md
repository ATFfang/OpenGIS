# OpenGIS Prompt Cache 稳定前缀架构升级方案

> 修订原则：默认不依赖 `prompt_cache_key` / `prefix_caching.cache_key`。  
> 主流 OpenAI-compatible / DeepSeek-compatible 路径应优先通过"前缀文本完全稳定"自动命中缓存。

---

## 1. 结论

OpenGIS 后续的 Prompt Cache 优化主线应从"给 provider 传缓存 key"调整为：

```text
构建稳定 Provider Request
  -> 固定 tools / system / memory 的前缀形态
  -> 将动态内容后置
  -> 记录 prefix hash 与 usage
  -> 用观测数据解释缓存命中变化
```

也就是说，缓存命中不是一个额外参数问题，而是 **provider request 构造质量问题**。

`prompt_cache_key` / `prefix_caching.cache_key` 只作为未来 provider-specific adapter 的可选扩展，不进入默认路径，也不作为 DeepSeek / OpenAI-compatible provider 的基础依赖。

---

## 2. 当前问题

> 本节结论来自对现有代码（`agent/loop`、`agent/context`、`agent/execution`）的实测，而非推测。它更正了本方案早期版本的一个错误假设——早期版本以为"工具动态裁剪"是缓存杀手，实测表明**并非如此**。

### 2.1 根因：双轨拼装 + 动态锚点前置

最终发给 LLM 的 request，目前由**两条独立的装配链**拼出来，彼此不知道对方的排布：

- **链 A：`context_manager.build_messages`** —— 产出 `system_prompt`、`user_instructions`、`summary`、`runtime_anchor`、`working_state`、投影后的历史。
- **链 B：`loop_kernel.run_turn` 的二次 splice** —— 在 `messages[0]` 之后、`messages[1:]` 之前，又插入 `runtime_control.system_prompt()`（当前 turn 目标、逐字 user 请求、失败记录、repair policy）和 `active_tool_prompt`（工具名清单）。

```409:415:python-backend/opengis_backend/agent/loop/loop_kernel.py
messages = [
    messages[0],
    *({"role": "system", "content": content} for content in system_inserts if content),
    *messages[1:],
]
```

于是最终 system 段的物理顺序变成：

| 位置 | 内容 | 来源 | 每轮是否变 |
|---|---|---|---|
| 0 | `system_prompt` | 链 A | ✅ 静态 |
| 1 | `runtime_control.system_prompt()`（turn 目标 / 逐字请求 / 失败记录 / repair） | **链 B splice** | ❌ **每轮变** |
| 2 | `active_tool_prompt`（工具名清单） | 链 B splice | 🟡 profile 内稳定 |
| 3 | user_instructions | 链 A | ✅ |
| 4 | summary | 链 A | 🟡 仅压缩时变 |
| 5 | `runtime_anchor`（active_operation / layer / 最近失败） | 链 A | ❌ **每轮变** |
| 6 | `working_state` | 链 A | ❌ **每轮变** |
| 7+ | recent_user_anchor / digest / 历史 | 链 A | 随窗口变 |

**结论（高置信度）**：大量**每轮变化的动态系统消息，被物理地插在 `system_prompt` 之后、对话历史之前**。按线性前缀缓存，缓存前缀基本在**位置 1 就断了**——真正可缓存的只有那一条 `system_prompt`，其后的工具 schema、历史全部 miss。这精确解释了长期 20%–30% 的命中率。

**这不是靠"调 section 顺序"能补的洞**：只要还有两条链各自拼装、且动态锚点排在历史之前，任何局部微调都会被另一条链的排布重新打乱。所以正确的解法是**自上而下**：用唯一的请求布局契约 + 唯一装配者收口，而不是逐点挪位置。

### 2.2 更正：工具暴露当前不是瓶颈

早期版本假设"每轮动态裁工具"破坏了缓存。实测 `tool_materializer.py` 表明相反：

```1:7:python-backend/opengis_backend/agent/execution/tool_materializer.py
Tool availability is an agent/profile contract, not a per-turn heuristic.
...Token pressure is handled by context projection and observation compression,
not by making tools disappear mid-run.
```

- `ToolMaterializer.materialize()` 暴露的是**整个 profile 的去重全集**（`reason="profile"`），并非每轮子集——即 `tool_schema_hash` 目前已在 profile 内稳定。
- 仅有的动态点是 `force_all`（可见性混淆重试，瞬时）与 `disable_tools`（终止轮 schemas=[]），都是边缘/终止情形。
- skill 通过 `load_skill` 工具的**结果**注入对话，受 `_PRUNE_PROTECTED_TOOLS` 保护，**不改变工具 schema 前缀**——已很接近"能力清单 + expansion"的理想形态。

因此第 3 节的目标架构里，**工具前缀策略（3.7）从"当前必救的头号问题"降级为"需要写进契约、长期守住的不变量"**。当前真正的头号问题是 2.1 的双轨拼装与锚点前置。

### 2.3 其余次生问题

- 上下文裁剪以历史消息为中心，而不是以完整 provider request 为中心。
- provider usage 字段没有统一规范，前端观测存在误判空间。
- `build_tool_schemas` 的顺序依赖 `registered` 列表顺序，需确认跨进程重启是否 deterministic，否则跨会话缓存拿不到。

---

## 3. 目标架构

```text
Agent Loop
  ↓
ProviderRequestBuilder
  ↓
StablePrefixComposer
  ↓
ContextProjector / RequestCompactor
  ↓
ProviderProtocolAdapter
  ↓
LLM Provider
  ↓
UsageNormalizer
  ↓
Cache Observatory
```

### 3.1 ProviderRequestBuilder —— 唯一装配者 + 唯一布局契约

这是整个升级的**中心**。它同时解决 2.1 的两件事：**消灭双轨拼装**、**用一个固定布局把动态内容结构性地压到最后**。

#### 3.1.1 唯一装配者（Single Assembler）

> **硬性约束：整个系统里，只有 ProviderRequestBuilder 能决定 request 的最终排布。**

- `loop_kernel.run_turn` **不得再** splice `runtime_control` / `active_tool_prompt`。
- `context_manager.build_messages` **不得再**自行决定 `runtime_anchor` / `working_state` 的插入位置。
- 这些内容改为以**带类型标签的语义片段（Section）**交给 Builder，由 Builder 按下面唯一的布局契约排布。

也就是说，链 A、链 B 都退化为"**产出片段**"，不再"**决定顺序**"。顺序只有一个地方定义。这样任何一处改动都不可能再从物理上打断前缀——因为没有第二个地方能插入内容。

#### 3.1.2 唯一布局契约（Canonical Request Layout）

Builder 产出的 request 物理顺序**恒定如下**，每个片段都带稳定性等级：

```text
┌── STABLE PREFIX（可缓存，只增不改，append-only）──────────────┐
│ [S0] system core prompt          静态                          │
│ [S1] capability manifest         静态（平台能力清单，自然语言） │
│ [S2] tool protocol / project rules  静态                       │
│ [S3] tools schema (core + frozen packs)  会话内冻结、单调扩展   │
│ [S4] stable memory projection    稳定排序、稳定格式             │
│ ── conversation history ──       append-only（只在尾部追加）   │
└───────────────────────────────────────────────────────────────┘
┌── DYNAMIC TAIL（每轮重算，永不进缓存前缀，排在历史之后）──────┐
│ [D0] 当前 turn objective / 逐字 user 请求                      │
│ [D1] runtime anchor（active_operation / layer / 最近失败）     │
│ [D2] working state（map / worker / operation 瞬时态）          │
│ [D3] 最近关键 tool results / code outputs                      │
│ [D4] loop anomaly / deviation feedback / repair policy         │
└───────────────────────────────────────────────────────────────┘
```

**关键转变**：现在被前置在 position 1 的 `runtime_control` / `runtime_anchor` / `working_state`，在契约里全部属于 **DYNAMIC TAIL**，物理上排到**对话历史之后**。这不是"把某几条消息往后挪"的补丁，而是布局契约的直接结果——只要片段被打上 `dynamic` 标签，它就永远出现在历史之后。

这一步同时带来两个收益，且互不冲突：

- **缓存**：STABLE PREFIX + append-only 历史构成一个逐轮只增长、不改写的前缀，缓存断点从 position 1 推到了历史末尾。
- **指令遵循**：turn 目标、失败、runtime 状态本就该靠近"模型要作答的位置"，后置后 recency 更好。

#### 3.1.3 结构化产物

Builder 输出一个结构化对象，而不是散落的 `messages + tools + kwargs`：

```python
ProviderRequest(
    model=...,
    provider=...,
    session_id=...,
    stable_sections=[...],   # S0..S4，带类型标签，Builder 保证顺序与 deterministic 序列化
    history=[...],           # append-only
    dynamic_tail=[...],      # D0..D4，永不进前缀
    tools=[...],
    metadata={
        "stable_prefix_hash": "...",
        "tool_schema_hash": "...",        # = hash(tool_core_hash + tool_pack_set_hash)
        "tool_core_hash": "...",          # Stable Core Tools，正常运行应恒定
        "tool_pack_set_hash": "...",      # 已加载 Domain Tool Packs 的集合
        "system_prefix_hash": "...",
        "memory_projection_hash": "...",
        "dynamic_suffix_hash": "...",
    },
)
```

ProviderRequestBuilder 是 loop 和 provider 之间的硬边界。loop 不再直接关心 provider wire format，provider adapter 不再反向理解 agent 业务语义。**所有片段是 stable 还是 dynamic，由片段自己的类型标签声明，Builder 只按契约排布，不做业务判断。**

### 3.2 StablePrefixComposer

职责：把 Builder 收到的 `stable_sections` 组织成 deterministic 的稳定前缀（对应 3.1.2 的 `[S0]..[S4]`）。它只处理 STABLE PREFIX，DYNAMIC TAIL 不经过它。

稳定前缀顺序（与 3.1.2 契约一致）：

```text
S0. system core
S1. capability manifest
S2. tool protocol / project rules
S3. tools schema (core + frozen packs)
S4. stable memory projection
── conversation history（append-only，紧随其后）──
```

原则：

- 前缀内不出现当前时间、run id、随机采样、实时 token 统计。
- tools 固定排序，schema 序列化必须 deterministic。
- memory 检索结果必须排序稳定，且限制数量和格式。
- 一切每轮变化的内容一律进 DYNAMIC TAIL（排在历史之后），不得进前缀。

> **地基性约束（务必先理解）：Prompt Cache 是线性前缀缓存，前缀一旦某处变化，其后全部 miss。**
>
> 由此可推出两条必须同时守住的不变量：
>
> 1. **动态内容必须排在对话历史之后**（DYNAMIC TAIL）。这是当前命中率低的直接原因——`runtime_control` / `runtime_anchor` / `working_state` 现在排在历史之前（见 2.1），把前缀在 position 1 就打断了。
> 2. **tools 永远排在整段对话历史之前**：工具集会话中途任何变动（增删、改序、改 schema），会连带其后**整段 messages 历史一起 miss**。因此工具前缀稳定保护的不是那几个工具 schema 的 token，而是**排在它们后面的全部对话历史缓存**。工具的稳定化策略见 [3.7 工具前缀稳定策略](#37-工具前缀稳定策略tool-prefix-stability-policy)。

### 3.3 RequestCompactor

职责：以完整 provider request 为单位裁剪，而不是只裁剪历史消息。

裁剪顺序：

```text
保留稳定前缀
  -> 保留当前任务目标
  -> 保留最近关键 tool result
  -> 保留最近有效消息
  -> 注入历史摘要
  -> 丢弃低价值噪声
```

这比"历史消息太长就删旧消息"更可靠，因为 token 开销最大的不一定是聊天历史，也可能是 tools schema、memory projection、重复 tool outputs。

### 3.4 ProviderProtocolAdapter

职责：只做协议降级。

默认行为：

- 不传 `prompt_cache_key`
- 不传 `prefix_caching.cache_key`
- OpenAI-compatible provider 依赖隐式前缀缓存
- DeepSeek-compatible provider 同样依赖前缀文本稳定命中

可选行为：

- Anthropic / Bedrock 可支持 explicit cache marker
- 只有 provider capability 明确支持时才开启
- provider-specific 参数必须可观测、可关闭、可回退

### 3.5 UsageNormalizer

职责：把不同 provider 的 usage 字段统一成一个后端标准结构。

```python
NormalizedUsage(
    input_tokens=...,
    non_cached_input_tokens=...,
    cache_read_input_tokens=...,
    cache_write_input_tokens=...,
    output_tokens=...,
    reasoning_tokens=...,
    total_tokens=...,
    cache_hit_ratio=...,
    provider_raw=...,
)
```

如果 provider 不返回 cache 字段，应该明确标记为：

```text
cache_status = "provider_not_reported"
```

而不是在前端显示空白或 0，避免误判。

### 3.6 Cache Observatory

职责：解释缓存为什么命中或没命中。

每一轮记录：

- `stable_prefix_hash`
- `tool_schema_hash`
- `tool_core_hash`（Stable Core Tools，正常运行应恒定）
- `tool_pack_set_hash`（已加载 Domain Tool Packs 集合，仅应在显式 expansion 时变化）
- `system_prefix_hash`
- `memory_projection_hash`
- `dynamic_suffix_hash`
- input / output / cache read / cache write tokens
- cache hit ratio
- tools count
- request section token estimate

工具 hash **必须分段记录**（`tool_core_hash` / `tool_pack_set_hash`），这样才能把"工具导致的 miss"精确归因：

- `tool_core_hash` 变化 → **异常**，core 工具本应恒定，视为 bug。
- `tool_pack_set_hash` 变化 → **预期内**，是一次显式的 pack expansion（应低频、单调）。

Settings 中展示的不应只是总 token，而应该能看到每轮波形和 section 变化。真正有用的问题是：

```text
这轮 cache hit 低，是 provider 没返回，还是前缀 hash 变了？
如果前缀 hash 变了，是 tools、system、memory 哪一段变了？
如果是 tools 变了，是 core（不该变）还是 pack expansion（预期内）？
```

### 3.7 工具前缀稳定策略（Tool Prefix Stability Policy）

这是本方案里唯一一个**目标态自身存在张力**、必须显式定策的点：

- **动态工具暴露**想要的是：每轮只给 agent 当前最相关的工具，降低工具数量、降低 token、减少误调用。
- **静态缓存前缀**想要的是：tools schema 越长期不变越好，缓存越容易命中。

两者天然拉扯，但**不能**简单归结为"动态工具错了"或"全部工具固定最好"。结合 [3.2 的地基性约束](#32-stableprefixcomposer)（tools 排在 messages 之前，任何变动连带整段历史 miss），正确的表述是：

> **关键不是"动态还是静态"，而是"动态边界是否稳定"。**

要同时拿到"能力灵活"和"缓存命中"，必须把工具选择拆成**两个正交的轴**分别约束：

| 轴 | 含义 | 缓存友好的选择 |
|----|------|----------------|
| **空间轴（分层）** | 工具如何组织 | 分层，稳定的在前、易变的在后 |
| **时间轴（稳定性）** | 工具多久变一次 | 每会话冻结 + 单调扩展 |

#### 3.7.1 空间轴：三层工具

```text
Stable Core Tools（稳定核心工具）
  每轮固定暴露，进入稳定缓存前缀，正常运行永不变化。
  例如：read_file / edit_file / list_layers / get_layer / execute_code / get_map_state

Domain Tool Packs（领域工具包）
  按任务域成"包"加载，而不是散装临时拼接。
  pack 内工具顺序与 schema 必须 deterministic（固定排序、稳定序列化）。
  例如：map / raster / datasource / style / worker / operation / workflow

On-demand Tools（按需工具）
  默认不进首轮 request。
  只有 agent 明确需要、或 router 判定后，通过显式 expansion 加载。
  例如：websearch / osm / qgis / heavy analysis / rare export
```

#### 3.7.2 时间轴：每会话冻结 + 单调扩展

仅有分层**不足以**拿到缓存收益——因为 tools 排在 messages 之前，pack 只要在会话中途变动，其后整段历史照样 miss。所以必须叠加两条时间约束：

1. **每会话冻结（freeze per session）**：会话开始时根据意图选定 pack 集合，之后原则上不再随每轮变化。绝大多数缓存损失来自 pack 在会话内逐轮抖动。
2. **单调扩展（monotonic growth）**：会话内工具集**只增不减**。一次 expansion 只允许**加**新 pack，且加入后本会话不再移除。
   - 每次 expansion 会断一次前缀（低频、可接受）；
   - 若之后再收回工具，会**第二次断前缀且无法回到扩展前的缓存**，造成抖动 —— 严禁。

反例（会直接杀死缓存的"散装动态"）：

```text
每轮临时挑 18 个工具 → 顺序变化 → 描述变化 → 参数 schema 变化 → 下一轮又换一批
```

#### 3.7.3 能力可见性：平台能力 ≠ 当前暴露工具

动态工具选择**绝不能**把"平台能力"伪装成"没有能力"。必须区分并同时表达两件事：

1. **平台拥有哪些能力** —— 用自然语言写一份 **capability manifest（能力清单）常驻稳定 system 前缀**，告诉 agent "OpenGIS 具备 raster / style / worker / operation 等能力"。它是文本、永不变、随缓存白嫖，且让 agent 永远知道平台能干什么。
2. **当前请求实际暴露了哪些工具** —— 若当前未暴露，必须提供**稳定的 expansion 入口**：一个常驻 core、永不变化的 meta-tool（如 `request_tool_pack` / `load_capability`）。

于是"第一轮没有 `update_layer_style`、第二轮才有，agent 自己解释'刚才没这个能力'"被根治：能力永远在 manifest 里可见，工具按需长出来，agent 不会谎称自己残废。

#### 3.7.4 验收标准

- 同一会话内工具集**只增不减**；连续 turn 的 `tool_pack_set_hash` 稳定，仅在显式 expansion 时变化一次。
- `tool_core_hash` 在正常运行中**恒定不变**（一旦变化即视为 bug）。
- 缺工具时 agent 触发 expansion，而**不是**回答"没有该能力"或编造替代方案。
- Cache Observatory 能明确回答：本轮工具导致的 miss 是 `core`（不该发生）还是 `pack expansion`（预期内）。

---

## 4. 和 opencode 的对应关系

opencode 的核心经验不是"所有 provider 都传 cache key"，而是：

- cache schema 与 provider wire format 解耦
- policy 层决定缓存意图
- protocol 层只在 provider 支持时降级
- OpenAI-compatible provider 依赖隐式前缀缓存
- Anthropic / Bedrock 才需要 explicit cache marker

OpenGIS 应吸收的是这个分层，而不是把某个 provider 参数硬塞进所有模型请求。

---

## 5. 分阶段实施

> 排序原则：**先立骨架，再填血肉**。Phase 1 一次性把"唯一装配者 + 布局契约"落地——它本身就结构性地根治了 2.1 的双轨与锚点前置（当前命中率的根因），而不是靠后续阶段一点点挪位置。工具 pack 化因当前工具已稳定（见 2.2），从早期版本的 P0 降级为"守成不变量"，靠后安排。

### Phase 1：唯一装配者 + 布局契约（根因治理，最高优先）

这是唯一必须先做、且改动收益最大的一步。

- 新增 `ProviderRequest` 结构（`stable_sections` / `history` / `dynamic_tail` / `tools` / `metadata`）。
- 新增 `ProviderRequestBuilder` 作为**唯一装配者**：所有 LLM 请求统一经它产出。
- **移除 `loop_kernel.run_turn` 的二次 splice**（不再插 `runtime_control` / `active_tool_prompt`）。
- **`context_manager.build_messages` 不再决定 `runtime_anchor` / `working_state` 的位置**，改为产出带类型标签的 Section 交给 Builder。
- Builder 按 3.1.2 契约排布：`runtime_control` / `runtime_anchor` / `working_state` / turn objective / deviation feedback **全部归入 DYNAMIC TAIL，物理排到对话历史之后**。
- `llm.py` 只接收已构建好的请求，不再承担 section 组织职责。

验收：

- 系统内**不存在第二处**能决定 request 排布的代码。
- 连续普通对话中，`system_prefix_hash` 与 `stable_prefix_hash` 稳定不变，每轮只有 `dynamic_suffix_hash` 变化。
- agent loop 的行为语义不变（只是内容位置变了，不是逻辑变了）。

### Phase 2：分段 Hash + Cache Observatory（验证 Phase 1）

紧跟 Phase 1，用来**证明**根因确实被治好，而不是凭感觉。

- 后端统一 usage（UsageNormalizer），落地 `system_prefix_hash` / `tool_*_hash` / `memory_projection_hash` / `dynamic_suffix_hash`。
- 前端 Settings 展示 normalized usage 与每轮 section 波形。
- cache hit 为空时明确区分"provider 未返回"与"确实没命中"。

验收：

- 能直接读出 Phase 1 前后 `stable_prefix_hash` 的稳定性差异与命中率变化。
- 任一轮 miss 都能归因到具体 section（system / tools / memory / dynamic）。

### Phase 3：Prompt Section 化与 memory 稳定投影

- 把 system core 内的残余动态内容（run id、当前时间、临时状态）清出前缀。
- capability manifest 写入稳定 `[S1]`。
- memory projection 使用稳定排序和稳定格式。

验收：

- `system_prefix_hash`、`memory_projection_hash` 在无实质变化时恒定。

### Phase 4：Request-Aware Compaction

- 裁剪逻辑改成面向完整 provider request，而非只裁历史。
- 历史消息、tool outputs、memory projection 分别有预算。
- 优先保留当前任务目标与最近关键证据。

验收：

- 长 loop 不再因历史膨胀导致 request 爆炸。
- 裁剪不破坏 STABLE PREFIX（只动 tail 与历史中段）。

### Phase 5：Tool Pack 化与稳定暴露（守成不变量）

当前工具已是稳定全量（见 2.2），本阶段目的**不是救火，而是把"不退化"写进契约**，防止未来有人重新引入每轮动态裁剪。

- 定义 Stable Core Tools 全集并固定排序；确认 `build_tool_schemas` 顺序跨进程 deterministic。
- 把工具按域归入 Domain Tool Packs，pack 内 schema deterministic。
- 新增常驻 core 的 `request_tool_pack` meta-tool；能力清单已在 Phase 3 进前缀。
- 若未来引入 pack 动态加载，必须遵守"每会话冻结 + 单调扩展"。
- 去掉/后置冗余的 `active_tool_prompt`（工具名清单与 schema 重复且位置靠前）。

验收：

- 同一会话内工具集只增不减；`tool_pack_set_hash` 仅在显式 expansion 时变化一次。
- `tool_core_hash` 在正常运行中恒定不变（变化即视为 bug）。
- agent 缺工具时触发 expansion，而不是回答"没有该能力"。

### Phase 6：Provider-Specific Explicit Cache，可选

- Anthropic / Bedrock 支持 inline cache markers。
- OpenAI-compatible / DeepSeek-compatible 默认不传 cache key。
- 所有 provider-specific 行为必须由 capability gate 控制。

验收：

- 不支持 explicit cache 的 provider 不会收到未知参数。
- 支持 explicit cache 的 provider 可独立开启、关闭、回退。

---

## 6. 预期收益

在不依赖 cache key 的情况下，收益主要来自稳定前缀。

> 下表为**目标区间**而非承诺，实际数值以 Cache Observatory 的观测为准。

| 阶段 | 目标命中率 |
|------|------------|
| 当前（双轨拼装 + 动态锚点前置，前缀在 position 1 断） | 20%-30% |
| **Phase 1：唯一装配者 + 布局契约（动态尾部后置）** | **40%-60%** |
| Phase 2-3：分段观测 + system/memory 稳定前缀 | 50%-65% |
| Phase 4：request-aware compaction 完成 | 55%-70% |
| Phase 6：Anthropic/Bedrock explicit marker | 视 provider，可能更高 |

这个提升不是靠"强制缓存"，而是靠让 provider 看到尽可能长、尽可能不变的 prefix。**杠杆最高的一步是 Phase 1**——把动态锚点从历史之前挪到历史之后，缓存断点从 position 1 一次性推到历史末尾，这是当前命中率低的根因治理。工具包稳定化（Phase 5）在 OpenGIS 当前是守成而非增益，因为工具已稳定。

---

## 7. 非目标

以下内容不进入默认方案：

- 默认向所有 provider 传 `prompt_cache_key`
- 默认向 OpenAI-compatible provider 传 `prefix_caching.cache_key`
- 为了缓存命中牺牲 tool correctness
- 为了减少 token 硬隐藏用户明确需要的工具
- 为了缓存命中让 agent 谎称平台"没有某能力"（能力清单必须常驻可见，缺工具走 expansion）
- 会话内对工具集做非单调的增删抖动
- 用 prompt 规则替代 request 架构边界

---

## 8. 最终原则

OpenGIS 的缓存优化应该满足五句话：

1. **request 的排布只能有一个装配者（ProviderRequestBuilder），动态内容一律排在对话历史之后。** 这是根因治理，也是其余一切的地基。
2. **前缀稳定优先于缓存参数。**
3. **请求构造必须可解释、可观测、可回放。**
4. **provider-specific 能力只能在 adapter 层显式开启，不能污染 agent loop。**
5. **工具暴露必须 pack 化、每会话冻结、单调扩展；平台能力永远对 agent 可见，缺工具走 expansion 而非谎称无能力。**

---

## 9. 实现现状（已落地代码）

> 本节记录**已经写进代码**的改动（区别于上文的方案设计）。对照 Phase 编号，标注实际落点、文件与关键不变量。已落地范围为 **Phase 1 全部 + Phase 2/3 主体**；Phase 4/5/6 尚未开始。

### 9.1 Phase 1：唯一装配者 + 布局契约 ✅ 已落地

- **`ContextManager.build_provider_request()`（`context/context_manager.py`）成为唯一装配者。** 它按 3.1.2 契约排布：
  `[S0] system core → [S1] stable system sections（能力清单 / active tools） → [S2] user preferences → [S3] conversation summary →` 对话历史（append-only）`→ [D*] dynamic tail（runtime control / runtime anchor / working state）`。
  - 每个片段通过 `builder.add_system_text(..., stability=..., cache_policy=...)` 打类型标签，`stable_*` 走 `cacheable`，`turn_dynamic` 走 `none`。
  - 旧 `build_messages()` 保留为薄包装（`return self.build_provider_request(...).messages`），供 debug 投影工具复用，**保证只有一处决定排布**。
- **`loop_kernel.run_turn` 移除了二次 splice。** 原先在 `messages[0]` 之后插 `runtime_control` / `active_tool_prompt` 的 `messages = [messages[0], *inserts, *messages[1:]]` 代码块已删除；改为把 insert 拆成两类交给装配者：
  - `active_tool_prompt` → `stable_system_sections`（profile 内稳定，进前缀）；
  - `request.extra_system_messages`（turn 目标 / final-turn / deviation feedback）→ `dynamic_tail_sections`（进历史之后的尾部）。
  - 预算重算分支也统一走内部 `_assemble(limits)`，不再各自拼装；一次性删除了旧的观测用 `_provider_request_from_messages()`。
- **project memory 归入 DYNAMIC TAIL。** `factory_common.compose_system_prompt` 不再把「Retrieved Project Memory」拼进 system prompt；新增 `project_run_memory(ctx)` 单独投影，经 `LoopRuntimeBundle.project_memory` 传给 `AgentLoop` / `WorkflowLoop`，由 loop 注入 tail（`agent_loop.py` / `workflow_loop.py` / `agent_factory.py` / `workflow_factory.py`）。原因：memory 按当前 user message 检索、每 run 变化，留在前缀顶部会直接打断缓存。

### 9.2 Phase 2：分段 Hash + Cache Observatory ✅ 主体已落地

- **后端分段 hash。** `provider_request.py` 新增 `ProviderRequest.system_prefix_hash`（历史之前所有 section 的 digest，健康时恒定）与 `dynamic_suffix_hash`（最后一个 history section 之后的尾部）。`loop_kernel` 把 `system_prefix_hash` / `dynamic_suffix_hash` / `tool_schema_hash` 一并写入 run archive 的 telemetry。
- **usage 归一化。** `llm.py:_extract_usage` 补齐 DeepSeek 口径（`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`），并派生 `non_cached_input_tokens`、`cache_status`（`reported` / `provider_not_reported`）、`cache_hit_ratio`——provider 不报缓存时显式标注，避免前端误显示 0% 命中。
- **前端可观测。** `SettingsView.tsx` 的 `collectPromptCacheMetrics` / `summarizePromptCacheUsage` 采集分段 hash，并按「同一 loop 内出现 >1 个不同 `system_prefix_hash` 即判定前缀被打断」渲染「前缀稳定/被打断」「工具恒定/变动」徽标；i18n 文案见 `src/i18n/{en,zh}.ts`（`promptCacheSegmentHash` 等）。

### 9.3 Phase 3：能力清单 + memory 稳定投影 ✅ 主体已落地

- **能力清单进稳定前缀。** `tool_capabilities.py` 新增 `_DOMAIN_MANIFEST_LABELS` 与 `format_capability_manifest()`：按 `ToolCapability.domain` 聚合、**domain 排序后输出**，字节稳定，随缓存前缀白嫖。`compose_system_prompt` 将其拼入 system prompt，让模型永远知道平台具备哪些能力域，不再因当前未暴露某函数而谎称「不支持」。
- **memory 检索去抖（关键稳定性修复）。** `memory_store.py`：
  - `_score()` **移除 wall-clock 项**——原先 `recency = ...(time.time())...` 使浮点分值每次调用都漂移、进而打乱排序、破坏前缀稳定；recency 改由稳定的 `created_at` tie-breaker 表达。
  - `search()` 排序改为确定性三元组 `(-score, -created_at, id)`，并新增 `touch: bool = True` 参数。
  - `context_projector.py` / `failure_memory.py` 的投影路径改传 `touch=False`：把 memory 投进 prompt **不得**改写 `last_used_at`，否则下一次检索重排、注入文本逐轮漂移。

### 9.4 尚未开始

- **Phase 4（Request-Aware Compaction）**：裁剪仍以历史为中心，尚未面向完整 request 分预算（详见 `docs/message-compaction-architecture.md`）。
- **Phase 5（Tool Pack 化）**：当前工具已是 profile 内稳定全量（`tool_materializer`），pack 化 / `request_tool_pack` meta-tool / 单调扩展契约尚未实现，暂作守成不变量。
- **Phase 6（Provider explicit cache marker）**：未开始。

### 9.5 本次改动文件一览

| 层 | 文件 | 改动要点 |
|---|---|---|
| 装配 | `agent/context/context_manager.py` | 新增 `build_provider_request()` 唯一装配者；`build_messages()` 降为薄包装 |
| 装配 | `agent/loop/loop_kernel.py` | 移除二次 splice 与 `_provider_request_from_messages`；stable/dynamic 分流；补分段 hash 观测 |
| 契约 | `agent/context/provider_request.py` | 新增 `system_prefix_hash` / `dynamic_suffix_hash`；扩展 `PromptSectionKind` |
| 能力 | `agent/execution/tool_capabilities.py` | 新增能力清单 `format_capability_manifest` 与域标签表 |
| 前缀 | `agent/factory_common.py` | 能力清单进 system prompt；memory 拆出为 `project_run_memory` 进 tail |
| memory | `workspace/memory_store.py` | 去 wall-clock 评分；确定性排序；`touch=False` |
| memory | `agent/context/context_projector.py`、`agent/context/failure_memory.py` | 投影检索传 `touch=False` |
| 装配管线 | `agent/agent_factory.py`、`agent/workflow/workflow_factory.py`、`agent/loop/agent_loop.py`、`agent/loop/workflow_loop.py` | 透传并注入 `project_memory` 至 DYNAMIC TAIL |
| usage | `agent/llm.py` | DeepSeek 缓存口径 + `cache_status` / `cache_hit_ratio` 归一化 |
| 前端 | `src/features/settings/SettingsView.tsx`、`src/i18n/{en,zh}.ts` | 分段 hash 徽标与文案 |
