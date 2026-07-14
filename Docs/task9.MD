Step 9：Skill System

到 Step 8 为止，claw 已经可以在用户指定的 workspace 中读取文件、修改文件、运行命令，并通过 approval 控制真实环境中的写操作。

本 Step 要让 claw 拥有一套 Skill system。Skill 是面向某类任务的可复用能力包，用来保存稳定的工作方法，例如指令、模板、检查清单、示例和参考资料。

很多重复任务都会反复用到类似内容：

某类文档应该有哪些结构。
某类材料应该如何整理和总结。
某类输出应该使用什么模板。
生成结果后应该检查哪些问题。

这些内容不适合全部写进 system prompt。system prompt 应该保持通用，而 skill 只在任务需要时进入当前 agent loop。

本 Step 需要引入三个核心概念：

    Skill: 一组可复用的任务说明、模板和参考资料。

    Skill Registry: 负责扫描本地 skills 目录，维护可用 skill 列表。

    Skill Selection: 让用户可以显式调用 skill，也让模型可以根据任务自主选择 skill。

9.1 Skill

Skill 可以理解为“某一类任务的工作手册”。它不替代 system prompt，也不替代 memory。

它们的边界是：

    system prompt: 定义 claw 的通用行为规则。

    memory: 保存跨 session 的长期事实和偏好。

    skill: 保存某类任务的可复用工作方法。

    session messages: 保存当前对话的具体过程。

本 Step 可以使用本地目录表示 skill。一个 skill 至少包含一个 SKILL.md，也可以包含一些别的资源，例如：

skills/example-skill/
  SKILL.md 
  assets/template.md
  references/checklist.md

SKILL.md 至少需要表达：

    name: skill 名称，例如 example-skill。

    description: skill 适合处理什么任务，以及哪些用户请求应该考虑使用它。

    instructions: 使用该 skill 时需要遵守的详细步骤。

可以使用类似下面的格式：

**---**
name: example-skill
description: 说明这个 skill 能处理什么任务，以及用户在什么情况下应该使用它。
**---**

**# Example Skill**

这里写具体工作流程、输出要求、注意事项和资源说明。

assets 和 references 目录不是强制格式。是否需要这些目录，取决于这个 skill 是否需要模板、示例、检查清单或参考资料。

完整 skill 内容只有在 skill 被选中后才加入上下文。未被选中的 skill 不应该整篇塞进每次 LLM 请求。
9.2 Skill Registry

Skill registry 负责管理本地可用 skill。

本 Step 需要支持：

    扫描本地 skills/ 目录。

    识别每个 skill 的名称、描述和适用场景。

    列出所有可用 skill。

    根据名称找到指定 skill。

    加载指定 skill 的 SKILL.md 和相关资源。

    生成一份轻量 skill 索引，供模型判断是否需要使用 skill。

轻量 skill 索引只应该包含短信息，例如：

name: example-skill
description: 说明这个 skill 能处理什么任务，以及用户在什么情况下应该使用它。

Registry 不负责执行任务。它只负责告诉 runtime：

有哪些 skill。
每个 skill 适合什么任务。
如何加载某个 skill 的完整内容。

真正的任务执行仍然由已有 agent loop 完成。
9.3 Skill 调用方式

Skill 需要支持两种调用方式。

第一种是用户显式调用：

/skill <skill-name> <task>

例如：

/skill course-report 帮我写一份 2000 字的思政课读书报告草稿，主题是《乡土中国》的现代意义，参考材料在 workspace 的 notes.md 中，保存为 reports/xiangtu.md。

这种情况下，runtime 直接根据 skill-name 加载对应 skill，并把 skill 内容和用户任务一起交给 agent loop。

第二种是模型自主选择。

例如用户直接输入：

帮我写一份 2000 字的思政课读书报告草稿，主题是《乡土中国》的现代意义，参考材料在 workspace 的 notes.md 中，保存为 reports/xiangtu.md。

用户没有写 /skill course-report，但这个任务明显适合 course-report。context builder 可以把轻量 skill 索引放入本轮上下文，让模型判断是否需要使用某个 skill。

一种简单实现方式是：

    普通消息进入 agent loop 前，context builder 提供可用 skill 的轻量索引。

    模型判断是否需要使用 skill。

    如果需要，模型通过明确结构或内部 tool 表达“使用某个 skill”的意图。

    Runtime 根据 skill 名称加载完整 skill 内容。

    Agent loop 带着 skill 内容继续完成任务。

模型自主决定使用 skill 之前必须向用户发送一个 approval 信息，以让用户知道 claw 使用了 skill 的能力。
9.4 Agent Loop 接入

Skill system 不能绕过已有 runtime。

Skill 被显式调用或被模型自主选中后，需要把以下内容加入当前 agent loop：

    skill 名称

    skill 描述

    skill 适用场景

    skill 指令

    本次用户任务

    需要使用的模板、检查清单或参考资料

    调用来源：explicit 或 auto

Skill 调用后仍然需要复用已有能力：

session store
context builder
memory
tool registry
workspace
approval
compaction

Skill 只是为本轮任务补充上下文和工作方法，不是另一套 agent。Skill 如果需要生成文件，仍然应该通过 Step 8 的 update tool 发起文件写入，并经过 approval。

Skill 使用记录也需要保存到 session 中，至少包含：

    使用了哪个 skill

    所属 session

    用户任务

    调用来源是 explicit 还是 auto

    如果是 auto，模型为什么选择该 skill

    使用时间

    最终输出或保存路径

9.5 需要实现的 Skills

为了验证 skill registry、显式调用和模型自主选择，本 Step 至少需要实现 3 个 skill。

其中必须包含 course-report。它只是本 Step 用来验证 skill system 的一个具体例子，不是新的概念。

course-report 面向大学课程报告场景，例如：

课程小论文
课程学习总结
课程实验报告

这个 skill 至少需要包含 SKILL.md，并说明它用于生成课程报告草稿。可以额外提供报告模板或检查清单，但不强制规定具体文件名。

使用这个 skill 时，claw 需要能根据用户给出的课程要求、主题、字数、参考材料或保存路径，生成一份结构化 Markdown 草稿，并通过已有 workspace update tool 和 approval 流程保存到 workspace。

另外两个 skill 可以自行设计，也可以选择：

    material-summary: 汇总 workspace 中的学习材料、课堂笔记或调研资料。

    presentation-outline: 根据主题和材料生成课堂展示大纲、讲稿提纲或 PPT 页面结构。

无论选择什么，每个 skill 都需要有清晰的 description，说明它能做什么以及什么时候应该使用，否则模型无法可靠地自主选择。
9.6 CLI 与图形化入口

CLI 至少需要支持：

    /skill list: 查看可用 skill。

    /skill show <skill-name>: 查看某个 skill 的基本信息。

    /skill <skill-name> <task>: 显式调用 skill。

    /skill usage: 查看当前 session 的 skill 使用记录。

前面 Step 已经实现图形化操作入口，该入口也需要能使用 skill：

    查看可用 skill 列表。

    查看 skill 描述和适用场景。

    选择某个 skill 并输入任务。

    在普通聊天中允许模型自主选择 skill。

    展示本轮是否使用 skill，以及使用原因。

    继续使用已有 approval 流程批准报告草稿保存。
