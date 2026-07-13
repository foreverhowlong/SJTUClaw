# SJTUClaw

SJTUClaw is a minimal agent runtime course project. The current implementation covers Step 6: persistent multi-session context, safe compaction, streamed OpenAI-compatible tool calling, a read-only environment feedback loop, and a shared FastAPI Gateway with a React command center.

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

Assistant text is streamed as it arrives. Tool calls are assembled and validated
by the runtime before execution, then rendered as a trace:

```text
User> 读取 README.md 并总结项目内容。
[tool_call] read_file {"path":"README.md"}
[tool_result] read_file {"path":".../README.md","content":"...","truncated":false}
Assistant> README.md describes a minimal agent runtime...
```

## Gateway and Web command center

Build the browser interface once, then start the Gateway:

```bash
cd web
npm install
npm run build
cd ..
uv run python -m gateway
```

Open `http://127.0.0.1:8000`. The Gateway serves the production Web build and
uses the same `AgentService`, `SessionStore`, context builder, memory store,
compactor, and tool registry as the CLI. It never sends the LLM API key to the
browser.

For frontend development, run the Gateway and Vite in separate terminals:

```bash
uv run python -m gateway
cd web && npm run dev
```

Vite proxies `/api` and `/ws` to the local Gateway. The interface is a
three-column agent command center: shared sessions on the left, persisted chat
history in the middle, and structured runtime activity plus session attachments
on the right. Sessions can be renamed or deleted from the left rail, assistant
messages render safe GitHub-flavored Markdown, and text emitted before a tool
call remains visible as a working note. On smaller screens the side panels
become drawers.

The REST surface is intentionally small:

- `GET /api/sessions` lists sessions created by either CLI or Web.
- `POST /api/sessions` creates a session.
- `PATCH /api/sessions/{sessionId}` renames a session.
- `DELETE /api/sessions/{sessionId}` deletes a session and its attachments.
- `GET /api/sessions/{sessionId}` returns persisted history.
- `GET/POST /api/sessions/{sessionId}/attachments` lists or uploads attachments.

`/ws/chat` accepts `run_turn` frames with `requestId`, optional `sessionId`, and
`message`. A missing session ID creates a new session; an unknown ID returns a
structured error. The Gateway first emits `session_resolved`, then wraps each
existing `AgentEvent` as `agent_event`. Transport failures use `gateway_error`.
One failed request does not terminate the connection or server.

Web requests for the same session are serialized inside the Gateway. Concurrent
CLI updates remain protected by the SessionStore revision check.

## Session attachments

Uploaded files are stored below
`data/sessions/<sessionId>/attachments/`. The user-supplied filename is metadata;
the server generates the on-disk attachment ID, rejects unsafe filenames, and
limits uploads to 10 MiB. Each session has an atomic `index.json`, and APIs only
list metadata for the requested session.

Attachment metadata is included in that session's model context. Uploading a
file does not make it a workspace file, grant permission to modify it, or add a
new shell/write capability. When an attachment store is configured, the runtime
adds a session-scoped `read_attachment` tool for UTF-8 text. The tool accepts an
attachment ID, never exposes the server path, and returns at most 65,536
characters. Attachments from another session, missing blobs, symbolic links,
invalid UTF-8, and obvious binary content become ordinary tool failures.

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
`meta.json` and an append-only `messages.jsonl`. A completed logical turn,
including any tool calls and results, is committed as one revisioned record. Stale revisions are rejected instead of
overwriting newer history. Invalid or corrupt files produce a visible error
instead of being replaced silently.

## Stable context and memory

Stable context is assembled before the current session history on every LLM request:

1. `claw/prompts/system_prompt.md` defines runtime rules and behavior boundaries.
2. `claw/prompts/soul.md` defines Claw's stable identity and interaction style.
3. Manually managed memories provide long-term facts and preferences across sessions.
4. The current session summary, when present, preserves compacted conversation state.

System prompt and soul changes take effect after restarting the CLI. Memory commands are handled locally and are never sent to the LLM as user messages:

```text
/memory add <content>
/memory list
/memory delete <memoryId>
```

Each memory is stored as a readable Markdown file under `data/memory/`. Step 3 only supports explicit, manual memory updates; ordinary conversation cannot rewrite stable context.

## Conversation compaction

Compaction only processes the current session's conversation context. System
prompt, soul, and cross-session memory are never sent to the summarization call.
The normal model context is assembled in this order:

1. system prompt;
2. soul;
3. memory;
4. current session summary, when non-empty;
5. recent active session messages.

