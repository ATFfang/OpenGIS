# OpenGIS 后端：消息压缩 / 上下文裁剪机制

> 适用范围：`python-backend/opengis_backend` 的 agent loop 上下文管理。
> 本文详述当前的四层裁剪机制、触发条件、预算判定，以及与 prompt-cache 稳定前缀架构的边界关系。
> 相关文档：`docs/prompt-cache-stable-prefix-architecture.md`。

---

## 0. 一句话概括

系统有**四层独立的裁剪机制**，作用点各不相同。核心原则是：

> **原始对话历史（`ContextManager.messages`）永久保存、只增不改（append-only）；裁剪只发生在"投影给模型可见"的那一层，或把老的 tool 结果就地替换为骨架占位符。**

| 层 | 名称 | 作用对象 | 是否修改原始 `messages` | 触发点 |
|----|------|---------|:---:|--------|
| **L0** | 工具输出定界 `ToolOutputRuntime.bound` | 单条工具输出（入库前） | 否（写入前就限） | 每次工具执行 |
| **L1** | 摘要压缩 `compress()` + sliding window | 老的 live 历史 → LLM 摘要 | **是**（推进 `_summary_cutoff`） | `should_compress()` 命中 |
| **L2** | 工具结果剪枝 `_prune_outputs()` | 老的 tool_result 消息体 | **是**（就地替换为骨架） | 预算 hot/overflow，或每次工具落定后 |
| **L3** | 提供方投影 `ProviderContextProjector` | 组装发给模型的临时消息 | 否（只影响本次请求） | 每次组装 provider request |

预算判定统一由 `RequestBudgetManager` 负责，产出 `pressure` 与 `BudgetLimits`。

---

## 1. 涉及的核心文件

| 文件 | 职责 |
|------|------|
| `agent/context/context_manager.py` | 对话历史存储、`should_compress` / `compress` / `_prune_outputs`、装配 provider request |
| `agent/context/request_budget.py` | `RequestBudgetManager` / `BudgetLimits` / `RequestBudgetReport`，预算估算与压力分级 |
| `agent/context/provider_projector.py` | `ProviderContextProjector`，把原始历史投影成一份更小的、发给模型的临时消息 |
| `agent/context/summarizer.py` | `llm_summarize` / `simple_summarize`，L1 的摘要实现 |
| `agent/execution/tool_output.py` | `ToolOutputRuntime`，L0 工具输出入库前定界，全量落盘保留 |
| `agent/loop/loop_kernel.py` | `run_turn`，触发 L1/L2 并按预算重建请求的主入口 |

---

## 2. 触发与入口

### 2.1 主入口：`LoopKernel.run_turn`

`loop_kernel.py:126-138` —— 每轮开始，先做 L1 压缩判定：

```python
def run_turn(self, request: LoopTurnRequest) -> LoopTurnOutcome:
    if request.compress_context:
        should_compress, reason = self.context.should_compress()
        if should_compress:
            logger.info("Compression triggered (pre-call): %s", reason)
            self.context.compress(self.llm_call)          # L1 摘要压缩

    budget_manager = RequestBudgetManager(
        input_token_budget=getattr(self.context, "token_budget", 100_000),
    )
    budget_limits = budget_manager.suggest_limits(
        live_tokens=self.context.estimate_live_tokens(),
    )
```

组装请求后，若预算压力为 `hot`/`overflow`，触发 L2 剪枝并用更紧的 limits 重建（`loop_kernel.py:194-210`）：

```python
budget_report = self._analyze_request_budget(messages, active_tool_schemas)
if (
    request.compress_context
    and budget_report.pressure in {"hot", "overflow"}
    and self.context.prune_tool_results() > 0            # L2 剪枝
):
    budget_limits = budget_manager.suggest_limits(
        pressure=budget_report.pressure,
        live_tokens=self.context.estimate_live_tokens(),
    )
    provider_request, messages = _assemble(budget_limits) # 用更紧 limits 重建 L3
    budget_report = self._analyze_request_budget(messages, active_tool_schemas)
```

### 2.2 触发条件：`ContextManager.should_compress`

`context_manager.py:450-472` —— **触发依据是 token 预算，不是消息条数/轮次**：

```python
def should_compress(self) -> tuple[bool, str]:
    live = self.messages[self._summary_cutoff:]
    estimated = estimate_messages_tokens(live) + self._system_prompt_tokens
    threshold = int(self.token_budget * self.compress_threshold)   # 100_000 * 0.80 = 80_000
    if estimated > threshold:
        return True, "Token count ... exceeded threshold"
    # 工具结果占比过高也触发
    tool_result_tokens = sum(
        estimate_tokens(m.get("content", "")) for m in live if self._is_tool_result(m)
    )
    if tool_result_tokens > threshold * 0.6:                       # 48_000
        return True, "Tool results dominating context"
    return False, ""
```

