Step 3：System Prompt、Memory 与 Soul

在 Step 2 中，我们已经实现了多 session 管理与持久化。claw 可以创建、切换、保存和恢复不同会话。

但是，此时 claw 的上下文仍然比较简单：主要由系统指令和当前 session 的历史 messages 组成。随着 agent 能力逐渐增强，我们需要把上下文拆得更清楚：哪些是系统规则，哪些是用户长期记忆，哪些是 agent 的稳定风格，哪些是普通会话历史。

本 Step 的目标是引入三类稳定上下文：

system prompt
memory
soul

完成本 Step 后，claw 应该能够从独立文件加载 system prompt 和 soul，支持手动管理 memory，并在每次调用 LLM 时将这些稳定上下文加入 context builder。
3.1 实验目标

本 Step 需要完成以下内容：

    将 system prompt 从代码中拆出，改为从配置文件加载。

    新增 soul 配置，用于描述 claw 的稳定身份、风格和交互方式。

    实现 memory store，用于保存用户长期偏好、长期事实或长期项目背景。

    支持基本 memory 命令，例如添加、查看和删除 memory。

    将 system prompt、soul、memory 和当前 session messages 统一组装成 LLM 输入。

    明确 stable context 的边界：system prompt、memory 和 soul 是稳定上下文，不应该被普通 session 历史覆盖。

本 Step 的交付物是：稳定上下文能从普通对话历史中分离出来，并在每次请求中进入模型输入。
3.2 为什么需要拆分上下文

一个更完整的 agent 上下文通常包含多类信息：

system prompt
  稳定系统规则。

soul
  agent 的稳定身份和交互风格。

memory
  用户长期偏好、长期项目背景或长期事实。

session messages
  当前 session 的普通对话历史。

它们的生命周期和更新方式不同，不能全部混在普通 messages 里。

例如：

system prompt
  不应该被用户随便覆盖，通常由开发者维护。

memory
  应该长期保存，可以跨 session 复用。

soul
  应该稳定存在，用来控制 claw 的整体风格。

session messages
  只表示当前会话历史，不应该承担长期记忆职责。

3.3 System Prompt

system prompt 用来定义 claw 的基本运行规则。它应该偏向“系统约束”和“行为边界”，例如：

基于已有上下文回答用户问题。
不能伪造工具结果或外部执行结果。
如果上下文不足，需要明确说明。
回答应清晰、直接，并优先帮助用户完成当前任务。

要求：

    system prompt 不应该写死在 agent loop 中。

    system prompt 应从独立配置文件加载。

    修改配置文件后，重启程序可以生效。

    system prompt 不应该被普通用户消息覆盖。

3.4 Soul

soul 用来描述 claw 的稳定身份、语气和交互风格。它和 system prompt 不同。

system prompt
  更偏系统规则、权限边界和安全约束。

soul
  更偏 agent 的长期风格、人格设定和工作方式。

例如，可以在 soul 中写入一种稳定表达风格：

你说话时保持轻松、简短。
每句话末尾都加一个“喵”。

这样用户问“你是谁？”，claw 可能回答：

我是 claw，一个帮助你完成学习和编程任务的 agent 喵。

Soul 不应该写得过长，也不应该塞入具体任务状态。具体任务状态应该保存在当前 session 中。

要求：

    soul 从独立配置文件加载。

    soul 每次调用 LLM 时都会进入上下文。

    soul 不用于保存某个 session 的临时任务进度。

    soul 不应该被普通用户消息覆盖。

3.5 Memory

memory 用于保存跨 session 的长期信息，例如用户偏好、长期项目背景、常用约束等。

Memory 不等于聊天记录。它应该保存长期有效、未来可能反复使用的信息，而不是把所有历史对话都存进去。

适合写入 memory 的例子：

用户正在实现一个名为 claw 的课程 agent 项目。
用户偏好中文回答。
用户希望课程 Lab 文档语言清晰、任务可验收。

不适合写入 memory 的例子：

用户刚才说了一句“你好”。
用户临时让模型解释某个词。
某一轮对话中的普通寒暄。

本 Step 使用手动 memory 管理：只有明确的 memory 命令会修改 memory。

需要支持：

/memory add <content>
/memory list
/memory delete <memoryId>

注意：这些命令由 CLI 直接处理，不应该作为普通用户消息发送给 LLM。
3.6 稳定上下文的边界

本 Step 需要明确一个边界：

system prompt、memory、soul 是 stable context。
session messages 是 conversation context。

二者职责不同：

stable context
  负责长期规则、长期偏好和长期风格。

conversation context
  负责当前 session 中的临时对话历史和任务进度。

因此：

    system prompt 不应该由普通对话自动改写。

    soul 不应该由普通对话自动改写。

    memory 只能通过明确的 memory 命令写入或删除。

    session messages 可以随着对话继续增长。

3.7 最小程序行为

程序运行后，可以通过 memory 命令管理长期记忆，并验证其跨 session 持久化能力：

$ python -m src.main

claw started. Type /exit to quit.

User> /memory add 用户正在实现一个名为 claw 的课程 agent 项目。
Added memory: mem_001

User> /session new
Created session: session_002

User> 我现在在做什么项目？
Assistant> 你正在实现一个名为 claw 的课程 agent 项目。

这个例子说明：

1. memory 可以被手动写入，并持久化到磁盘。
2. memory 在不同 session 中都可见。
3. assistant 可以基于 memory 回答问题，而不依赖当前 session 的历史消息。
4. memory 是跨 session 的长期上下文，而不是某个 session 的局部状态。
