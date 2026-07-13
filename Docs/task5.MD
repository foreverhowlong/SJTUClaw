Step 5：只读 Tool、外部环境反馈闭环与 Agent Loop

在 Step 4 中，我们已经实现了 compaction，使 claw 能够在长对话中压缩旧消息，并通过：

system prompt
soul
memory
session summary
recent session messages

构造可持续增长的上下文。

但是，到目前为止，claw 仍然只能基于已有上下文和模型自身知识回答问题。它还不能主动读取外部环境，例如当前时间、项目目录或文件内容。

本 Step 的目标是为 claw 加入只读 tool，使它能够读取外部环境信息，并将 tool 执行结果反馈回 agent loop。这样，claw 就从“只能聊天的模型调用程序”，升级为“能够观察外部环境的最小 agent”。

需要特别注意：本 Step 只实现只读 tool，不实现写文件、改文件、删除文件、执行危险命令等 update tool。

完成本 Step 后，claw 应该能够根据用户问题主动调用只读 tool，并基于真实 tool result 给出最终回答。
5.1 实验目标

本 Step 需要完成以下内容：

    定义统一的 Tool 数据结构。

    实现 tool registry，用于注册、查找和执行 tool。

    实现至少 3 个只读 tool。

    定义模型发起 tool call 的协议。

    将 agent loop 从“一次 LLM 调用”改造成“LLM -> tools -> LLM”的循环。

    将 tool result 加入 session messages，作为模型继续推理的上下文。

    记录 tool 调用 trace，方便调试。

5.2 为什么需要 Tool

没有 tool 时，claw 只能依赖 prompt 和模型参数中的知识回答问题。例如用户问：

当前项目目录下有哪些文件？

如果没有 tool，模型只能猜测，无法知道真实文件系统状态。

加入 tool 后，流程变成：

用户提问
-> 模型判断需要读取目录
-> claw runtime 调用 list_dir
-> tool 返回真实目录内容
-> 模型基于 tool result 回答用户

这就是最基本的外部反馈闭环：

reason -> act -> observe -> reason

5.3 Tool 基本要求

Tool 是 runtime 暴露给模型的一组可调用能力。模型看到的是 tool 的说明，真正执行的是 runtime 中的 handler。

一个 tool 可以拆成两部分理解：

tool definition
  给模型看的说明，告诉模型这个 tool 叫什么、能做什么、需要什么参数。

tool handler
  runtime 中真正执行的函数，负责读取时间、列目录或读文件，并返回结果。

每个 tool 至少需要表达以下信息：

name
  tool 的唯一名称。模型通过这个名称请求调用 tool。

description
  tool 的能力说明。模型依靠它判断什么时候该调用这个 tool。

input schema
  tool 参数格式。runtime 需要用它检查模型给出的参数是否合理。

handler
  runtime 中真正执行操作的函数。模型不能直接执行 handler。

safety level
  tool 的能力边界。本 Step 中统一为 read_only。

字段命名和实现方式可以自行设计，但必须满足：

    模型能知道有哪些 tool、各自能做什么、需要什么参数。

    runtime 能根据 tool name 找到对应实现。

    runtime 会校验参数，而不是直接信任模型输出。

    runtime 执行 handler 后，会把 tool result 返回给模型继续推理。

    本 Step 中所有 tool 的 safety level 都必须是 read_only。

模型只能提出 tool call，不能自己真正读取文件或执行代码。真正的 tool 执行必须由 claw runtime 完成。
5.4 必须实现的只读 Tool

本 Step 至少实现 3 个只读 tool，建议覆盖以下能力：

读取当前时间
列出目录内容
读取文本文件

具体 tool 名称可以自行设计，但验收时需要能够完成以下问题：

当前时间是多少？
列出当前项目目录。
读取 README.md 并总结项目内容。
讲解这个仓库。

5.5 只读边界

本 Step 中所有 tool 都必须是只读的。

允许实现：

current_time
list_dir
read_file

建议暂不实现以下工具（后续大家在Step-8 中会实现更加复杂的工具，当前实现下面的工具可能会对本地的文件等重要内容产生比较严重的结果，如误删文件）

write_file
edit_file
delete_file
run_shell
install_package
git_commit
send_email
post_message

本 Step 不要求实现目录沙箱、路径白名单或敏感文件过滤。read_file 和 list_dir 只需要把读取结果或错误信息返回给 agent loop。

基础要求：

    文件不存在时返回明确错误。

    文件过大时可以截断或报错，避免一次性塞入过长上下文。