阈值来自 `ContextManager` dataclass 字段：

| 字段 | 默认值 | 含义 |
|------|:---:|------|
| `token_budget` | `100_000` | 总上下文预算（估算 token） |
| `compress_threshold` | `0.80` | 超过预算此比例即触发压缩 → 硬阈值 **80_000** |
| `keep_recent` | `8` | 永不被压缩的最近 N 条 |
| `safe_buffer_tokens` | `40_000` | L2 剪枝的安全缓冲，低于此值跳过剪枝 |
| `max_single_result_tokens` | `6_000` | 单条 tool 结果硬上限（即使在保护窗口内） |
| `max_output_chars` | `3_000` | `add_code_output` 单条输出入库截断 |

工具结果专项阈值 = `80_000 * 0.6 = 48_000` tokens。`_system_prompt_tokens` 在 `build_provider_request` 里由 `estimate_tokens(system_prompt)` 缓存（`context_manager.py:326`）。

### 2.3 其它触发点

- **每次工具落定后**：`turn_runner` 会调用 `self.context.prune_tool_results()`（无条件调用，但内部有安全缓冲判定，见 §3.3）。
- **Workflow 每步后主动压缩**：`workflow_loop.py` 里每步结束调用 `should_compress()`，命中则 `compress()`。

### 2.4 配置来源

`ContextManager()` 在所有构造点（`agent_factory.py`、`workflow_factory.py`、`subagent_tool.py`）均**无参数**，因此运行时恒为默认值：`token_budget=100_000`、`compress_threshold=0.80`、`keep_recent=8`。配置字段**不持久化**（`to_dict` 只存 `messages / summary / summary_cutoff / recently_edited_files`，`context_manager.py:131-144`）。

---

## 3. 四层裁剪策略详解

### 3.1 L0 — 工具输出入库前定界（`ToolOutputRuntime.bound`）

文件：`agent/execution/tool_output.py`

在工具结果写入历史**之前**就做定界，避免超大 stdout / JSON / 表格预览污染上下文。原则：**模型只看到有界预览，完整输出落盘保留**。

- 阈值：`DEFAULT_MAX_OUTPUT_LINES = 2000` 行、`DEFAULT_MAX_OUTPUT_BYTES = 50 * 1024`（50KB）。
- 未超限 → 原样返回。
- 超限 → 完整内容写入 `<workspace>/.opengis/tool-output/<tool>-<uuid>.txt`（`_write_full_output`），给模型的是 `compress_observation` 生成的压缩预览 + 一条 `[OpenGIS: tool output truncated ...]` 提示，提示里带原始字节/行数、预览字节/行数、以及落盘完整输出的路径。
- 结果封装为 `BoundedToolOutput(content, truncated, metadata)`，`metadata` 记录 `retained_output_path` 等，供后续剪枝时保留引用。

此外 `ContextManager.add_code_output`（`context_manager.py:235-292`）在入库 `execute_code` 观测时，用 `truncate_output(output, max_output_chars=3000)` 再做一次单条截断，并在 `_meta` 里保留 `step / had_error / code_summary`，供 L2 生成骨架。

### 3.2 L1 — 摘要压缩 + sliding window（`compress`）

文件：`context_manager.py:474-557`

被 `should_compress()` 命中后触发，把老消息摘要成一段文字，同时保留最近 `keep_recent` 条原文：

```python
live = self.messages[self._summary_cutoff:]
if len(live) <= self.keep_recent:
    return                          # 全是"最近"消息，无可压缩
to_summarize = live[:-self.keep_recent]
self._prune_outputs()               # 顺带触发 L2
```

摘要生成策略：

- 有 `llm_call` → `llm_summarize`，把**上一版摘要**放进 `<previous-summary>` 块一起喂给模型做**锚定合并（anchored merge）**，新摘要已包含旧摘要，故**替换而非拼接**，防止跨多次压缩无限增长。
- LLM 摘要失败/为空 → 回退 `simple_summarize`（简单拼接）。此时新摘要**不含**旧摘要，故**追加**旧摘要（`self._summary + "\n\n---\n\n" + summary`）以免丢历史。

压缩完成后：

- 推进 `_summary_cutoff = len(self.messages) - self.keep_recent`——游标之前的消息不再进入 live 窗口（但原始 `messages` 数组仍完整保留）。
- **压缩后重读最近编辑的文件**（Gap #2）：`build_reread_message` 把最近编辑过的文件内容重新注入，避免摘要丢失关键文件状态。

摘要产物最终由 `build_provider_request` 作为 `context.summary` 段放进**稳定前缀**（`session_static / cacheable`，`context_manager.py:352-370`）。

