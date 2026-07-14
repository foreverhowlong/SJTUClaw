# SJTUClaw 整体架构与模块设计

> 本文是分步实施路线图，其中目录树保留了早期规划名称。当前已实现模块、
> API 与故障语义以仓库根目录 `README.md` 和源码为准。

## Context

课程项目 SJTUClaw:一个最小 agent runtime,需覆盖 [generalSpec.MD](generalSpec.MD) 的全部 11 项要求(LLM 调用、CLI 多轮对话、多 session、上下文管理、压缩、tool call、Gateway+图形化入口、定时任务、workspace+approval、skill system、统一 runtime),并加入两个前沿记忆点:**Agent 思考过程实时可视化** 与 **自省式长期记忆 (auto-memory)**。

已确认的决策:
- 技术栈:**Python 3.11+**(asyncio),Gateway 用 **FastAPI**,前端用 **React + Vite**
- 图形化入口:先做 **Web**,架构上保留桌宠/IM bot 的可拓展性
- LLM 接入:OpenAI-compatible 接口(`openai` SDK + 可配置 base_url),配置来自 `.env`
- 持久化:本地文件(JSONL/JSON/Markdown),不引入数据库——透明、可演示、符合 spec"持久化到本地文件"

## 核心架构思想

**一切入口都是"事件流的渲染器"。** 唯一的业务门面是 `AgentService`:

```
AgentService.run_turn(session_id, user_input, ...) -> AsyncIterator[AgentEvent]
```

agent loop 内部每一步(LLM 流式输出、tool call、approval 等待、memory 写入、压缩)都 emit 结构化 `AgentEvent`:

- CLI 订阅事件流 → 渲染成终端文本
- Gateway 订阅事件流 → 通过 WebSocket 原样推给 Web 前端 → 前端渲染成"透明大脑"可视化时间线
- Scheduler 到期触发 → 调用同一个 `run_turn`,事件写回 session

这一设计同时满足:spec 第 11 条"所有入口复用同一套 runtime"(架构上无法绕过),以及记忆点 1"思考过程实时可视化"(零额外成本,是架构副产品)。未来加桌宠/QQ bot 只需写一个新的事件渲染器。

## 目录结构

```
SJTUClaw/
├── pyproject.toml           # uv 管理依赖
├── .env.example             # API_KEY / BASE_URL / MODEL 模板(.env 加入 .gitignore)
├── claw/                    # ── 核心 runtime 包(不依赖 FastAPI)──
│   ├── config.py            # 配置加载:.env + 环境变量,缺失时清晰报错
│   ├── errors.py            # 统一异常:ConfigError / LLMError / ToolError ...
│   ├── llm.py               # LLMProvider:messages 进、流式 delta/tool_call 出;重试与错误归一化
│   ├── events.py            # AgentEvent 类型定义(全系统的通用语言,见下)
│   ├── store/
│   │   ├── sessions.py      # SessionStore:JSONL 消息 + meta.json(summary、workspace、附件元数据)
│   │   ├── memory.py        # MemoryStore:data/memory/*.md,CRUD
│   │   ├── approvals.py     # ApprovalStore:pending/approved/denied 状态机
│   │   └── tasks.py         # TaskStore:定时任务及执行历史
│   ├── context.py           # ContextBuilder:system prompt + soul + memory + summary + 近期历史 → messages
│   ├── compaction.py        # 超阈值时 LLM 总结早期消息为 summary;失败不丢原始历史
│   ├── tools/
│   │   ├── registry.py      # ToolRegistry:注册、schema 导出、分发执行、requires_approval 标记
│   │   ├── builtin.py       # 只读三件套:current_time / list_dir / read_file
│   │   ├── workspace.py     # advanced:write_file / run_command(强制走 approval)
│   │   └── memory_tool.py   # save_memory tool(auto-memory 的落笔通道)
│   ├── workspace.py         # Workspace:当前工作目录设置 + 路径边界校验(拒绝越界)
│   ├── skills/loader.py     # 扫描 skills/*/SKILL.md,列表注入 system prompt,按需加载全文
│   ├── memory_reflection.py # 自省式记忆:提炼候选 → 与现有记忆合并/去重/修正(记忆点 2)
│   ├── scheduler.py         # asyncio 后台循环:到期任务 → AgentService.run_turn → 结果写回 session
│   └── agent.py             # AgentService + agent loop(唯一门面)
├── cli/main.py              # CLI 入口:REPL、/session /memory /task /skill 等内部命令、事件→终端渲染
├── gateway/
│   ├── app.py               # FastAPI:REST(sessions/messages/approvals/tasks/memory/skills/attachments)
│   └── ws.py                # WebSocket:AgentEvent 直通前端;不持有任何 API KEY
├── web/                     # React + Vite 单页应用
│   ├── src/components/Chat.tsx        # 消息流 + 输入
│   ├── src/components/AgentTrace.tsx  # 思考过程可视化时间线(记忆点 1)
│   ├── src/components/ApprovalCard.tsx# 审批弹卡:批准/拒绝
│   └── src/components/{Sessions,Memory,Tasks}Panel.tsx
├── skills/
│   ├── course-report/SKILL.md   # 必做:生成课程报告 Markdown 草稿 → workspace 写入 → approval
│   ├── daily-digest/SKILL.md    # 汇总当日 session 要点(配合 scheduler 演示定时任务)
│   └── code-explain/SKILL.md    # 读 workspace 代码文件生成讲解文档
├── prompts/                 # system_prompt.md / soul.md(独立配置加载)
├── data/                    # 运行时数据(gitignore):sessions/ memory/ tasks/ approvals/ attachments/
└── tests/                   # pytest:store、context、compaction、registry、approval 状态机
```

