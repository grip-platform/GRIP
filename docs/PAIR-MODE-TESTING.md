# Pair Mode Testing Guide

Testing procedures for the native Teams-based pair mode (v2.0.0).

## Prerequisites

- Claude Code CLI installed and authenticated
- GRIP infrastructure at `~/.claude/` with pair-mode files:
  - `~/.claude/agents/pair-worker.md`
  - `~/.claude/skills/pair-mode/SKILL.md`
  - `~/.claude/commands/pair.md`
  - `~/.claude/modes/definitions/pair-mode.yaml`

## Test Cases

### 1. Start a Pair Session

**Command:**

```bash
/pair "create a test file called hello.txt with the contents 'hello world'"
```

**Expected:**

- TeamCreate called with team_name `pair-session`
- TaskCreate called with the task description
- Worker agent spawned via Task tool
- Task assigned to worker via TaskUpdate
- Worker picks up task, creates the file, marks task completed
- Parent receives completion notification via SendMessage

**Verify:**

- `TaskList` shows the task with status `completed`
- `hello.txt` exists with correct contents
- No errors in the conversation

### 2. Check Status

**Command:**

```bash
/pair status
```

**Expected:**

- TaskList output showing all tasks with their status, owner, and dependencies
- Worker's task shows as `completed` (from test 1) or `in_progress` if still running

### 3. Send Steering Guidance

**Command:**

```bash
/pair steer "use descriptive variable names and add error handling"
```

**Expected:**

- SendMessage delivered to worker with the guidance text
- Worker acknowledges receipt (visible in next progress update)
- Worker's subsequent work reflects the guidance

**Verify:**

- No delivery errors
- Worker's next message references the steering input

### 4. Add a New Task

**Command:**

```bash
/pair task "rename hello.txt to greeting.txt"
```

**Expected:**

- TaskCreate called with the description
- TaskUpdate assigns the task to worker
- Worker picks up the new task from TaskList
- Worker executes and marks complete

**Verify:**

- `TaskList` shows the new task
- `greeting.txt` exists, `hello.txt` does not

### 5. Stop the Session

**Command:**

```bash
/pair stop
```

**Expected:**

- SendMessage with `type: shutdown_request` sent to worker
- Worker approves shutdown (unless mid-task)
- TeamDelete cleans up team files
- No team files remain in `~/.claude/teams/`

**Verify:**

```bash
ls ~/.claude/teams/pair-session/ 2>/dev/null || echo "Clean - no team files remain"
```

### 6. Safety Gate Compliance

**Command:**

Start a pair session and give the worker a task that touches dependency folders:

```bash
/pair "list all files in node_modules"
```

**Expected:**

- Worker respects GRIP safety gates
- Worker does NOT read from `node_modules`
- Worker reports the constraint via SendMessage

### 7. Worker Progress Updates

**Command:**

```bash
/pair "refactor src/data.js to use TypeScript types, then update all imports"
```

**Expected:**

- Worker sends progress updates every 3-5 tool calls via SendMessage
- Updates are automatically delivered to parent
- Parent can see incremental progress without polling

## Failure Scenarios

### Worker Crashes Mid-Task

If the worker agent errors out:

- Task remains `in_progress` in TaskList
- Parent can spawn a new worker or complete the task manually
- Team files persist until explicit `/pair stop` or `TeamDelete`

### Network Timeout

Not applicable - native Teams use in-process communication, not network transport.

### Concurrent Steering

Not applicable - SendMessage to a single worker is inherently sequential. No lock mechanism needed.

## Cleanup After Testing

Remove any test artifacts:

```bash
rm -f hello.txt greeting.txt
```

Ensure no orphaned teams:

```bash
ls ~/.claude/teams/ 2>/dev/null
```

## Architecture Reference

```text
Parent (you)                    Worker (pair-worker agent)
+-------------------+           +-------------------+
| TeamCreate        |---------->| Spawned via Task   |
| TaskCreate/Update |           | Claims tasks       |
| SendMessage       |<--------->| SendMessage        |
| TaskList          |           | TaskUpdate         |
+-------------------+           +-------------------+
        |                               |
        +----------- Shared ------------+
                    TaskList
```