只读边界是本 Step 的唯一强制边界。
5.6 Tool Registry

Tool registry 是 runtime 管理 tools 的地方。它不一定要很复杂，但需要形成一个清晰边界：

tool definition
  给模型看的 tool 说明。

tool handler
  runtime 中真正执行 tool 的函数。

tool registry
  保存 tool definition 和 handler，并根据 tool name 找到对应 tool。

本 Step 中，tool registry 至少需要支持：

注册 tool
列出可用 tool definitions
根据 tool name 执行对应 handler
返回统一格式的 tool result 或错误

这样 agent loop 只需要处理“模型请求了哪个 tool”，不需要把每个具体 tool 的执行细节都写在 loop 里。
5.7 Tool Call 协议

如果使用的 LLM API 支持 function calling 或 tool calling，可以直接使用 API 原生 tool call 格式。

如果使用的 API 不支持 tool calling，也可以让模型输出结构化 JSON。

Tool call 的第一原理是：模型只负责提出“我要调用哪个工具、参数是什么”，runtime 才负责真正执行。执行结果必须再喂回模型，模型才能基于真实 observation 生成最终回答。

单个 tool call 的 JSON 协议可以长这样：

{
  "type": "tool_call",
  "tool": "read_file",
  "args": {
    "path": "README.md"
  }
}

如果同一轮需要读取多个信息，可以一次请求多个 tool call，最多 5 个：

{
  "type": "tool_calls",
  "calls": [
    {
      "tool": "current_time",
      "args": {}
    },
    {
      "tool": "read_file",
      "args": {
        "path": "README.md"
      }
    }
  ]
}

最终回答可以长这样：

{
  "type": "final",
  "content": "README.md 说明这个项目是一个最小 agent runtime。"
}

无论采用哪种方式，都必须满足：

    模型可以表达“请求调用一个或多个 tool”和“输出最终回答”两种结果。

    runtime 能解析模型输出。

    runtime 能区分 tool call 和 final answer。

    模型请求不存在的 tool 或参数错误时，runtime 能返回清晰错误。

    如果模型在 JSON 前后混入解释文本，runtime 应尽量抽取其中合法的协议 JSON。

    不能靠随意字符串匹配判断 tool call。

协议格式可以自行设计，但应在 README 或代码注释中说明，便于验收。
5.8 Agent Loop 改造

在前面的 Step 中，agent loop 大致是：

用户输入
-> buildContext
-> callLLM
-> 保存 assistant 回复
-> 返回

加入 tool 后，需要支持内部循环：

用户输入
-> buildContext
-> callLLM
-> 如果模型返回 final，则结束
-> 如果模型返回 tool_call 或 tool_calls，则执行本轮 tool
-> 将本轮 tool results 加入 session messages
-> 再次 buildContext
-> 再次 callLLM
-> 直到得到 final

不设置整个 agent turn 的总迭代次数上限。限制放在单轮 tool 批量上：模型一次最多请求 5 个 tool call，runtime 执行后把结果反馈给模型，再由模型决定继续调用工具还是输出 final。
5.9 Tool Result 消息

Tool 执行结果需要加入当前 session，作为模型下一轮推理的上下文。

要求：

    tool result 属于 session messages。

    tool 成功和失败都需要反馈给模型。

    tool 失败不等于程序崩溃。

    旧的 tool result 可以作为历史消息被压缩。

    tool definition 本身不参与 compaction。

如果 tool 执行失败，也应该把错误作为 observation 反馈给模型，让模型基于真实错误继续回答用户，而不是假装 tool 成功。
5.10 上下文更新

本 Step 中，在原有上下文之外，加入 tool 使用说明。

原来的上下文结构是：

system prompt
soul
memory
session summary
recent session messages

加入 tool 后，需要让模型知道：

available tools
tool call protocol

如果使用原生 tool calling API，tool definitions 可以通过 API 的 tools 参数传入，而不是拼进 system message。课程实现不强制具体方式，但协议必须清晰。
5.11 最小程序行为

程序运行后，可以看到 claw 主动调用 tool：

$ python -m src.main

claw started. Type /exit to quit.

User> 列出当前项目目录。
[tool_call] list_dir {"path": "."}
[tool_result] ...
Assistant> 当前目录包含若干文件和子目录。

仓库讲解示例：

User> 讲解这个仓库。
[tool_call] list_dir {"path": "."}
[tool_call] read_file {"path": "README.md"}
[tool_call] list_dir {"path": "src"}
...
Assistant> 这个仓库实现了一个最小 agent runtime。入口、LLM client、session store、memory、