### 3.3 L2 — 工具结果剪枝（`_prune_outputs` / `prune_tool_results`）

文件：`context_manager.py:750-857`

把**老的** tool_result 消息体就地替换为**骨架占位符**，保留因果链（步骤号、首行代码、成功/失败、产物引用），而非置为完全不透明的 "[content cleared]"。

关键规则：

- **保护最近 `keep_recent` 条**：只处理 `range(0, total - keep_recent)`。
- **安全缓冲跳过（Gap #3）**：`use_token_based_pruning=True` 时，若 live tokens ≤ `safe_buffer_tokens`（40_000）则直接返回 0，不剪枝。这解释了为什么"每次工具落定后无条件调用"不会过度剪裁——大多数时候被安全缓冲挡下。
- **保护特定工具（Gap #5）**：`_PRUNE_PROTECTED_TOOLS = {"load_skill"}`——skill 指令通过 tool 结果注入，剪掉会让后续轮次丢失操作指令。
- **幂等**：已被替换为占位符（`content` 以 `body removed to save tokens` 结尾）的消息会跳过，可安全反复调用。
- **骨架内容**（`_make_pruned_placeholder`，`context_manager.py:712-748`）：`[Step N pruned] (ok|error) — code: \`...\` — refs: artifact_layer_id=... — body removed to save tokens`，保留 `artifact_layer_id / artifact_path / script_path / retained_output_path` 等引用。
- **保护窗口内的单条硬上限（#6）**：即使在最近 `keep_recent` 内，单条 tool 结果超过 `max_single_result_tokens`（6_000）也会被头尾截断，防止一条巨型 dump 独占 live 窗口。

返回节省的 token 数；`loop_kernel` 据此判断是否需要重建请求。

### 3.4 L3 — 提供方投影（`ProviderContextProjector`）

文件：`context_manager.py:294-424`（装配）+ `agent/context/provider_projector.py`（投影实现）

这是**发给模型前**的最后一层，**不修改原始 `messages`**，只产出本次请求用的临时消息数组。原始历史留在磁盘/内存，投影只把"任务有用的较小版本"发给模型。

投影配置 `ProviderProjectionConfig`（`_provider_projection_config`，`context_manager.py:609-631`）按预算 `BudgetLimits` 动态收紧：

| 配置项 | 默认 | 含义 |
|------|:---:|------|
| `raw_recent` / `provider_raw_recent` | 8 | 最近 N 条保持逐字原文 |
| `collapse_old_messages` | True | 老消息折叠成摘要式 digest |
| `max_digest_chars` | 6000 | 折叠 digest 上限 |
| `max_tool_result_chars` | 4000 | 投影后单条 tool 结果上限 |
| `max_tool_call_arg_chars` | 1400 | tool_call 参数上限 |
| `max_execute_code_chars` | 900 | execute_code 投影上限 |
| `recent_user_turns` | 4 | 保留的最近 user 轮数 |

投影结果作为 `context.history` 段放进请求（`kind="history"`）——它是稳定前缀与动态尾部的分界线（见 §5）。

---

## 4. 预算判定：`RequestBudgetManager`

文件：`agent/context/request_budget.py`

### 4.1 压力分级

构造参数（`request_budget.py:89-103`）：`input_token_budget=100_000`、`output_reserve_tokens=4096`（输出预留），`usable_input_tokens = budget - reserve`。压力比例阈值：

| 压力 | 触发比例（`total / usable`） |
|------|:---:|
| `ok` | < 0.55 |
| `warm` | ≥ 0.55（`warm_ratio`） |
| `hot` | ≥ 0.75（`hot_ratio`） |
| `overflow` | ≥ 0.92（`overflow_ratio`） |

`analyze()` 会把请求拆成 section 桶（`system / runtime / memory / working_state / history / tool_observation / tool_schema / output_reserve`），产出 `RequestBudgetReport`（含 `total_tokens / pressure / sections / largest_messages / tool_schema_*`）。section 归桶靠 `_system_bucket` 按内容标题关键词识别（如 "Retrieved Project Memory" → memory、"Working State" → working_state）。

### 4.2 `suggest_limits`：按压力档位输出 `BudgetLimits`

`request_budget.py:141-182` —— 压力越高，投影越紧。`BudgetLimits` 字段：`max_memory_records / provider_raw_recent / recent_user_turns / max_tool_result_chars / max_tool_call_arg_chars / max_execute_code_chars / max_digest_chars`。

| 档位 | memory | raw_recent | tool_result_chars | digest_chars |
|------|:---:|:---:|:---:|:---:|
| `ok`（默认） | 10 | 8 | 4000 | 6000 |
| `warm` | 8 | 6 | 3000 | 5000 |
| `hot` | 6 | 5 | 2200 | 4200 |
| `overflow` | 4 | 4 | 1400 | 3200 |

