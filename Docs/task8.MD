Step 8：Workspace、Advanced Tool 与 Approval

到 Step 5 为止，claw 已经可以通过只读 tool 观察外部环境，例如读取当前时间、列目录、读文件。

本 Step 要让 claw 从“只能观察环境”变成“可以在用户指定项目中执行真实操作”的 agent runtime。需要引入三个核心概念：

    Workspace: 当前 agent 可以操作的项目目录。

    Advanced Tool: 在实现 workspace 进行隔离后可以实现的高级 tool 操作，包括修改文件，执行脚本，将文件发送到远端等，能支持 claw 实现更高级的功能。

    Approval: 在执行会改变环境的操作前，先让用户确认。

其中 Advanced Tool 要求至少实现以下三类：

    Update Tool: 能够在 workspace 内创建或修改文件。

    Shell Tool: 能够启动 shell，并在 workspace 内执行命令或脚本。

    Download Tool: 能够为 workspace 内文件创建可通过 Gateway 下载的临时入口，以支持远端通过 gateway 获取服务端的文件。

本 Step 需要在已有 tool registry、session、context builder 和 agent loop 上增加这些能力。
8.1 Workspace

Workspace 是当前 agent session 的项目根目录，也是文件读写、shell 命令、下载入口创建和 approval 判断的默认边界。

用户说：

帮我修改 README.md。
运行测试脚本。

runtime 需要知道这些操作发生在哪个项目中。这个项目目录就是 workspace。

本 Step 需要支持：

    用户可以设置当前 workspace。

    用户可以查看当前 workspace。

    文件读取、文件修改、shell 命令和下载入口创建默认都发生在 workspace 内。

    tool 参数中的相对路径需要按 workspace 解析。

    不允许通过 ../ 或绝对路径绕过 workspace 边界。

    需要实现 copy_attachment_to_workspace tool，将 Step 6 中通过 Gateway 上传并绑定到当前 session 的附件，拷贝到 workspace 内指定路径，以支持 claw 像操作普通 workspace 文件一样读取或处理这些附件。

    copy_attachment_to_workspace 只能访问当前 session 绑定的附件，不能读取其他 session 的附件；目标路径必须按 workspace 解析，且不能写到 workspace 之外。

    如果 workspace 未设置，涉及文件修改、命令执行、附件拷贝或下载入口创建的请求不能直接执行。

Workspace 可以绑定到当前 session，也可以作为 runtime 的当前配置；具体实现方式由学生自行决定，但用户必须能清楚知道 claw 当前正在操作哪个目录。
8.2 Update Tool

Update tool 用来把模型的文件修改意图转化为真实的 workspace 文件变化。

本 Step 需要至少实现以下 update tool：

    create_file: 在 workspace 内创建新文件。

    overwrite_file: 用新内容覆盖 workspace 内已有文件。

    edit_file: 根据用户要求更新 workspace 内已有文件的部分内容。

如果实现时希望使用一个统一 tool，也可以设计成：

    update_file: 通过 operation 参数区分 create、overwrite 和 edit。

无论使用多个 tool 还是一个统一 tool，都必须覆盖创建文件、覆盖文件和更新文件内容三类能力。

Update tool 的执行结果需要返回给 agent loop，至少包含：

    是否成功

    执行了哪个 tool

    影响了哪个路径

    简要结果或错误信息

Update tool 只能修改 workspace 内的文件，不能修改 workspace 外的文件，如果 tool 参数的路径在 workspace 之外，需要直接返回执行失败。
8.3 Shell Tool

Shell tool 用来让模型通过 runtime 控制一个 shell，并在 workspace 中运行命令或脚本，Shell tool 是实现 agent 自动化运行代码进行测试的重要环节。

本 Step 必须实现两个 shell tool：

    new_shell: 启动一个新的 shell；如果之前已经启动过 shell，旧 shell 必须先退出；新 shell 的初始工作目录必须是 workspace，或 workspace 内的子目录。

    run_command: 在当前已经启动的 shell 中运行一条命令；多次 run_command 应该复用同一个 shell；前序命令配置的 cwd、环境变量或 source 结果，可以影响后续命令；如果当前没有 shell，需要返回明确错误，提示先调用 new_shell。

