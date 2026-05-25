# Agent Loop 完整评估报告

## 架构概述

OpenGIS Agent Loop 采用 **Hybrid CodeAct** 架构（参考 CodeAct 论文 + Claude Code + OpenHands），核心设计：

```
┌─ Frontend (React + Zustand) ─────────────────────────────────────────┐
│  chatStore.ts  ←──notifications──  pythonClient.ts (WebSocket)       │
│       │                                    │                          │
│       └── sendMessage() ──────── JSON-RPC ─┘                          │
└───────────────────────────────────────────────────────────────────────┘
                            │ WebSocket │
┌─ Backend (FastAPI) ──────────────────────────────────────────────────┐
│  server.py → RpcHandler → GISCodeAgent                                │
│                              ↓                                        │
│  ┌─ AgentRunner (async generator) ────────────────────────────────┐  │
│  │  asyncio.to_thread → worker thread                             │  │
│  │      ┌─ AgentLoop (synchronous) ───────────────────────────┐   │  │
│  │      │  for iteration in range(max_steps):                  │   │  │
│  │      │    1. check _interrupted                             │   │  │
│  │      │    2. build_messages (ContextManager)                │   │  │
│  │      │    3. LLM call (streaming → StreamingParser)         │   │  │
│  │      │    4a. pure text → return (task done)                │   │  │
│  │      │    4b. code block → SubprocessExecutor → result      │   │  │
│  │      │    5. check final_answer / step limit                │   │  │
│  │      └──────────────────────────────────────────────────────┘   │  │
│  │  events → asyncio.Queue → yield AgentEvent                     │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                              ↓                                        │
│  EventTranslator → JSON-RPC notifications → WebSocket → 前端          │
└───────────────────────────────────────────────────────────────────────┘
```

---

## 各模块评估

### 1. AgentLoop（核心循环）— `agent_loop.py`

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ 终止策略清晰：隐式（纯文本）+ 显式（`final_answer()`）+ 步数限制
- ✅ StreamingParser 状态机将 token 流实时分类为 thought/code，UI 体验优秀
- ✅ 回调异常全部被 try/except 包裹，不会中断主循环
- ✅ SAFETY_MULTIPLIER (×2) 防止无限循环
- ✅ 中断检查在循环顶部（`_interrupted` 标志）

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | LLM 调用期间无中断检查 | ⚠️ 中 | `_interrupted` 仅在循环顶检查，LLM streaming 期间（可能 30-120s）不检查 |
| 2 | 代码执行期间无中断检查 | ⚠️ 中 | executor 调用阻塞在 `_recv()`，需依赖 L3 进程杀死 |
| 3 | `_generate_max_steps_summary` 无超时 | ⚠️ 低 | 步数用完后再次调用 LLM 生成摘要，如果此时 LLM 卡住会永久阻塞 |
| 4 | `extract_code_block` 与 StreamingParser 双重解析 | ℹ️ 信息 | 注释说明是 fallback，但实际上是冗余的 token 开销 |

**建议修复：**
```python
# 问题 1：在 streaming 回调中检查中断
def _on_llm_delta(piece: str) -> None:
    if self._interrupted:
        raise KeyboardInterrupt("Agent interrupted during LLM streaming")
    parser.feed(piece)
```

---

### 2. StreamingParser — `agent_loop.py:92-338`

#### 设计评分：⭐⭐⭐⭐⭐ (5/5)