> 注意：`suggest_limits` 也会看 `live_tokens`——即便 `pressure` 名义为 `ok`，只要 `live_tokens` 超过 `usable * warm_ratio`/`hot_ratio` 也会降档。

**工具 schema 不在裁剪范围内**：`BudgetLimits` 只约束历史、memory、observation。工具是 profile/provider 契约，保持稳定（见 §5）。

---

## 5. 与 Prompt Cache 稳定前缀架构的边界

（详见 `docs/prompt-cache-stable-prefix-architecture.md`）

`build_provider_request`（`context_manager.py:294-424`）是**唯一装配者**，物理布局如下：

```text
── STABLE PREFIX（可缓存，append-only）──
[S0] system.base                 static / cacheable
[S1] stable_system_sections      session_static / cacheable（能力清单 / active_tools / 规则）
[S2] system.user_preferences     session_static / cacheable
[S3] context.summary             session_static / cacheable  ← L1 摘要产物落在这里
── context.history ──            turn_dynamic / cacheable    ← L3 投影产物，稳定/动态分界线
── DYNAMIC TAIL（每轮重算，永不进前缀）──
[D*] dynamic_tail_sections       turn_dynamic / none（turn objective / deviation feedback）
[D1] runtime.anchor              turn_dynamic / none
[D2] runtime.working_state       turn_dynamic / none
```

各层裁剪**遵守"只裁历史与投影、不动稳定前缀顶部"的原则**：

- **L0（工具输出定界）** 发生在入库前，只影响历史里单条内容，不触碰前缀结构。
- **L1（摘要）** 产物进 `context.summary`，属稳定前缀的 `session_static` 段——只在压缩发生时变一次，同一会话内多数轮次恒定。
- **L2（剪枝）** 就地改写**老的** tool_result（在 `history` 段内、且在保护窗口之外），不改前缀顶部；剪枝后 `history` digest 变化只影响历史段之后的缓存，符合 append-only 原则。
- **L3（投影）** 只产出 `history` 段，永远排在稳定前缀之后。
- **动态尾部（runtime anchor / working state / turn objective）** 全部排在 `history` **之后**，`cache_policy="none"`，每轮重算但不打断前缀。

Hash 归因（`provider_request.py`）：

- `system_prefix_hash`：history 之前所有 section 的 digest——健康时应**跨 turn 恒定**，变化即说明动态内容漏进了前缀。
- `dynamic_suffix_hash`：最后一个 history 段之后所有 section 的 digest——每轮变化属正常。

> 局限（对应架构文档 Phase 4「Request-Aware Compaction」尚未完成）：当前裁剪仍**以历史消息为中心**，而非以"完整 provider request"为单位统一分预算。tool schema、memory projection 各自有上限，但没有一个统一的 request 级裁剪器按优先级（保留任务目标 → 最近关键证据 → 丢低价值噪声）全局取舍。长 loop 场景下这是下一步优化点。

---

## 6. 数据流全景

```text
工具执行
  └─ L0 ToolOutputRuntime.bound（>50KB/2000行 → 落盘 + 压缩预览）
        └─ add_tool_result / add_code_output（入库，max_output_chars=3000 再截断）
              └─ ContextManager.messages（append-only，永久保留）

每轮 run_turn：
  1. should_compress()？ ──命中──▶ compress()（L1）
        · 老消息 → LLM 锚定合并摘要 → _summary（进 [S3]）
        · 顺带 _prune_outputs()（L2）
        · 推进 _summary_cutoff、重读最近编辑文件
  2. RequestBudgetManager.suggest_limits(live_tokens) → BudgetLimits
  3. build_provider_request(limits)
        · ProviderContextProjector 按 limits 投影 history（L3）
        · 组装 STABLE PREFIX + history + DYNAMIC TAIL
  4. analyze 预算；pressure∈{hot,overflow} 且 prune 有收益
        ──▶ prune_tool_results()（L2）→ 收紧 limits → 重建请求
  5. 发送给 provider
```

---

## 7. 关键不变量（务必守住）

1. **原始历史 append-only**：任何裁剪都不删除 `messages` 数组元素；L1 靠 `_summary_cutoff` 游标、L2 靠就地替换 content、L3 靠临时投影。
2. **保护最近窗口**：`keep_recent=8` 内的消息不被 L1 摘要、不被 L2 骨架化（仅单条超 `max_single_result_tokens` 会头尾截断）。
3. **保护 skill 指令**：`load_skill` 结果免于 L2 剪枝。
4. **裁剪不破坏稳定前缀顶部**：所有裁剪作用在 history 段及其之后；动态内容一律在 history 之后。
5. **工具 schema 不参与裁剪**：工具是会话内冻结的稳定契约，token 压力靠投影与观测压缩消化，不靠删工具。
