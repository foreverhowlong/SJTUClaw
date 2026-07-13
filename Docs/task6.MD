Step 6：Gateway 与图形化操作入口

到 Step 5 为止，claw 已经具备了最小 agent runtime：它可以维护 session，构造上下文，调用 LLM，执行只读 tool，并把 tool result 反馈回 agent loop。

但是，此时 claw 主要仍然通过 CLI 与用户交互。CLI 适合调试，却不是一个便于操作的交互界面。我们希望能通过网页、QQ/微信/飞书机器人等图形化界面，实现与 claw 的交互。

为了支持与图形化界面交互，本 Step 要引入 Gateway。Gateway 是外部入口和 claw agent runtime 之间的服务层：

    外部图形化界面：负责收集用户输入、展示消息、上传附件。

    Gateway：负责接收外部请求、识别 session、调用已有 agent runtime、返回结果。

    Agent runtime：继续负责上下文构造、memory、compaction、tool 调用和最终回答。

整体工作逻辑可以理解为：

图形化入口发送请求
-> Gateway 接收请求
-> Gateway 找到或创建 session
-> Gateway 调用已有 agent loop
-> agent loop 复用 context、memory、compaction 和 tool
-> Gateway 把 assistant 回复或错误返回给入口

Gateway 不是新的 agent。它只是把原来只能从 CLI 进入 claw 的能力，变成可以被图形化入口等外部入口调用的服务接口。

本 Step 需要在已有 session、context builder、memory、compaction、tool registry 和 agent loop 之上，实现 Gateway server、至少一个图形化操作入口、session 管理，以及和 session 绑定的附件上传能力。
6.1 Gateway Server

Gateway 是一个可以长期运行的 server，用来承接外部入口和 claw runtime 之间的通信。

本 Step 需要实现：

    Gateway 可以独立启动，并在运行期间持续接收外部请求。

    Gateway 提供清晰的通信协议，可以选择 HTTP、WebSocket、SSE 或其他本地通信方式。

    Gateway 能够根据请求中的 session 信息找到或创建 session。

    Gateway 将外部请求转交给已有 agent runtime 处理。

    Gateway 返回 assistant 回复、session 信息和错误信息。

    单次请求失败不能导致 Gateway 进程退出。

如果实现流式输出，也需要说明事件含义，例如 assistant 正在生成、tool 正在执行、最终回复完成或请求失败。
6.2 图形化操作入口

本 Step 至少需要实现一个图形化操作入口，用来验证 Gateway 不再依赖终端 stdin/stdout。

这个入口可以是网页，也可以是桌宠、桌面客户端、QQ/微信/飞书机器人或其他有图形化交互能力的入口。

图形化入口至少需要支持：

    输入用户消息。

    发送消息到 Gateway。

    展示当前 session 的历史消息。

    展示 assistant 回复。

    展示已有 session 列表。

    创建新 session。

    切换 session。

    展示 Gateway 返回的错误。

图形化入口必须满足：

    图形化入口不能保存或暴露 LLM API Key。

    图形化入口中的消息发送必须经过 Gateway。

    切换 session 后，需要展示对应 session 的历史消息。

    创建新 session 后，后续消息应进入新 session。

图形化入口的具体形态、技术栈、交互风格、是否使用流式输出，都由学生自行决定。简单实现可以做成网页；有余力的同学也可以做成桌宠，或接入飞书、微信、QQ 等平台。课程只要求它能作为一个真实外部入口，完整调用自己的 claw。
6.3 Agent Runtime 复用

Gateway 接入后，CLI 和图形化入口都应该复用同一套 agent runtime。

Gateway 只能调用已有 agent loop，不能单独调用底层 LLM client。

调用形式可以类似：

runAgent(sessionId, userMessage, ...)

具体函数签名可以自行设计，但需要满足：

    CLI 和 Gateway 都能调用同一套 agent loop。

    Gateway 不绕过 context builder。

    Gateway 不绕过 session store。

    Gateway 不绕过 tool registry。

    Gateway 调用后产生的 user、assistant 和 tool messages 需要进入同一个 session 历史。

    Gateway 调用后仍然触发已有的 compaction、memory 和 tool 行为。

也就是说，从图形化入口发出的消息应该和从 CLI 发出的消息一样，进入同一条 agent runtime 路径。
6.4 Session 管理

Gateway 需要处理外部入口和 session 之间的关系。

本 Step 需要支持：

    如果请求带有 sessionId，则使用对应 session。

    如果 sessionId 不存在，需要返回错误或创建新 session，并说明策略。

    如果请求没有 sessionId，可以使用默认 session 或创建新 session。

    图形化入口需要能列出已有 session。

    图形化入口需要能创建 session。

    图形化入口需要能切换当前 session。

    图形化入口切换 session 后，只能展示当前 session 的消息历史。

需要注意：

session 的真实历史仍然由 session store 管理。
Gateway 可以路由 session，但不应该另存一份独立聊天历史。

如果用户在 CLI 中创建过 session，图形化入口中也应该能看到这些 session。
6.5 附件上传与 Session 隔离

本 Step 需要让图形化入口可以向 Gateway 上传附件，以增强 Gateway 的输入能力和 session 的附件能力。

这里的附件上传不是 workspace 文件管理。附件属于当前 session，用来保存用户从图形化入口提交的材料；它不代表用户项目目录，也不代表 agent 可以修改 workspace 文件。

本 Step 需要支持：

    图形化入口可以把附件上传到 Gateway；如果选择的入口平台不适合直接上传文件，需要通过 Gateway 提供等价的附件提交或关联方式，并在实现说明中写清楚。

    Gateway 在 claw 运行的服务端为附件准备专门的存储位置，例如 data/、data/attachments/ 或类似目录。

    附件必须和 session 绑定。

    当前 session 只能看到或列出自己绑定的附件 metadata，例如文件名、大小、类型和上传时间。

    一个 session 不能看到另一个 session 的附件 metadata。

推荐的存储结构可以是：

data/
  sessions/
    <sessionId>/
      attachments/
        <attachmentId-or-safe-file-name>

也可以使用其他结构，但必须保证附件和 session 的绑定关系。

如果希望 agent 后续能够使用用户上传的附件，需要先把附件 metadata 记录到 session 或 session 附件索引：

用户上传附件
-> Gateway 将附件保存到当前 session 的附件区
-> Gateway 将附件 metadata 记录到 session 或 session 附件索引
-> agent loop 通过明确的上下文看到当前 session 有哪些附件

注意：图形化入口不能为了读取附件而绕过 Gateway 直接访问服务端文件系统。Gateway 接入不应该新增修改用户项目文件、删除用户项目文件、执行 shell 命令等修改外部环境的能力。这些能力不属于本 Step 的必做内容。
