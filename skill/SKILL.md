---
name: ai-schedule
description: Quota-aware task scheduler for Claude Code. Use when the user wants to schedule a task against their Claude quota ("schedule this task", "run this before my quota resets", "run this every Friday morning", "작업 예약해줘", "리셋 전에 남는 토큰/할당량으로 ~해줘", "매주 ~요일에 실행되게 해줘"), manage schedules ("list/remove my scheduled tasks", "예약 목록/삭제"), control the daemon ("turn auto-run on/off", "자동 실행 켜줘/꺼줘/상태"), or check quota ("how much quota is left", "할당량 얼마 남았어"). A background daemon runs eligible tasks headlessly and sends macOS notifications.
---

# AI Schedule — quota-aware task scheduler

All commands use the global `ais` command (works from any directory):

```bash
ais <command> [options]
```

## Commands

| Command | Purpose |
|---|---|
| `usage` | current 5-hour / weekly quota and reset times |
| `list` | scheduled tasks (id, condition, model, budget, last session) |
| `add --prompt ... --dir ... <condition>` | non-interactive scheduling (Claude should always use this form) |
| `remove <id>` | delete a task |
| `run-once` | check conditions now and run eligible tasks once |
| `start --hours N` / `stop` / `status` | daemon on / off / status+log |
| `ui` | open the web dashboard (http://localhost:8787) |

## Scheduling (non-interactive)

5-hour based — run when ≥X% remains and reset is within Y hours:
```bash
ais add --prompt "task instruction" --dir /work/folder --five-hour-remaining 50 --before-reset 1 [--model haiku] [--effort low] [--budget 3]
```

Weekly based — first check after DAY HH:MM if ≥X% of the weekly quota remains:
```bash
ais add --prompt "task instruction" --dir /work/folder --weekly-remaining 30 --day fri --time 09:00
```

- Exactly one condition (5-hour or weekly) per task
- `--day`: mon–sun (Korean 월–일 also accepted) / `--time`: HH:MM
- `--model`: fable|opus|sonnet|haiku (omit = default). fable is top-tier but consumes the most quota and has its own scoped weekly limit — reserve for genuinely hard tasks
- `--effort`: low|medium|high|xhigh|max (omit = default). Use low for simple/mechanical tasks to save tokens
- `--budget`: auto-stop when estimated cost exceeds this many dollars (omit = unlimited)
- `--add-dir <path>`: extra folders the session may access (repeatable). When the user says "compare/use several projects together", pick one primary --dir and pass the rest via --add-dir

## Defaults when the user doesn't specify conditions

- No trigger mentioned → **5-hour based, ≥50% remaining, within 1 hour of reset**
- "every week / weekday / morning" mentioned → weekly based; default time 09:00, default remaining 30%
- Model: checks/summaries/reports → `haiku`; normal coding → omit; large refactors/design → `opus`
- Budget: use the user's number if given; otherwise omit, but suggest ~5 USD for heavy tasks
- Working dir: the project the user mentioned, else the current directory. Verify it exists before scheduling

## Prompt-writing rules (important)

Scheduled prompts run in **unattended headless sessions** — Claude cannot ask follow-up questions. Therefore:
- Rewrite vague or typo-ridden requests into a self-contained instruction (subject, scope, expected output)
- New sessions have no prior context: replace "that thing", "the task from before" with concrete content
- After scheduling, always show the registered card to the user and mention anything you rephrased

## After scheduling, always

1. Show the confirmation card (`=== Task Scheduled ===` output)
2. Check `status` — **if the daemon is off, turn it on with `start --hours 1`** (otherwise the schedule will never fire)
3. Check `usage` and tell the user roughly when the task is likely to fire

## How it works (for user questions)

- The daemon polls the quota endpoint every N hours, evaluates conditions, and runs `claude -p` in the task's folder
- macOS notifications on start/finish/failure; clicking the finish notification opens the session in Terminal via `claude --resume`
- Each task fires at most once per 5-hour window / per week (failures don't retry within the window)
- Re-runs resume the previous session (`--resume`) for continuity
- The daemon dies on reboot — run `start` again (or add AI Schedule.app to Login Items)
- State: `tasks.json` in the project folder (schedule + last 100 run records); log: `logs/scheduler.log`
