Step 7：Scheduler 与定时任务

在 Step 6 中，claw 已经通过 Gateway 支持了图形化操作入口或其他外部入口调用。外部入口可以把用户消息交给同一套 agent runtime，从而复用 session、memory、compaction、tool 和 agent loop。

但此时 claw 仍然主要是“被动运行”的：只有当用户发来一条消息时，agent loop 才会被调用。很多真实 agent 场景还需要 claw 能在未来某个时间点，或按照某种重复规则自动开始工作。例如：

明天上午 9 点提醒我整理实验报告。
今晚 10 点总结一下今天的 session。
每隔一段时间检查某个信息源，并把结果记录下来。
每天晚上生成一次当天工作总结。

本 Step 要引入 Scheduler。Scheduler 是 claw runtime 外围的长期运行服务，负责保存和管理用户创建的定时任务，并在任务到期时调用已有 agent loop。

可以把定时任务理解为：未来由 Scheduler 自动发送给 claw 的一条用户消息。

Scheduler 不是新的 agent，也不是新的对话系统。它只负责“什么时候触发”和“触发后如何记录结果”；真正的上下文构造、memory、tool 调用、compaction 和最终回答，仍然由已有 agent runtime 完成。
7.1 Scheduler

Scheduler 解决的问题是：当没有新的外部消息到来时，claw 如何在未来自动开始工作？

Gateway 和 Scheduler 都属于 claw runtime 外围的服务模块，但它们的触发来源不同：

    Gateway: 由外部用户消息触发，入口通常是图形化入口、CLI、Webhook 或聊天软件。

    Scheduler: 由时间触发，入口是已经保存的任务计划。

它们最终都应该进入同一套 agent runtime：

图形化界面消息 -> Gateway   -> agent loop
定时任务 -> Scheduler -> agent loop

本 Step 需要支持：

    Scheduler 可以长期运行，并持续检查是否有任务到期。

    Scheduler 可以在程序重启后恢复未完成任务。

    Scheduler 可以更新任务状态，并记录每次执行结果。

    Scheduler 可以触发一次性任务和周期性任务。

    Scheduler 到期执行任务时，必须调用已有 agent loop。

    Scheduler 不应该绕过 session store、context builder、tool registry、memory 或 compaction。

Scheduler 可以和 Gateway 运行在同一个进程中，也可以是独立进程。课程不限制具体部署方式，但需要说明它如何启动、如何访问任务数据，以及如何调用 agent runtime。
7.2 定时任务

定时任务是 Scheduler 管理的基本对象。它表示一条未来要交给 agent runtime 执行的用户指令。

定时任务至少需要表达：

    任务内容: 到期后要交给 agent loop 的用户指令。

    触发计划: 任务应该在什么时候开始执行，以及是否需要重复执行。

    下一次触发时间: Scheduler 下次应该检查并执行这个任务的时间。

    任务状态: 用于区分等待中、执行中、已完成、已取消或执行失败等情况。

    所属 session: 任务执行时应该进入哪个 session。

    执行历史: 任务每次执行后的 assistant 回复、错误信息或执行摘要。

具体字段名、存储结构和 ID 格式由学生自行设计。关键是任务必须是一个可管理对象，而不是一段临时 sleep 逻辑。

本 Step 需要支持两类任务：

    一次性任务: 用户指定一个未来时间点，任务到期后执行一次，执行完成后不再自动重复。

    周期性任务: 用户指定一个重复规则，任务按照该规则多次触发，每次触发都会调用 agent loop，并留下执行历史。

周期性任务不要求实现完整 cron 表达式或复杂日历规则。可以支持固定间隔、每天固定时间，或其他能清晰说明的周期规则。关键是周期性任务必须真的能够重复触发，而不是只保存一个文本说明。

周期性任务需要定义清楚：

    如何描述重复规则。

    上一次执行失败后是否继续下一次。

    任务执行时间长于间隔时如何处理。

    用户取消时是否取消未来所有触发。

    程序关闭期间错过的触发是否补执行。

这些边界不要求采用某一种固定答案，合理即可。
7.3 任务管理与持久化

本 Step 需要实现任务创建、任务列表、任务取消和任务持久化。

创建任务时必须提供：

    任务内容。

    触发时间或重复规则。

    所属 session。

如果时间无法解析、重复规则无效或 session 不存在，需要返回错误，不能创建任务。

任务列表必须展示：

    任务内容。

    任务类型（一次性任务 or 周期性任务）。

    重复规则。

    下一次触发时间。

    任务状态。

    所属 session。

    创建时间或更新时间。

任务历史必须保存每次执行的结果或失败原因。周期性任务不能只保留最后一次结果。

取消任务需要满足：

    一次性任务取消后不再触发。

    周期性任务取消后不再产生未来触发。

    任务列表或历史中能看出任务已取消。

任务必须持久化。程序重启后，未完成任务、任务状态、重复规则、下一次触发时间和执行历史都不能丢失。

存储方式由实现自行决定，但不能破坏已有 session 数据结构，也不能让 Scheduler 另存一份完整聊天历史。
7.4 Agent Loop 接入

任务到期后，Scheduler 需要把任务内容交给已有 agent loop，而不是直接调用底层 LLM client。

整体流程可以理解为：

Scheduler 发现任务到期
-> 将任务标记为执行中
-> 把任务内容作为用户消息交给 agent loop
-> agent loop 使用已有 context builder、memory、tool 和 compaction
-> 保存 assistant 回复或错误信息
-> 更新任务状态

本 Step 需要满足：

    任务执行必须进入对应 session 历史。

    后续对话可以看到定时任务产生的消息和结果。

    任务执行应复用 context builder、system prompt、soul、memory、session summary 和 recent session messages。

    任务执行应复用已有 tool registry 和 tool trace。

    任务执行产生较长历史时，仍然遵守已有 compaction 规则。

    任务失败需要被记录，而不是被 Scheduler 静默吞掉。

    周期性任务执行完成后，需要根据重复规则计算下一次触发时间。

定时任务需要有明确的 session 归属。创建任务时可以使用当前 session、用户显式选择的 session，或实现中定义的专门 scheduler session；无论采用哪种策略，用户都必须能清楚知道任务属于哪个 session，以及执行结果写回了哪里。
7.5 图形化入口

Step 6 已经要求实现图形化操作入口。本 Step 需要在该入口中增加基本任务管理能力。

图形化入口至少需要支持：

    创建一次性定时任务。

    创建周期性定时任务。

    查看任务列表。

    查看任务状态。

    查看任务执行结果。

    查看周期性任务的重复规则和下一次触发时间。

    取消任务的未来触发。

    看到任务所属 session。

图形化入口不需要做复杂日历组件或完整任务看板。重点是验证 Scheduler 能通过真实入口被管理，并且任务到期后能够进入已有 agent runtime。

所有任务创建、查询、取消和执行结果读取，都应该通过服务端接口完成。Gateway 和图形化入口不应该自己执行定时任务，它们只负责把任务管理请求交给服务端，并展示 Scheduler 保存的状态和结果。