The automatic policy deterministically counts characters in the compact JSON
context payload, including stable context, active session messages, and tool
definitions. Compaction starts when that request exceeds 80,000 characters. It
keeps recent complete logical turns up to a 20,000-character budget and summarizes
older turns. The policy never separates a tool call from its observation.
Automatic compaction runs at most once, before a turn starts, and only sees
committed history. If the projected request remains oversized, the runtime emits
a warning and continues best-effort instead of repeatedly calling the summarizer.

Use `/compact` to compact the current session immediately. Successful automatic
and manual compactions print the number of summarized and retained messages plus
the updated summary. Empty summaries, LLM failures, revision conflicts, and
storage failures leave the previous active history untouched and produce a
visible warning.

Compaction is persisted as a revisioned record in the session's append-only
`messages.jsonl`. Replaying the log exposes the latest summary and only the
retained messages as active context; earlier records remain available for audit
and failure recovery. The summary is session-local and is never shared as memory.

## Read-only tools and agent loop

The model receives OpenAI-native function definitions on each normal agent call.
It can produce a final answer or request tools. The runtime executes at most five
calls in one batch, appends successful and failed results to the in-progress
session context, and calls the model again until it produces a final answer.
There is no total agent-loop iteration limit.

Step 5 provides three workspace-oriented read-only tools:

- `current_time {}` returns local time with its UTC offset.
- `list_dir {"path":"."}` lists one directory level in stable name order.
- `read_file {"path":"README.md"}` reads UTF-8 text and caps returned content at
  65,536 characters.

Relative tool paths are resolved from the process working directory. Step 5 does
not reinterpret them relative to the runtime data directory.

Gateway turns also receive a session-scoped attachment reader when the
`AttachmentStore` is available:

- `read_attachment {"attachment_id":"attachment_0123456789ab"}` reads UTF-8
  text belonging to the current session only and caps returned content at
  65,536 characters.

Unknown tools, invalid JSON arguments, schema violations, missing files, invalid
UTF-8, and handler failures become structured tool observations rather than
crashing the loop. Tool definitions are sent through the API `tools` parameter;
they are not session messages and never participate in compaction.

The registry deliberately supports a small schema subset: an object with
`string`, `integer`, `number`, or `boolean` properties, optional `enum`,
`required`, and boolean `additionalProperties`. Unsupported types or keywords are
rejected when the tool is defined instead of being silently ignored.

Before each provider call, internal tool-result metadata is projected onto the
Chat Completions message schema. A stream is complete only when its terminal
`finish_reason` matches `stop` or `tool_calls`; missing, length-limited, filtered,
or contradictory terminal states fail the turn without committing partial text.

Tool handlers may be synchronous or asynchronous. Synchronous handlers run in a
worker thread so they do not block the agent event loop. Every handler has a
30-second timeout; a timed-out synchronous worker may continue in its thread, but
the agent receives a timeout observation and can continue reasoning.

Session storage retains full tool results. Model requests and trace events use a
defensive projection: at most 16,384 preview characters per result and 32,768
preview characters across one request, allocated newest-first. Every older result
keeps a protocol stub, so assistant tool calls always remain paired with tool
messages. Compaction uses this same projected view when asking the LLM for a
summary.

A completed agent turn is committed atomically as one append-only JSONL record:

```text
user -> assistant(tool_calls) -> tool results -> ... -> assistant(final)
```

This step intentionally has no workspace sandbox or sensitive-file filter. It
does not expose write, edit, delete, shell, package, Git, or messaging tools.
Advanced definitions are nevertheless fail-closed: they must declare approval,
the default policy denies them, and the registry refuses execution even after an
approval because Step 5 has no durable execution journal or idempotency protection.

Atomic commit-at-end is safe for the current read-only tools. M7 may enable
side-effecting tools only after approval is paired with a durable execution journal
or idempotency keys, so a successful side effect cannot disappear after a failed
turn commit.

## Runtime paths

When running from a source checkout, `.env` and `data/` are resolved from the
repository root regardless of the current working directory. An installed wheel
uses `~/.sjtuclaw` by default. Set `CLAW_HOME` to choose a different runtime root.

The default prompts are packaged with `claw`. Optional prompt overrides can be
provided with `CLAW_SYSTEM_PROMPT` and `CLAW_SOUL`; both variables take absolute
or user-relative file paths.

Unexpected runtime exceptions are written with their full traceback to the
rotating `logs/claw.log` file (1 MB, three backups). Agent error events contain
only a stable error code and a user-safe summary.

## Test

```bash
uv run pytest
cd web && npm test && npm run build
```

The real `.env` file and runtime outputs under `data/` or `logs/` are ignored by Git.
