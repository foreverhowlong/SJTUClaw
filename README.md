# SJTUClaw

SJTUClaw is a minimal agent runtime course project. The current implementation covers Step 1: running an in-memory, multi-turn CLI conversation through an OpenAI-compatible LLM API.

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

The CLI keeps the current conversation history for the lifetime of the process and sends it with every request. Type `/exit` to leave the conversation:

```text
claw started. Type /exit to quit.
User> 你好，我叫小明。
Assistant> 你好，小明！
User> /exit
bye.
```

Session history is currently in memory only and is discarded when the process exits.

## Test

```bash
uv run pytest
```

The real `.env` file and runtime outputs under `data/` or `logs/` are ignored by Git.
