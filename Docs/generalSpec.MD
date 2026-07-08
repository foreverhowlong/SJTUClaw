SJTUClaw 功能要求汇总

SJTUClaw 是一个最小 agent runtime。用户可以通过 CLI 或图形化操作入口与它对话。claw 需要维护会话、构造上下文、调用 LLM，并在需要时读取环境、执行任务、保存结果。

具体功能要求如下：

    实现基础 LLM 调用。程序应能从 .env 或环境变量读取配置，使用通用 messages 结构向模型发送请求并打印 assistant 回复；API KEY 不能写死或提交到仓库中，配置缺失、网络失败和响应异常需要有清晰提示。

    支持 CLI 多轮对话。用户可以在终端连续输入消息，程序每轮都把当前 session 的历史上下文发送给 LLM，并把 user 输入和 assistant 回复写回会话历史；普通退出、中断和调用失败需要能基本处理。

    支持多 session 管理。用户可以创建、列出和切换 session；不同 session 的历史相互隔离，并能持久化到本地文件。/session 等内部命令由 runtime 处理，不应作为普通消息发送给 LLM。

    上下文管理。支持 system prompt、soul、memory 和普通 session 历史等上下文的管理；system prompt 与 soul 从独立配置加载，memory 用于保存跨 session 的长期事实或用户偏好，并支持手动添加、查看和删除。

    实现长对话压缩。session 历史过长时，claw 应能把较早消息压缩成当前 session 的 summary，并保留最近几轮原始消息；压缩只处理当前 session 的 conversation context，失败时不能丢失原始历史。

    实现工具调用：claw 必须实现工具调用以支持模型与环境交互。模型通过明确协议请求 tool call，runtime 真实执行 tool，把结果写回 session，再让模型基于 observation 继续回答。claw 至少提供获取当前时间、列出目录、读取文本文件 3 个只读 tool，以及后续提到的 advanced tool。

    提供 Gateway 与图形化操作入口。Gateway 作为外部入口和 agent runtime 之间的服务层；至少实现一个非 CLI 的图形化入口，可选择网页、桌宠、桌面客户端、QQ/微信/飞书机器人等形态。图形化入口至少支持发送消息、展示历史、展示错误、列出/创建/切换 session 和上传或关联附件；图形化入口不能保存或暴露 LLM API KEY，附件 metadata 必须按 session 隔离。

    支持定时任务。用户可以创建一次性任务和周期性任务，查看任务列表、状态、执行历史，并取消未来触发；任务需要保存内容、计划、下一次触发时间、状态、所属 session 和执行历史，到期后仍应调用已有 agent loop，结果写回对应 session。

    支持 workspace、advanced tool 和 approval。用户可以设置当前 workspace 用于 claw 工作的场所和边界；claw 可以在 workspace 内创建或修改文件、启动 shell 运行命令等，并支持 workspace 和外部入口的文件交互（advanced tools）。写文件和运行命令前必须创建 approval，用户批准后才执行，拒绝或执行结果都需要进入 session 历史。

    实现 skill system。claw 应能扫描本地 skills/ 目录，列出可用 skill，并在用户显式调用或模型判断需要时加载对应 SKILL.md 及相关资源；至少实现 3 个 skill，其中必须包含 course-report，用于生成课程报告类 Markdown 草稿，并通过 workspace update tool 与 approval 流程保存结果。

    复用同一套 runtime。CLI、Gateway、图形化入口、Scheduler、workspace tool 和 skill 都必须复用同一套 session store、context builder、memory、compaction、tool registry 和 agent loop，不能各自绕过底层逻辑单独调用 LLM 或直接修改状态。

更细的实现步骤和要求见后续内容