run_command 的执行结果需要返回给 agent loop，至少包含：

    是否成功

    执行的命令

    执行时的 cwd

    退出码

    stdout

    stderr

    是否超时

    输出是否被截断

    错误信息

Shell tool 只能在 workspace 内执行命令。执行前和执行后都需要确认 shell 的当前工作目录仍然位于 workspace 内；如果 shell 离开 workspace，runtime 应该终止当前 shell 并返回错误。
8.4 Download Tool

Claw 在 workspace 中对文件进行修改后会得到输出的文件，但如果用户是通过网页等远端通过 Gateway 和 claw 交互，则无法获取这些输出文件。Download tool 作用是把 workspace 内已有文件注册成一个可以由 Gateway 下载的临时链接，使得远端通过 Gateway 访问的用户可以获得 workspace 中的文件。

本 Step 需要实现 create_download，它为 workspace 内一个已有文件创建临时下载入口。

create_download 需要接收，要下载的文件的路径。

Runtime 执行 create_download 时，只需要在 workspace 中找到文件，并向 Gateway 注册一个可下载的临时入口。Gateway 可以把这个入口表现为 downloadId 或 downloadUrl，再交给图形化入口展示成下载按钮、下载链接或对应平台支持的文件获取方式。

create_download 不负责把文件内容返回给模型。模型只需要知道下载入口已经创建，或创建失败的简要原因。
8.5 Approval

Update tool 和 shell tool 都可能改变用户项目或运行环境，因此不能像只读 tool 一样直接执行。

当模型请求执行 update tool 或 shell tool 时，runtime 需要先创建 approval 请求，并暂停这次 tool 执行。用户批准后才真正执行；用户拒绝后不能执行。

Download tool 不需要进入这个显式 approval 流程；它的用户确认发生在前端点击下载入口时。

Approval 请求需要把这次模型请求的 tool call 表达清楚：

    approvalId: 用于批准或拒绝这次请求。

    tool: 请求执行的 tool 名称。

    本次 tool 执行对应的参数（update tool 的文件和对应的内容，shell tool 需要执行的命令等）

Approval 的结果也需要进入 agent loop：

    用户批准: runtime 执行对应 tool，并把 tool result 写入 session 历史。

    用户拒绝: runtime 不执行 tool，并允许用户输入拒绝的原因以指导模型继续回答。

这样模型才能知道操作是否真实发生，并基于真实结果继续回答。
8.6 Agent Loop 接入

本 Step 的内容需要继续接入 agent loop，并要求能在 CLI 交互和图形化入口交互中，都能设置 workspace、审批 approval。下载入口仅需在图形化或其他远端入口中展示即可，CLI 不需要实现下载按钮、下载链接或下载入口展示；使用飞书、微信或 QQ 等入口的同学请自行探索如何实现从服务端下载文件。

需要满足：

    模型仍然通过 tool call 表达文件修改、命令执行或创建下载入口的意图。

    Update tool、shell tool 和 download tool 都注册到已有 tool registry。

    Runtime 在执行 update tool 和 shell tool 前必须创建 approval。

    Runtime 执行 create_download 前不需要创建显式 approval，成功后把下载入口交给 Gateway 并与图形化界面展示。

    Runtime 负责等待用户决定，并在批准后执行需要 approval 的 tool。

    Tool result、approval 拒绝结果和错误信息都需要进入 session messages，使得后续 LLM 调用能够看到这些结果

    Gateway 和图形化入口不能直接改文件，也不能直接执行命令；它们只负责展示 approval、收集用户决定和展示下载入口。

    原有只读 tool、memory、compaction 和 context builder 需要继续可用。

整体流程可以理解为：

用户请求
-> agent loop 调用 LLM
-> LLM 请求 update tool、shell tool 或 download tool
-> 如果是 update tool 或 shell tool:
   -> runtime 创建 approval
   -> 用户批准或拒绝
   -> runtime 执行 tool，或记录拒绝结果
-> 如果是 create_download:
   -> runtime 创建短期下载入口
   -> Gateway 将 downloadId 或 downloadUrl 发给前端
   -> 用户点击下载入口后，Gateway 返回文件内容
-> tool result / approval result 回到 agent loop
-> LLM 基于真实结果继续回答

