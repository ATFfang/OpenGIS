# OpenGIS 后端：新增一个 Tool 的完整流程

> 适用范围：`python-backend/opengis_backend` 下的 function-call 工具（LLM 可调用的函数）。
> 本文描述"从零加一个 tool"需要接触的所有文件，以及与 prompt-cache 稳定前缀架构的关系。

---

## 1. 机制总览

OpenGIS 的工具是**自动发现 + 装饰器注册**的机制，新增工具的最小成本是"在 `builtin/` 下新建一个文件"，但要做对需要额外登记 capability。数据流如下：

```text
tools/builtin/*.py  ──@tool 装饰器──▶  全局 _registry（registry.py:40）
   （自动发现，无需手动登记）              │
                                        │ ToolRegistry.list_registered()
                                        ▼
factory_common.build_loop_runtime_bundle
   按 profile.tool_groups 过滤 group ──▶ build_tool_schemas(registered)  →  给 LLM 的 JSON schema
                                       └▶ build_tool_callables(registered) →  name → 可执行函数
                                          ▼
                                   ToolRuntime.execute（参数校验 / 权限 / 执行 / 输出裁剪）
```

关键事实：

- `ToolRegistry.discover_and_load()`（`tools/registry.py:126-143`）遍历 `tools/builtin/` 下所有模块并 `import`，触发 `@tool` 装饰器把工具塞进全局 `_registry`。
- `tools/builtin/__init__.py` 是空的——**新增工具文件后不需要手动导出或登记**。
- profile 通过 `tool_groups` 过滤工具：`factory_common.build_loop_runtime_bundle` 里 `registered = [s for s in registered if s.schema.group in effective_groups]`（`factory_common.py:128-130`）。

---

## 2. 工具的定义结构

底层由两个 dataclass 描述（`tools/schema.py`）：

- **`ToolParam`**：单个参数，字段 `name / type / description / required / default / options / min_value / max_value`。`type` 取自 `ParamType` 枚举：`file_path / number / string / enum / boolean / geometry / crs / layer_ref / array / object / any`。
- **`ToolSchema`**：一个工具的完整定义。关键字段 `name / display_name / description / category / params / returns / examples / tags / version / group`。其中 **`group`** 决定工具属于哪个 tool group（`core / qgis / osm / datasource / worker / subagent / workflow / report`），profile 靠它过滤。

工具通过 **`@tool` 装饰器**定义并自动注册（`tools/registry.py:43-120`）。装饰器会把函数包成异步 `wrapper`，异常自动包成 `ToolResult(success=False, error=...)`，同步函数自动丢进线程池执行。

---

## 3. 新增流程（分步）

### 步骤一（必做）：编写工具文件

在 `python-backend/opengis_backend/tools/builtin/` 下新建 `my_tool.py`，用 `@tool` 装饰器。范式参考 `builtin/glob_tool.py`：

```python
from opengis_backend.tools.context import ToolContext
from opengis_backend.tools.registry import tool


@tool(
    name="my_tool",                    # LLM 调用用的唯一函数名（全局唯一）
    display_name="My Tool",
    description="一句话说清它做什么、什么时候用（LLM 靠这段选工具）",
    category="vector",                 # vector|raster|statistics|conversion|visualization|data|system...
    params=[
        {"name": "input_path", "type": "string", "required": True,
         "description": "输入文件路径"},
        {"name": "mode", "type": "enum", "required": False,
         "options": ["a", "b"], "default": "a", "description": "运行模式"},
    ],
    returns="dict，包含 output_path / feature_count 等",
    examples=["my_tool(input_path='data.geojson')"],
    needs_context=True,                # 需要 ctx.notify / workspace 时设 True
    group="core",                      # ★ 决定哪些 profile 能用它（见步骤三）
)
def my_tool(ctx: ToolContext, input_path: str, mode: str = "a") -> dict:
    # needs_context=True 时 ctx 是第一个位置参数，runtime 自动注入。
    # 不要把 ctx 写进 params。
    return {"output_path": ..., "feature_count": ...}
```

要点：

