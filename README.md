# SJTUClaw

SJTUClaw is a minimal agent runtime course project. The current implementation covers Step 9: persistent multi-session context, safe compaction, streamed OpenAI-compatible tool calling, a shared FastAPI Gateway with a React command center, persistent scheduled tasks, session-scoped workspace tools with explicit approval for side effects, and a turn-scoped skill system.

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
by the runtime before execution, then rendered as compact activities rather than
raw arguments and results:

```text
User> 读取 README.md 并总结项目内容。
Tool> 读取文件 · README.md [RUNNING]
Tool> 读取文件 · README.md [DONE] · 2,148 字符
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
browser. This long-running Gateway process also activates the runtime-owned
Scheduler; constructing a runtime or starting the ordinary CLI does not start
background services implicitly.

For frontend development, run the Gateway and Vite in separate terminals:

```bash
uv run python -m gateway
cd web && npm run dev
```

Vite proxies `/api` and `/ws` to the local Gateway. The interface is a
three-column agent command center: shared sessions on the left, persisted chat
history in the middle, and session attachments or scheduled tasks on the right.
Sessions can be renamed or deleted from the left rail. The conversation header
offers a secondary `COMPACT` action with an inline result notice. A persisted
session summary is rendered as the first scrollable history block above the
retained active messages, so compacted context remains available after reloads
and session switches without pinning it over the conversation. Assistant messages render safe
GitHub-flavored Markdown and KaTeX, while tool activities appear inline between
working notes and final answers. Transport-only events such as turn boundaries
and response deltas are not shown. On smaller screens the side panels become
drawers.

The REST surface is intentionally small:

- `GET /api/sessions` lists sessions created by either CLI or Web.
- `POST /api/sessions` creates a session.
- `PATCH /api/sessions/{sessionId}` renames a session.
- `POST /api/sessions/{sessionId}/compact` force-compacts complete old turns through the shared AgentService and returns the refreshed session.
- `DELETE /api/sessions/{sessionId}` deletes an idle session and its attachments. Scheduled tasks or pending approvals require explicit `?cascade=true`; approved or executing effects always block deletion.
- `GET /api/sessions/{sessionId}` returns persisted history.
- `GET/POST /api/sessions/{sessionId}/attachments` lists or uploads attachments.
- `GET/PUT /api/sessions/{sessionId}/workspace` reads or changes the session workspace.
- `GET /api/approvals` lists approval records; `POST /api/approvals/{approvalId}/resolve` approves or denies one pending request.
- `GET /api/downloads/{downloadId}` returns an unexpired download snapshot.
- `GET/POST /api/tasks` lists or creates persistent tasks.
- `GET /api/tasks/{taskId}` returns one task with its complete execution history.
- `POST /api/tasks/{taskId}/cancel` cancels all future triggers without deleting history.
- `GET /api/skills` and `GET /api/skills/{name}` expose the installed skill catalog.
- `GET /api/sessions/{sessionId}/skill-usages` returns the session's durable skill audit trail.

`/ws/chat` accepts `run_turn` frames with `requestId`, optional `sessionId`, and
`message`. A missing session ID creates a new session; an unknown ID returns a
structured error. The Gateway first emits `session_resolved`, then wraps each
existing `AgentEvent` as `agent_event`. Tool events also carry the shared,
interface-neutral timeline projection used by Web and CLI. Session detail
responses include both the provider-neutral messages and this derived timeline.
Transport failures use `gateway_error`. One failed request does not terminate
the connection or server.

Agent turns for the same session are serialized by one runtime-owned
`SessionCoordinator`, so Web, CLI, Scheduler, rename, workspace changes, task
creation, and deletion share the same lifecycle boundary. A per-session file
lease extends turn exclusion across processes; SessionStore revision checks
remain the final stale-write defense.

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

## Scheduler and persistent tasks

The Scheduler is part of the core `claw` runtime, not the Gateway transport.
`build_runtime()` only constructs the object graph. The Gateway explicitly enters
the runtime service lifecycle because it is the project's long-running host;
closing an ordinary CLI therefore cannot silently stop or start scheduled work.

Tasks are stored independently under `data/tasks/<taskId>.json`. Each aggregate
contains its instruction, owning session, schedule, next trigger, status,
revision, and every execution result. Task files contain only the final assistant
reply or error summary; complete user, assistant, and tool messages remain in the
owning SessionStore.

The Web task panel supports two explicit schedule types:

- `once`: one timezone-aware future `runAt` value;
- `interval`: a timezone-aware `startAt` plus a positive `intervalSeconds`.

At each due time the Scheduler atomically claims the task, marks it running, and
passes its instruction to `AgentService.run_turn(sessionId, content)`. Successful
turns therefore reuse normal context, memory, tools, compaction, and atomic
session persistence. Agent error events and unexpected Scheduler errors are
stored in execution history rather than swallowed.

Periodic tasks do not overlap themselves. After an execution finishes, the next
trigger is the first schedule boundary strictly after completion. A failed
periodic execution remains visible as failed but retains a future trigger. On
restart, overdue schedules run once instead of replaying every missed interval;
an execution interrupted by the previous process is closed as failed. Cancelling
a running task prevents future triggers but lets the current agent turn finish
and records its outcome. Session deletion fails while active tasks or unresolved
approvals still refer to it; explicit cascade deletion cancels tasks and pending
approvals, while approved or executing effects remain a hard safety blocker.

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

## Skills

Skills are discovered from packaged and user skill directories, but only their
name and description enter the initial turn context. A turn can load at most one
full skill. Users select one explicitly with `/skill <name> <task>` in CLI or the
WebSocket `skillName` field. The model may instead request `load_skill`; because
that expands model context, automatic selection requires normal user approval.
The selected instructions stay turn-local and never leak into later turns.

Use `/skill list`, `/skill show <name>`, and `/skill usage` to inspect the catalog
and the current session's durable usage records. Each record stores the source,
selection reason, task, outcome, and final output for auditability.

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

## Workspace, tools, and approval

The model receives OpenAI-native function definitions on each normal agent call.
It can produce a final answer or request tools. The runtime executes at most five
calls in one batch, appends successful and failed results to the in-progress
session context, and calls the model again until it produces a final answer. One
turn is capped at 12 provider rounds and 30 total tool calls. Reaching either
budget emits a warning and forces one final tools-disabled provider round instead
of spinning indefinitely.

Each session can bind one canonical workspace directory. CLI users manage it
locally with:

```text
/workspace set <path>
/workspace show
/workspace clear
```

The Web Inspector exposes the same session binding through Gateway APIs. A
workspace, memory snapshot, attachment catalog, and skill catalog are captured
when a turn starts. Later tool iterations in that turn see the same stable
context. Model-supplied file paths must be relative, and canonical resolution
rejects absolute paths, `..` escapes, and symlink escapes. Filesystem tools fail
clearly while no workspace is configured.

The read-only catalog contains:

- `current_time {}` returns local time with its UTC offset.
- `list_dir {"path":"."}` lists one directory level in stable name order.
- `read_file {"path":"README.md"}` reads UTF-8 text and caps returned content at
  65,536 characters.

`list_dir` and `read_file` resolve paths against the current session workspace,
never against the Gateway process cwd or runtime data directory.

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
30-second timeout. Read-only timeouts are ordinary failures. Because a timed-out
synchronous side-effect worker may still finish in its thread, that result is
recorded as `uncertain` and the turn stops; the model is never told that the
effect definitely failed.

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

Step 8 adds these workspace capabilities:

- `create_file`, `overwrite_file`, and exact-match `edit_file` use atomic writes;
- `copy_attachment_to_workspace` can copy only a blob owned by the current session;
- `new_shell` explicitly creates or resets one persistent shell per session;
  `run_command` requires that shell and then reuses its cwd, environment, and
  sourced state;
- `create_download` snapshots an existing workspace file into a short-lived
  runtime download and returns metadata rather than file contents to the model.

Update, attachment-copy, and shell calls are prepared and schema-validated before
an approval is created. The runtime emits the complete approval ID, arguments,
and workspace, then suspends on an `asyncio.Future`. CLI prompts for a decision;
Web displays an approval card and resolves it through Gateway. Denial reasons and
execution results become ordinary tool observations, so the LLM can continue from
what actually happened.

Approval records are atomically persisted under `data/approvals/`. Side-effect
execution has a separate journal under `data/executions/` with idempotency keys,
precondition hashes, terminal results, and a `session_recorded` marker. File
writes revalidate the approved precondition immediately before execution. On
restart, an expected post-write hash is reconciled as success, an unchanged file
as failure, and an ambiguous file or shell effect as `uncertain`; effects are
never replayed automatically. Recovered terminal outcomes missing from session
history are appended as an explicit runtime-recovery audit turn. Temporary
download blobs live under `data/downloads/` and expire after fifteen minutes.
Pending or approved flows that never started execution expire on restart, and
their prepared execution records are cancelled rather than resumed.

The shell boundary enforces its canonical cwd before and after each command and
terminates the shell after timeout, workspace change, or cwd escape. This is not
a container or OS sandbox: arbitrary approved commands can explicitly address
resources outside cwd. Approval is therefore the safety boundary for command
effects.

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
