Step 2：多 Session 管理与持久化

在 Step 1 中，我们已经实现了一个内存版多轮对话 loop。程序可以在终端中持续接收用户输入，并把当前会话中的历史消息一并发送给 LLM。

但是，Step 1 仍然存在两个问题：程序退出后会话历史会丢失，用户也无法同时维护多个不同主题的会话。

本 Step 的目标是在 Step 1 的基础上实现多 session 管理与 session 持久化，使 claw 能够创建、切换、保存和恢复不同会话。

完成本 Step 后，你应该能够创建多个 session，在不同 session 之间切换，并在程序重启后恢复历史对话。
2.1 实验目标

本 Step 需要完成以下内容：

    支持多个 session。

    支持创建、列出、切换和查看 session。

    将 session 保存到本地文件中，程序重启后可以恢复。

    每个 session 拥有独立的历史消息。

本 Step 的交付物是“多个可恢复的独立会话”。
2.2 为什么需要 Session

在 Step 1 中，程序运行期间只有一个内存版 session。所有对话都写在同一个消息列表里。

这种方式适合最小 demo，但不适合真实使用：

程序退出后，历史消息全部丢失。
不同主题的对话混在一起，模型容易拿错上下文。

例如，用户可能希望同时维护多个会话：

session_001：实现 claw 课程项目
session_002：复习算法作业
session_003：整理实习经历

每个 session 都应该有独立的历史消息。切换到某个 session 后，claw 只应该使用该 session 的上下文，而不是把所有对话混在一起。
2.3 Session 数据要求

你需要扩展 Step 1 的 session，使它可以被唯一识别、展示和持久化。

每个 session 至少需要保存：

sessionId
title
messages
createdAt
updatedAt

字段命名和文件组织可以自行设计，但必须满足：

    sessionId 可以用于创建、切换和持久化。

    title 可以帮助用户识别不同会话。

    messages 保存该 session 独立的历史消息。

    createdAt 和 updatedAt 能反映创建与更新时间。

不要把所有会话写进同一个文件后再靠人工区分。程序必须能用 sessionId 稳定找到对应会话。
2.4 Session 持久化

Session 需要保存到本地文件中。推荐使用 JSON，因为它易读、易调试，也方便验收。

要求：

    程序重启后，历史 session 不能丢失。

    切换 session 后，新的 LLM 请求只使用对应 session 的历史消息。

    保存失败时，需要输出清晰错误信息。

    JSON 文件损坏或解析失败时，不能静默丢失数据。

2.5 多 Session 命令

本 Step 需要支持最基础的 session 管理命令：

/session new
  创建一个新的 session，并切换到该 session。

/session list
  列出所有已有 session，包括 sessionId、title、消息数量和更新时间。

/session switch <sessionId>
  切换到指定 session。

/session rename <sessionId> <title>
  修改指定 session 的标题。

/session delete <sessionId>
  删除指定 session。

注意：这些命令由 CLI 直接处理，不应该作为普通用户消息发送给 LLM。
2.6 Context Builder 雏形

从本 Step 开始，发送给 LLM 的上下文不应由 CLI 或 LLM client 模块临时拼接。你需要引入一个 context builder 模块负责构造模型输入。

本 Step 中，context builder 至少需要完成：

加入系统指令
加入当前 session 的历史 messages
返回发送给 LLM 的 messages

Context builder 的第一原理是：存储结构不一定等于模型输入结构。session 文件里可以有 title、createdAt、updatedAt 等字段，但发给 LLM 的通常只应该是模型能理解的 messages。

具体函数名、模块名和内部实现可以自行设计。关键是职责边界要清楚：session 负责保存历史，context builder 负责把历史组织成 LLM 输入。
2.7 最小程序行为

程序运行后，可以使用 session 命令管理会话：

$ python -m src.main

claw started. Type /exit to quit.
Created session: session_002

User> /session list
Sessions:
* session_002  新会话        messages=0    updated=2026-06-25 20:10
  session_001  算法作业    messages=8    updated=2026-06-25 20:00

User> 你好，这个会话是用来写数据库作业的。
Assistant> 好的，我们可以在这个会话中讨论数据库作业。

User> /session switch session_001
Switched to session: session_001
History:
User> 这个会话是用来写算法作业的。
Assistant> 好的，我们可以继续讨论算法作业。

这个例子说明：

    session 可以被创建和切换。

    不同 session 拥有不同历史。

    程序重启后仍然可以恢复已有 session。
