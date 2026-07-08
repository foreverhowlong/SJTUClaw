# SJTUClaw

SJTUClaw is a minimal agent runtime course project. The current implementation covers Step 0: reading LLM configuration, sending one OpenAI-compatible chat request, and printing the assistant reply.

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

You can also pass a custom prompt:

```bash
uv run python -m claw.cli "你好，请用一句话介绍你自己。"
```

## Test

```bash
uv run pytest
```

The real `.env` file and runtime outputs under `data/` or `logs/` are ignored by Git.