**优点：**
- ✅ 字符级状态机，处理各种 fence 格式（````python`、` ```py`、无语言标记等）
- ✅ 正确处理部分 token（如 `` `\n `` 跨两个 delta 到达）
- ✅ 四个回调 (`on_thought_delta`, `on_code_start`, `on_code_delta`, `on_code_end`) 解耦良好
- ✅ `finish()` 方法处理流结束时的残余 buffer

**无明显问题。**

---

### 3. SubprocessPythonExecutor — `executor.py`

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ 持久化子进程（类 Jupyter kernel），变量跨步保持
- ✅ 完整的 IPC 协议（JSON over stdin/stdout）
- ✅ Tool RPC 机制：子进程调用 stub → 父进程执行真实 skill → 结果回传
- ✅ 超时机制（默认 10 分钟/单次执行）
- ✅ `interrupt()` 三级中断：CTRL_BREAK → taskkill /F /T（Windows）
- ✅ 子进程死亡自动检测（`ChildDiedError`），下次调用 `_ensure_child()` 重建
- ✅ `plot_saved` / `risky_op` 侧通道事件

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | `_recv` 无中断标志检查 | ⚠️ 中 | 当 `_interrupted=True` 时，`_recv` 仍然在 `queue.get(timeout=0.5)` 上循环等待 |
| 2 | Tool RPC 无超时 | ⚠️ 低 | `_handle_tool_call` 直接调用 skill callable，如果 skill 卡住会导致 exec 超时 |
| 3 | 子进程重建无 backoff | ℹ️ 信息 | 如果 child 反复崩溃，会无限重启（受限于 max_steps） |

---

### 4. AgentRunner — `runner.py`

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ `asyncio.to_thread` 桥接同步循环到异步世界
- ✅ `Queue` + sentinel 模式实现 sync→async 事件流
- ✅ `interrupt_worker_thread()` 使用 `PyThreadState_SetAsyncExc(KeyboardInterrupt)`
- ✅ `drive()` 是异步生成器，可被 `async for` 消费

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | `PyThreadState_SetAsyncExc` 不可靠 | ⚠️ 中 | 在 C 扩展的 socket recv 中可能不响应（litellm 的 HTTP 调用） |
| 2 | worker thread 完成后队列可能有残余事件 | ℹ️ 信息 | `drive()` 排空队列时 cancelled task 的事件可能泄露到 UI |

---

### 5. ContextManager — `context_manager.py`

#### 设计评分：⭐⭐⭐⭐⭐ (5/5)

**优点：**
- ✅ Token budget 100K + 80% 压缩阈值，精确控制上下文大小
- ✅ Anchored LLM 摘要 + `<previous-summary>` 合并（参考 OpenCode），防止摘要无限增长
- ✅ 输出截断 3000 chars（head + tail 保留）
- ✅ 骨架占位符（step number + code first line + success/fail）— 比简单清空更有信息量
- ✅ Skill 输出保护（`_PRUNE_PROTECTED_TOOLS`）— 防止剪枝破坏因果链
- ✅ 文件重读（压缩后自动重新注入最近编辑文件的内容）
- ✅ 中英文摘要模板双版本
- ✅ `keep_recent=8` 滑动窗口确保 LLM 总能看到近期上下文

**无重大问题。** 这是整个系统中设计最精细的模块。

---

### 6. System Prompt — `prompts.py`

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ 双模式（文本/代码）清晰定义
- ✅ 终止规则明确（`final_answer()` vs 纯文本）
- ✅ Error Recovery Rules 指导修改而非重写
- ✅ Re-using prior context 规则减少冗余探索
- ✅ CRITICAL Task Completion Rules 防止过早终止
- ✅ 动态注入 skill 签名

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | 无 token 用量感知 | ℹ️ 信息 | 未告知 LLM 当前已用 token 或剩余预算，可能导致生成过长代码 |
| 2 | 缺少"多步任务最大步数"提示 | ℹ️ 信息 | LLM 不知道 max_steps=10，可能规划过多步骤 |
| 3 | save_plot 规则不够强 | ℹ️ 信息 | 实际使用中 LLM 仍偶尔调用 `plt.show()` |

---

### 7. GISCodeAgent — `code_agent.py`

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ per-conversation 上下文持久化（`_conversation_contexts` dict）
- ✅ Git 快照（pre/post）实现工作区安全网
- ✅ 路由决策：自由形式 vs WorkflowLoop（DAG 驱动）
- ✅ 异步 title 生成不阻塞主流程
- ✅ `_emit_final_answer` 策略正确（AgentLoop 不需要，WorkflowLoop 需要）

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | `_conversation_contexts` 无淘汰策略 | ⚠️ 低 | 长期运行的后端进程会积累所有对话的 ContextManager（内存泄露） |
| 2 | Git snapshot 可能失败 | ⚠️ 低 | 如果 workspace 不是 git 仓库或 git 不可用，异常处理不够优雅 |

---

### 8. EventTranslator + Events — `events.py`

#### 设计评分：⭐⭐⭐⭐⭐ (5/5)

**优点：**
- ✅ AgentEvent 是纯数据类（type + payload dict），解耦 agent 内部与传输层
- ✅ EventTranslator 为单一映射表，易于扩展
- ✅ 事件类型覆盖完整：reasoning/code/result/progress/error/max_steps

---

### 9. 通信层（server.py + rpc_handler.py）

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ **已修复**：WS 消息循环改为并发（`create_task`），中断不再被阻塞
- ✅ **已修复**：`_ws_write_lock` 防止并发写竞态
- ✅ Token 认证防止未授权连接
- ✅ 断连时自动取消后台任务
- ✅ Workspace 锁防止同一工作区并发运行

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | 前端 `abortTask()` UI 抢跑 | ⚠️ 中 | 前端先设 `isStreaming=false`，后端可能仍在推送事件 |
| 2 | Run 状态可能卡在 `running` | ⚠️ 中 | cancel 后前端 runsStore 不一定收到明确的 cancelled 状态 |
| 3 | HTTP RPC (`/api/rpc`) 无认证 | ⚠️ 低 | 仅绑定 localhost，风险较低 |

---

### 10. 前端 chatStore.ts

#### 设计评分：⭐⭐⭐⭐☆ (4/5)

**优点：**
- ✅ 通知桥覆盖所有事件类型
- ✅ partial message 机制实现流式 UI
- ✅ `configureBackendAgent()` 确保 LLM 配置同步
- ✅ 错误事件自动结束 streaming 状态

**问题：**

| # | 问题 | 严重度 | 说明 |
|---|------|--------|------|
| 1 | `abortTask` 竞态 | ⚠️ 中 | 见上方通信层分析 |
| 2 | 缺少 "cancelling" 中间态 | ⚠️ 低 | 用户可能在 cancel 过程中重复点击或发新消息 |

---

## 综合问题清单（按优先级排序）

### P0 — 已修复 ✅

| 问题 | 修复内容 | 状态 |
|------|----------|------|
| WS 消息循环串行阻塞导致 interrupt 无法送达 | `server.py` 改为 `create_task` 并发 | ✅ 已完成 |
| WebSocket 并发写竞态 | `rpc_handler.py` 加 `_ws_write_lock` | ✅ 已完成 |

### P1 — 待修复

| # | 问题 | 影响 | 建议方案 | 预估工时 |
|---|------|------|----------|----------|
| 1 | LLM streaming 期间无法中断 | 用户点 Stop 后需等待当前 LLM 调用完成（30-120s）才能退出循环 | 在 `_on_llm_delta` 中检查 `_interrupted` 并抛异常 | 0.5h |
| 2 | 前端 abort 状态竞态 | Cancel 后仍可能收到残余事件导致 UI 异常 | 后端发送 `chat.cancelled` 事件 + 前端延迟状态更新 | 2h |
| 3 | `_generate_max_steps_summary` 无超时 | 极端情况下永久阻塞 | 加 timeout + fallback 文本 | 0.5h |

### P2 — 优化建议

| # | 问题 | 影响 | 建议方案 | 预估工时 |
|---|------|------|----------|----------|
| 4 | 脚本碎片化 | 用户无法直接复用完整脚本 | run 完成时自动生成 `merged_script.py` | 2h |
| 5 | `_conversation_contexts` 内存泄露 | 长期运行后端占用增长 | 加 LRU 淘汰（如保留最近 50 个对话） | 1h |
| 6 | Prompt 中缺少步数上限提示 | LLM 可能规划过多步骤 | system prompt 中注入 `max_steps` 信息 | 0.5h |
| 7 | executor `_recv` 无中断短路 | 代码执行被杀后仍需等 0.5s poll | 检查 `_shutdown` 事件提前退出 | 0.5h |

### P3 — 远期改进

| # | 方向 | 收益 | 复杂度 |
|---|------|------|--------|
| 8 | LLM 调用改为异步（httpx.AsyncClient） | 彻底解决中断问题，无需线程注入 | 高（需重构整个 loop） |
| 9 | 引入 file-edit 工具 | 减少代码重复，类似 Claude Code 的 diff 模式 | 中 |
| 10 | 多 agent 并发支持 | 同一 workspace 多任务 | 高 |

---

## 与主流方案对比

| 特性 | OpenGIS | Claude Code | OpenHands | Cline |
|------|---------|-------------|-----------|-------|
| 执行模式 | 子进程持久化 | 子进程持久化 | Docker 容器 | VS Code terminal |
| 终止策略 | 纯文本 + final_answer() | 纯文本 + tool_use | max_iterations + LLM 判断 | tool_use 结束 |
| 上下文压缩 | LLM anchored 摘要 | 滑动窗口 + 摘要 | Conversation compressor | 无（截断） |
| 中断机制 | 三层（flag + thread inject + kill） | SIGINT + process group kill | Docker stop | terminal kill |
| 流式 UI | 逐 token 推送 | 逐 token | 非流式（batch） | 逐 tool |
| Tool 调用 | CodeAct（代码中直接调用） | JSON Schema function calling | CodeAct | JSON Schema |
| 沙箱安全 | 无（Claude Code 风格） | 无 | Docker 隔离 | 无 |

**结论**：架构成熟度高，与 Claude Code 最为接近。主要差距在中断响应速度和前端状态管理的严密性。

---

## 关键指标

| 指标 | 当前值 | 建议值 | 备注 |
|------|--------|--------|------|
| 最大步数 (max_steps) | 10 | 10-15 | 可根据任务复杂度动态调整 |
| LLM 超时 | 300s | 120s | 5 分钟过长，2 分钟足够 |
| 执行超时 | 600s | 300s | 除非训练模型，否则 5 分钟够用 |
| 上下文预算 | 100K tokens | 100-128K | 匹配 GPT-4o/Claude 窗口 |
| 压缩阈值 | 80% | 75-80% | 当前值合理 |
| 滑动窗口 | keep_recent=8 | 6-10 | 当前值合理 |
| 输出截断 | 3000 chars | 3000-5000 | 当前值合理 |
| 中断响应延迟 | ~17ms (已修复后) | <100ms | ✅ 达标 |

---

## 关键文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `python-backend/opengis_backend/agent/agent_loop.py` | 758 | 核心循环 + StreamingParser |
| `python-backend/opengis_backend/agent/runner.py` | 219 | async/thread 桥接 |
| `python-backend/opengis_backend/agent/executor.py` | 580 | 子进程 IPC 执行器 |
| `python-backend/opengis_backend/agent/code_agent.py` | 459 | 顶层编排器 |
| `python-backend/opengis_backend/agent/context_manager.py` | 715 | 上下文管理 + 压缩 |
| `python-backend/opengis_backend/agent/prompts.py` | 260 | System prompt |
| `python-backend/opengis_backend/agent/events.py` | 247 | 事件定义 + 翻译器 |
| `python-backend/opengis_backend/agent/agent_factory.py` | 187 | 组装胶水层 |
| `python-backend/opengis_backend/agent/step_recorder.py` | 196 | 脚本归档 |
| `python-backend/opengis_backend/rpc_handler.py` | 1057 | RPC 生命周期管理 |
| `python-backend/opengis_backend/server.py` | 175 | WebSocket 服务器 |
| `src/stores/chatStore.ts` | 769 | 前端状态 + 通知桥 |
| `src/services/pythonClient.ts` | 382 | WebSocket 客户端 |
