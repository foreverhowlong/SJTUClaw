# SJTUClaw

SJTUClaw is a minimal agent runtime course project. The current implementation covers Step 3: persistent sessions plus stable system prompt, soul, and cross-session memory context in a multi-turn CLI conversation through an OpenAI-compatible LLM API.

## Setup

1. Install `uv`.
2. Copy `.env.example` to `.env`.
3. Fill in `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL`.
4. Let `uv` create the pinned Python environment:

```bash
uv sync --dev
```

Example:

```env
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
```

## Run

```bash
uv run python -m claw.cli
```

The CLI restores the most recently updated session on startup. The CLI owns that
current-session pointer; every agent turn receives its `sessionId` explicitly.
Type `/exit` to leave the conversation:

```text
claw started. Type /exit to quit.
User> 你好，我叫小明。
Assistant> 你好，小明！
User> /exit
bye.
```

## Session commands

Session commands are handled locally and are never sent to the LLM:

```text
/session new
/session list
/session switch <sessionId>
/session rename <sessionId> <title>
/session delete <sessionId>
```

Use `/help` to show all commands. Unknown slash commands produce a local error;
prefix a message with `//` to send a literal leading slash.

Each session is stored independently under `data/sessions/<sessionId>/` using
`meta.json` and an append-only `messages.jsonl`. A completed user/assistant turn
is committed as one revisioned record. Stale revisions are rejected instead of
overwriting newer history. Invalid or corrupt files produce a visible error
instead of being replaced silently.

## Stable context and memory

Stable context is assembled before the current session history on every LLM request:

1. `claw/prompts/system_prompt.md` defines runtime rules and behavior boundaries.
2. `claw/prompts/soul.md` defines Claw's stable identity and interaction style.
3. Manually managed memories provide long-term facts and preferences across sessions.

System prompt and soul changes take effect after restarting the CLI. Memory commands are handled locally and are never sent to the LLM as user messages:

```text
/memory add <content>
/memory list
/memory delete <memoryId>
```

Each memory is stored as a readable Markdown file under `data/memory/`. Step 3 only supports explicit, manual memory updates; ordinary conversation cannot rewrite stable context.

## Runtime paths

When running from a source checkout, `.env` and `data/` are resolved from the
repository root regardless of the current working directory. An installed wheel
uses `~/.sjtuclaw` by default. Set `CLAW_HOME` to choose a different runtime root.

The default prompts are packaged with `claw`. Optional prompt overrides can be
provided with `CLAW_SYSTEM_PROMPT` and `CLAW_SOUL`; both variables take absolute
or user-relative file paths.

## Test

```bash
uv run pytest
```

The real `.env` file and runtime outputs under `data/` or `logs/` are ignored by Git.
