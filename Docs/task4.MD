Step 4：上下文压缩 Compaction

在 Step 3 中，我们已经将 claw 的上下文拆分为两类：

stable context:
  system prompt
  memory
  soul

conversation context:
  当前 session 的历史 messages

随着对话轮数增加，当前 session 的历史 messages 会不断变长。如果每次都把完整历史发送给 LLM，请求会越来越慢，也可能超过模型上下文长度限制。

本 Step 的目标是实现 compaction：当某个 session 的历史消息过长时，将较早的 messages 压缩成一段 summary，只保留最近几轮原始消息。

需要特别注意：compaction 只处理 session messages，不处理 system prompt、memory 和 soul。

完成本 Step 后，claw 应该能够在长对话中自动压缩旧消息，同时保留当前任务状态和重要上下文。
4.1 实验目标

本 Step 需要完成以下内容：

    扩展 session 数据结构，增加当前 session 的 summary。

    实现 compaction 判断逻辑，判断当前 session 是否需要压缩。

    使用 LLM 将旧消息压缩成摘要。

    将旧 messages 合并进 session summary。

    只保留最近几轮原始 messages。

    LLM 上下文应包括：

system prompt
soul
memory
session summary
recent session messages

    明确 compaction 边界：system prompt、memory、soul 不参与压缩。

本 Step 的交付物是：长对话不会无限增长，旧消息能被安全压缩成 session summary。
4.2 为什么需要 Compaction

在 Step 1 和 Step 2 中，claw 的多轮对话依赖完整历史消息。这种方式简单直接，但随着对话变长，会出现几个问题：

    上下文越来越长，可能超过模型上下文窗口。

    每轮请求都携带大量旧消息，调用速度变慢。

    API 调用成本增加。

    很多旧消息只是寒暄或中间过程，不值得原样保留。

Compaction 的核心思想是：

    把较旧的原始 messages 压缩成 summary

    只保留最近几轮 messages 的原文。

也就是说，compact 不是简单删除历史，而是把旧历史换成更短的表示：

压缩前：stable context + 很长的 session messages
压缩后：stable context + session summary + recent messages

其中 session summary 由 LLM 根据旧消息生成，负责保留长期任务状态；recent messages 原样保留，负责保留最近几轮对话的细节。

生成 session summary 时，需要使用一个专门的 compact prompt。它会把已有 summary 和要压缩的 old messages 交给 LLM，并要求模型输出新的 summary：

已有 summary + old messages + compact prompt -> new summary

这个 compact prompt 的作用是告诉模型：哪些信息要保留，哪些无关内容可以删除，输出要作为后续对话上下文使用。

压缩后的上下文应该仍然能回答“当前任务是什么、已完成什么、用户有哪些明确要求、下一步要做什么”这类问题。
4.3 Compaction 与 Stable Context 的边界

Step 3 已经引入了三类 stable context：

system prompt
memory
soul

这些内容不应该参与 compaction。

原因如下：

system prompt
  是系统规则和行为边界，应该由开发者维护，不能被摘要模型改写。

memory
  是长期记忆，跨 session 生效，应该由 memory store 单独管理。

soul
  是 claw 的稳定身份和交互风格，应该保持稳定，不应该被普通对话压缩改写。

因此，compaction 只处理：

session.summary
session.messages

不处理：

system prompt 配置
soul 配置
memory store

只压缩当前 session 的对话历史。系统规则、长期记忆和稳定风格不是普通聊天记录，不能交给摘要过程改写。
4.4 Session Summary

本 Step 需要为每个 session 增加 summary。

要求：

    summary 属于当前 session，只在当前 session 内生效。

    summary 来自较早的 messages，用于保留长期任务状态和重要上下文。

    summary 会持久化到该 session 的本地数据中。

    summary 不是 memory，不会跨 session 共享。

最近几轮原始 messages 仍然需要保留。它们用于保存最新对话细节，不能全部替换成 summary。
4.5 Compaction 触发策略

Token 是模型处理文本的基本单位，也是上下文窗口限制的来源。精确 token 计算依赖具体模型和 tokenizer；本 Step 可以先使用字符数、消息数量或其他可解释的阈值近似判断。

无论采用哪种策略，都需要满足：

    没有超过阈值时，不触发 compaction。

    触发后，只压缩较旧的 messages。

    最近消息需要原样保留。

    新 summary 需要合并已有 summary 和本次被压缩的旧消息。

    compaction 完成后，需要保存 session。

触发策略应写在 README 或代码注释中，便于验收时理解。
4.6 Summary 质量要求

Compaction 生成的 summary 应保留：

当前任务
已经完成的内容
用户明确提出的要求、偏好和约束
尚未解决的问题
影响继续回答的重要事实

应删除：

寒暄
重复表达
无关细节
没有继续使用价值的中间过程

summary 不需要固定格式，但必须简洁、可读，并适合作为继续对话的上下文。
4.7 Compaction 流程要求

加入 compaction 后，一轮普通对话需要在合适的位置检查是否应压缩。

要求：

    普通 LLM 调用失败时，不追加空 assistant message。

    compaction 调用失败时，不删除旧 messages。

    summary 为空或无效时，不应用本次 compaction 结果。

    compaction 成功后，需要打印压缩结果，并展示更新后的 session summary 或 summary 预览。

    保存失败时，需要提示用户当前 compaction 结果可能没有保存成功。

最重要的原则是：

compaction 失败时，不能删除旧 messages。

否则可能导致 session 历史不可恢复地丢失。
4.8 手动 Compaction 命令

可以选做一个手动命令：

/compact

它用于立即压缩当前 session，方便调试和验收。该命令应由 CLI 直接处理，不作为普通用户消息发送给 LLM。

每次 compaction 成功后，无论是自动触发还是手动触发，程序都需要打印本次压缩结果，并展示更新后的 session summary 或 summary 预览。这样用户才能判断摘要是否保留了关键任务状态，而不是只看到 messages 数量变化。
4.9 最小程序行为

程序运行后，可以在长对话中自动触发 compaction：

$ python -m src.main

claw started. Type /exit to quit.

User> 我们继续完善 claw 的 Lab 文档。
Assistant> 好的，我们继续。

...

[system] compact session session_001: old_messages=28, recent_messages=8
[system] summary:
当前任务：完善 claw 的 Lab 文档。
已完成：Step 0 到 Step 3 的核心能力。
下一步：继续完善 Step 4 的 compaction 行为。

User> 我们刚才完成了哪些 Step？
Assistant> 目前已经完成 Step 0 到 Step 3，并正在完善 Step 4：Compaction。

也可以使用手动命令：

User> /compact
Compacted session session_001.
Old messages: 28
Recent messages: 8
Summary updated: yes
Summary:
当前任务：完善 claw 的 Step 4 文档。
已完成：说明 compaction 的边界、触发策略和失败保护。
下一步：验证 context builder 能正确使用 session summary。
