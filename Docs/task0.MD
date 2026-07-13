Step 0：环境准备与 LLM API 接入

本 Step 的目标是完成 claw 的最小运行闭环：程序能够读取配置，向 LLM 发送一条消息，并在终端打印模型回复。

完成后，你应该能用一个固定命令启动程序，看到一次真实的模型返回。
0.1 实验目标

本 Step 需要完成以下内容：

    在 Windows、WSL 或 macOS 上搭建开发环境。

    创建 claw 项目的基础工程结构。

    配置 LLM API KEY，并确保密钥不会进入 Git。

    实现一次最小 LLM API 调用。

    对 API KEY 缺失、网络请求失败、HTTP 异常、响应格式异常等常见错误给出清晰提示。

本 Step 只关注一个最小闭环：配置正确、请求发出、响应解析成功、结果打印出来。
0.2 开发环境

推荐使用以下环境之一：

WSL + Ubuntu 22.04 或更新版本
macOS

需要安装的基础工具包括：

Git
Python

本课程推荐使用 Python 实现。课程不强制具体语言，但要求工程结构清晰、模块边界明确。
0.3 工程结构要求

你需要建立一个可以持续扩展的最小项目结构。具体目录和文件名可以自行设计，但至少应体现以下职责边界：

程序入口
配置读取
LLM API 调用
运行数据或日志目录

0.4 API KEY 配置

真实配置可以来自 .env 或系统环境变量。项目中应提供 .env.example，说明需要哪些配置项，例如：

LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

.gitignore 中必须包含真实密钥文件和运行产物，例如：

.env

真实 API KEY 只能保存在 .env 或环境变量中。不能把 API KEY 写死在代码里。不能把 .env 提交到 Git。

警告：不要在代码、README、提交记录、Issue、截图或公开仓库中的任何位置暴露 API KEY。公开仓库通常会被自动扫描，一旦 API KEY 泄漏，可能导致账号被盗用、额度被消耗或服务被封禁。
0.5 裸 API 调用要求

本 Step 需要实现一个最小 LLM 调用能力：

输入：一组 messages
输出：assistant 的回复文本

大多数 Chat Completions / Messages API 都使用 messages 作为输入。messages 是一个数组，每一项代表上下文中的一条消息：

[
  {
    "role": "user",
    "content": "你好，请用一句话介绍你自己。"
  }
]

其中 role 表示这句话是谁说的，content 表示消息正文。本 Step 至少需要理解以下角色：

system
  系统指令，用来规定模型的行为边界和回答风格。

user
  用户输入的问题或请求。

assistant
  模型返回的回复。

本 Step 的最小请求可以只包含一条 user message。也可以加入一条简短的 system message，但不是必须。

示例输入：

你好，请用一句话介绍你自己。

示例输出：

你好，我是一个可以帮助你完成编程、写作和学习任务的 AI 助手。

0.6 最小程序行为

程序运行后，应完成以下动作：

读取配置
检查必要配置是否存在
构造一条 user message
调用 LLM API
打印 assistant 回复

启动命令需要固定，例如：

python -m src.main

实际命令可以按你的工程结构调整，但应写在 README 中。