## 关键模块设计

### 1. AgentEvent(events.py)—— 全系统通用语言
事件类型(dataclass + type 字段,可 JSON 序列化):
`turn_start / llm_delta / llm_message / tool_call / tool_result / approval_required / approval_resolved / memory_written / compaction_started / compaction_done / error / turn_end`
每个事件带 session_id、时间戳、payload。CLI 与 Web 消费同一份定义。

### 2. Agent loop(agent.py)
```
run_turn():
  1. 追加 user 消息到 SessionStore
  2. 压缩检查(compaction.py,超阈值先压缩)
  3. ContextBuilder 组装 messages
  4. 循环:调 LLM(流式 emit llm_delta)
     - 无 tool call → 写回 assistant 消息,emit turn_end,结束
     - 有 tool call → emit tool_call:
         · 只读 tool → 直接执行
         · advanced tool → 创建 approval,emit approval_required,await Future 挂起;
           批准→执行 / 拒绝→构造"用户拒绝"observation(两者都写入 session 历史)
       结果写回 session(role=tool),继续循环
  5. 轮末钩子:memory_reflection(见下)
```
Approval 挂起用 asyncio.Future,由 ApprovalStore.resolve() 唤醒;CLI 里是 y/n prompt,Web 里是审批卡片,同一机制。

### 3. ContextBuilder(context.py)
组装顺序:system prompt(prompts/system_prompt.md)→ soul(prompts/soul.md)→ memory 摘要块 → skill 列表块 → 当前 session summary(若有)→ 最近 K 轮原始消息。各块独立可关,便于调试与答辩讲解。

### 4. 自省式记忆(memory_reflection.py)—— 记忆点 2
双通道:
- **主动通道**:模型可随时调用 `save_memory` tool 记录用户偏好/事实(写入需轻量确认或直接落盘,答辩可讲权衡)
- **反思通道**:每 N 轮或压缩触发时,后台用 LLM 对近期对话提炼候选记忆,与现有 memory 做合并/去重/矛盾修正(reflection pass),emit `memory_written` 事件 → 前端实时显示"claw 记住了 ×××",演示效果强
- 手动管理:CLI `/memory list|add|rm` + Web Memory 面板,满足 spec 的手动增删查要求

### 5. 思考过程可视化(AgentTrace.tsx)—— 记忆点 1
WebSocket 收到 AgentEvent 流,渲染为垂直时间线:思考文本流式打字、tool call 卡片(参数+结果折叠)、approval 卡片(内嵌批准/拒绝按钮)、memory 写入徽章、压缩提示。核心工作量在前端渲染,后端零额外成本。

### 6. 其余模块要点
- **SessionStore**:`data/sessions/<id>/messages.jsonl`(append-only)+ `meta.json`;附件存 `data/attachments/<session_id>/`,metadata 记入 meta.json,天然按 session 隔离
- **Scheduler**:任务持久化 `data/tasks/*.json`(内容、计划 once/cron、next_run、状态、所属 session、执行历史);asyncio 循环每 30s 检查,到期调 `run_turn`;启动时恢复未完成任务
- **Workspace**:`/workspace set <path>` 设定边界;所有文件/命令 tool 执行前经 `Workspace.resolve()` 校验路径不越界
- **Skill loader**:SKILL.md 带 YAML frontmatter(name/description);列表常驻 system prompt,全文按需注入(显式 `/skill use` 或模型调用 `load_skill` tool);course-report 产出经 write_file + approval 落盘
- **Gateway 安全**:API KEY 仅存 runtime 侧 `.env`;前端只知道 gateway 地址;所有错误经统一异常转为用户可读提示

## 实施里程碑(每个可独立验收,对应 spec 条目)

1. **M1 地基**:pyproject + config + llm.py + errors;CLI 单轮问答(spec-1)
2. **M2 会话**:SessionStore + CLI 多轮 REPL + `/session` 命令 + Ctrl-C/退出处理(spec-2/3)
3. **M3 上下文**:ContextBuilder + prompts/ + MemoryStore 手动 CRUD + compaction(spec-4/5)
4. **M4 工具**:events.py + ToolRegistry + 三个只读 tool + 事件化 agent loop,CLI 渲染事件(spec-6)
5. **M5 入口**:FastAPI Gateway + WebSocket + React 前端(聊天/session/附件/**AgentTrace 可视化**)(spec-7 + 记忆点 1)
6. **M6 定时**:Scheduler + TaskStore + CLI/Web 任务管理(spec-8)
7. **M7 边界**:Workspace + advanced tools + Approval 全链路(CLI y/n + Web 审批卡)(spec-9)
8. **M8 技能**:skill loader + course-report 等 3 个 skill(spec-10)
9. **M9 记忆点收尾**:memory_reflection 自省通道 + 可视化打磨 + 演示脚本(记忆点 2)

## 验证方式

- `pytest tests/`:SessionStore 读写与隔离、ContextBuilder 组装顺序、compaction 失败不丢历史、ToolRegistry 分发、Approval 状态机、Workspace 越界拒绝(LLM 层用 fake provider,不耗 API)
- CLI 端到端:多轮对话 → 切 session → 触发 tool → 触发 approval → 查看 memory
- Web 端到端:preview 启动 gateway + vite,验证消息流/可视化时间线/审批卡/附件上传
- Scheduler:创建 1 分钟后的一次性任务,确认结果写回对应 session