- **函数签名必须与 `params` 对齐**。`needs_context=True` 时首参为 `ctx`，且 `ctx` 不写进 `params`。
- **同步函数**会被自动丢到线程池执行；`async def` 也支持。
- 不必在函数体里 try/catch 全包——装饰器已把异常统一包成 `ToolResult`（`registry.py:89-108`）。
- 返回值放进 `ToolResult.data`。

### 步骤二（强烈建议）：登记 capability

在 `agent/execution/tool_capabilities.py` 的 `CAPABILITIES` 字典加一行（`tool_capabilities.py:21-86`）：

```python
"my_tool": ToolCapability("data", side_effect="file", object_type="dataset"),
```

`ToolCapability` 字段：`domain`（域）、`side_effect`（`none|file|map|worker|operation|workflow|external`）、`object_type`、`repair_tool`。

**不登记的后果**：`capability_for` 会 fallback 到 `ToolCapability("general")`（`tool_capabilities.py:89-90`），导致：

1. 该工具在**能力清单**里被归到 "General-purpose tools"，模型对它的域认知模糊；
2. `side_effect` 默认 `none`，会绕过按副作用做的执行控制（`tools_with_side_effect`）。

**引入全新 domain 时**：必须同时在 `_DOMAIN_MANIFEST_LABELS`（`tool_capabilities.py:101-112`）加对应标签，否则能力清单 `format_capability_manifest` 会输出裸 domain 名（fallback 到 domain 字符串本身），破坏清单可读性。

### 步骤三（按需）：确认 profile 能看到它

工具最终对某个 agent 可见，取决于它的 `group` 是否在 `profile.tool_groups` 里（`factory_common.py:128-130`）。

- 想让**所有 profile 默认可用** → `group="core"`。
- 只在特定场景启用 → 用对应 group（`qgis / osm / datasource / worker / subagent / workflow / report`），并确认目标 `AgentProfile.tool_groups` 已包含它，否则工具注册了但没有任何 profile 会加载，等于隐身。

### 步骤四（建议）：补测试

参照 `tests/test_agent_tools.py` 惯例，新增用例断言：

- 注册成功：`registry.has("my_tool")`；
- schema 正确（参数、必填项）；
- 执行结果符合预期。

---

## 4. 最小接触点清单（TL;DR）

| 顺序 | 文件 | 改什么 | 必须? |
|---|---|---|---|
| 1 | `tools/builtin/my_tool.py`（新建） | `@tool` 定义 + 实现 | ✅ 必须 |
| 2 | `agent/execution/tool_capabilities.py` | `CAPABILITIES` 加一行；引入新域时补 `_DOMAIN_MANIFEST_LABELS` | ⭐ 强烈建议 |
| 3 | 目标 `AgentProfile.tool_groups` | 确认 group 被启用（非 core group 时） | 按需 |
| 4 | `tests/test_agent_tools.py` | 加注册 / 执行测试 | 建议 |

---

## 5. 与 Prompt Cache 稳定前缀架构的关系

（详见 `docs/prompt-cache-stable-prefix-architecture.md`）

- **工具 schema 位于稳定前缀**：`build_tool_schemas` 暴露的是整个 profile 的去重全集（非每轮子集），是会话内冻结、全量稳定的。新增工具会改变工具 schema 前缀，但这是**跨 run 的一次性变化**，同一会话内保持稳定。
- **能力清单也在稳定前缀**：`format_capability_manifest` 按 `domain` 聚合并排序、进 `[S1]` 稳定段。
  - 新增**已有域**的工具 → 能力清单文本不变，`system_prefix_hash` 不受影响。
  - 新增**全新域**的工具 → 能力清单多一行，属设计允许的跨 run 一次性变化。
- **结论**：新增工具时，只要它属于已有域的 `core` 工具，对同一会话内的前缀恒定性零影响；引入全新 domain 只会在跨 run 层面让稳定前缀改变一次。

> 反面约束（务必遵守）：**不要**为了"减少 token"而让工具每轮动态增删。工具排在对话历史之前，任何中途变动会连带其后整段历史一起 miss 缓存。工具集应遵守"每会话冻结 + 单调扩展"。
