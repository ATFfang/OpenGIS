# OpenGIS Prompt Cache 稳定前缀架构升级方案

> 修订原则：默认不依赖 `prompt_cache_key` / `prefix_caching.cache_key`。  
> 主流 OpenAI-compatible / DeepSeek-compatible 路径应优先通过“前缀文本完全稳定”自动命中缓存。

---

## 1. 结论

OpenGIS 后续的 Prompt Cache 优化主线应从“给 provider 传缓存 key”调整为：

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

### 2.1 请求构造边界不够清楚

当前系统里，最终发给 LLM 的内容仍然由多个层级拼接：

- loop 负责控制运行过程
- context projector 负责投影上下文
- tool selector 负责决定工具暴露
- llm caller 负责转换和调用 provider
- telemetry 负责记录 usage

这些职责都合理，但缺少一个明确的 **ProviderRequestBuilder** 作为最终请求边界，导致：

- system prompt section 不够稳定
- tools schema 暴露顺序可能波动
- memory / runtime 状态容易混进前缀
- 最终请求难以解释为什么命中或没命中缓存

### 2.2 缓存命中率偏低的主要原因

近期观测中，缓存命中率长期约 20%-30%。更可能的原因不是缺少缓存 key，而是：

- tools schema 数量、顺序、描述存在波动
- system prompt 中混入动态内容
- memory 投影每轮变化过大
- 上下文裁剪以历史消息为中心，而不是以完整 provider request 为中心
- provider usage 字段没有统一规范，前端观测存在误判空间

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

### 3.1 ProviderRequestBuilder

职责：生成唯一、完整、可观测的 provider request。

它应该输出一个结构化对象，而不是散落的 `messages + tools + kwargs`：

```python
ProviderRequest(
    model=...,
    provider=...,
    session_id=...,
    system_sections=[...],
    messages=[...],
    tools=[...],
    metadata={
        "stable_prefix_hash": "...",
        "tool_schema_hash": "...",
        "system_prefix_hash": "...",
        "dynamic_suffix_hash": "...",
    },
)
```

ProviderRequestBuilder 是 loop 和 provider 之间的硬边界。loop 不再直接关心 provider wire format，provider adapter 不再反向理解 agent 业务语义。

### 3.2 StablePrefixComposer

职责：把可缓存内容组织成稳定前缀。

稳定前缀优先级：

```text
1. Tool schema
2. System core
3. Tool protocol
4. Project rules
5. Stable memory projection
6. Active task contract
```

动态后缀：

```text
1. 当前 user turn
2. tool results
3. code outputs
4. runtime warnings
5. transient map / worker / operation state
6. 当前 loop anomaly / deviation feedback
```

原则：

- 前缀内不出现当前时间、run id、随机采样、实时 token 统计。
- tools 固定排序，schema 序列化必须 deterministic。
- memory 检索结果必须排序稳定，且限制数量和格式。
- runtime 状态默认后置，不污染缓存前缀。

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

这比“历史消息太长就删旧消息”更可靠，因为 token 开销最大的不一定是聊天历史，也可能是 tools schema、memory projection、重复 tool outputs。

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
- `system_prefix_hash`
- `memory_projection_hash`
- `dynamic_suffix_hash`
- input / output / cache read / cache write tokens
- cache hit ratio
- tools count
- request section token estimate

Settings 中展示的不应只是总 token，而应该能看到每轮波形和 section 变化。真正有用的问题是：

```text
这轮 cache hit 低，是 provider 没返回，还是前缀 hash 变了？
如果前缀 hash 变了，是 tools、system、memory 哪一段变了？
```

---

## 4. 和 opencode 的对应关系

opencode 的核心经验不是“所有 provider 都传 cache key”，而是：

- cache schema 与 provider wire format 解耦
- policy 层决定缓存意图
- protocol 层只在 provider 支持时降级
- OpenAI-compatible provider 依赖隐式前缀缓存
- Anthropic / Bedrock 才需要 explicit cache marker

OpenGIS 应吸收的是这个分层，而不是把某个 provider 参数硬塞进所有模型请求。

---

## 5. 分阶段实施

### Phase 1：ProviderRequestBuilder 收口

- 新增 provider request 数据结构。
- 所有 LLM 请求统一经 ProviderRequestBuilder。
- `llm.py` 只接收已构建好的请求，不再承担 section 组织职责。
- 记录 request hash，但暂不改变 provider wire payload。

验收：

- 既有 agent loop 行为不变化。
- 每轮 run archive 能看到 stable prefix hash / tool schema hash。

### Phase 2：Prompt Section 化

- 把 system prompt 拆成稳定 section 和动态 section。
- 移除前缀中的 run id、当前时间、临时状态。
- memory projection 使用稳定排序和稳定格式。

验收：

- 连续普通对话中 `system_prefix_hash` 不应频繁变化。
- 相同工具域下 `tool_schema_hash` 不应频繁变化。

### Phase 3：Request-Aware Compaction

- 裁剪逻辑改成面向完整 provider request。
- 历史消息、tool outputs、memory projection 分别有预算。
- 优先保留当前任务目标与最近关键证据。

验收：

- 长 loop 不再因为历史消息膨胀导致 provider request 爆炸。
- 简单任务不会暴露大量不相关工具和上下文。

### Phase 4：UsageNormalizer + Cache Observatory

- 后端统一 usage。
- 前端 Settings 展示 normalized usage。
- 增加 prefix hash 变化解释。

验收：

- DeepSeek / OpenAI-compatible / Anthropic-like 返回都能落到统一字段。
- cache hit 为空时能说明是 provider 未返回还是确实没有命中。

### Phase 5：Provider-Specific Explicit Cache，可选

- Anthropic / Bedrock 支持 inline cache markers。
- OpenAI-compatible / DeepSeek-compatible 默认不传 cache key。
- 所有 provider-specific 行为必须由 capability gate 控制。

验收：

- 不支持 explicit cache 的 provider 不会收到未知参数。
- 支持 explicit cache 的 provider 可独立开启、关闭、回退。

---

## 6. 预期收益

在不依赖 cache key 的情况下，收益主要来自稳定前缀：

| 阶段 | 预期命中率 |
|------|------------|
| 当前 | 20%-30% |
| ProviderRequestBuilder + usage 观测 | 25%-35% |
| system/tools/memory 稳定前缀 | 40%-60% |
| request-aware compaction 完成 | 50%-70% |
| Anthropic/Bedrock explicit marker | 视 provider，可能更高 |

这个提升不是靠“强制缓存”，而是靠让 provider 看到尽可能长、尽可能不变的 prefix。

---

## 7. 非目标

以下内容不进入默认方案：

- 默认向所有 provider 传 `prompt_cache_key`
- 默认向 OpenAI-compatible provider 传 `prefix_caching.cache_key`
- 为了缓存命中牺牲 tool correctness
- 为了减少 token 硬隐藏用户明确需要的工具
- 用 prompt 规则替代 request 架构边界

---

## 8. 最终原则

OpenGIS 的缓存优化应该满足三句话：

1. **前缀稳定优先于缓存参数。**
2. **请求构造必须可解释、可观测、可回放。**
3. **provider-specific 能力只能在 adapter 层显式开启，不能污染 agent loop。**
