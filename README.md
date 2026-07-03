# ⏰ AI Schedule

**Quota-aware task scheduler for Claude Code** — automatically run pre-written tasks when your Claude subscription quota conditions are met (e.g. *"run this when ≥50% of my 5-hour quota remains and the reset is less than 1 hour away"*), instead of letting leftover quota expire unused.

Runs entirely on your Mac. No external dependencies — just macOS's built-in Python 3 and your own Claude Code login.

## How it works

```
ais start  →  background daemon polls your quota every N hours
                 │
                 ├─ 5-hour window:  ≥X% remaining AND within Y hours of reset?
                 ├─ weekly window:  ≥X% remaining AND it's the scheduled day/time?
                 │
                 └─ condition met → runs `claude -p "<your task>"` headless
                        in the task's working folder (resuming its previous
                        session), sends macOS notifications, records history
```

- Quota data comes from the same endpoint the Claude Code client uses, read with **your own** OAuth credential from the macOS Keychain. Nothing ever leaves your machine.
- Each task fires **at most once per 5-hour window / per week** — even if a run fails, it won't retry-burn your quota.
- Completed-task notifications are **clickable** — they open the session in Terminal via `claude --resume` so you can read the full conversation and continue it.

## Requirements

- **macOS** (Keychain, notifications, and Finder integration are macOS-specific)
- **Python 3** — already bundled with macOS at `/usr/bin/python3`; nothing to install. Verify with `python3 --version` (3.9+). On a brand-new Mac the first run may prompt to install the Command Line Tools — click *Install*, or run `xcode-select --install`. **No `pip install` and no third-party packages** — standard library only.
- **Claude Code** installed and logged in with a Claude subscription (Pro/Max)
- *(optional)* `brew install terminal-notifier` — enables click-to-open-session notifications

## Install

```bash
git clone https://github.com/kimik-hyum/ai-schedule.git
cd ai-schedule
./install.sh
```

The installer creates the global `ais` command, and optionally a double-clickable **AI Schedule.app** and a **Claude Code skill** (so you can just tell Claude *"schedule this task for me"*).

## Quick start

```bash
ais usage      # current 5-hour / weekly quota and reset times
ais add        # schedule a task (interactive, arrow-key menus)
ais start      # turn on auto-run (checks every hour; --hours N to change)
ais ui         # open the web dashboard (http://localhost:8787)
ais list       # scheduled tasks
ais status     # daemon status + recent log
ais stop       # turn off auto-run
```

Non-interactive scheduling (for scripts / the Claude Code skill):

```bash
# 5-hour based: run when ≥50% remains and reset is within 1 hour
ais add --prompt "Summarize unused exports in src/" --dir ~/proj \
        --five-hour-remaining 50 --before-reset 1 --model haiku --effort low --budget 3

# weekly based: every Friday 09:00 if ≥30% of the weekly quota remains
ais add --prompt "Weekly dependency audit" --dir ~/proj \
        --weekly-remaining 30 --day fri --time 09:00
```

Per-task options:

| Option | Effect |
|---|---|
| `--model fable\|opus\|sonnet\|haiku` | model override (defaults to your Claude Code setting) |
| `--effort low\|medium\|high\|xhigh\|max` | reasoning effort — `low` saves a lot of tokens on simple tasks |
| `--budget <USD>` | auto-stop the run if estimated cost exceeds this (omit = unlimited) |
| `--add-dir <path>` | extra folders the session may access (repeatable) |

## Web dashboard

While the daemon is running, `http://localhost:8787` (bound to localhost only) shows:

- live 5-hour / weekly gauges with reset countdowns
- waiting tasks (with the exact reason they're waiting) vs. tasks that already ran this window
- full run history — status, duration, cost, result preview, and an *Open session* button
- a scheduling form with a native Finder folder picker and recent-folder dropdown

UI language follows your browser (Korean/English); CLI follows your `LANG` (override with `AIS_LANG=en|ko`).

## Good to know

- **Headless runs consume the same subscription quota** as interactive use — that's the point (use it before it resets), but budget/effort/model options exist so you stay in control.
- The quota endpoint is the one the official client uses but is **not a documented public API** — it may change without notice. If usage lookup breaks, please open an issue.
- The daemon is started from your terminal session (this sidesteps macOS TCC folder-permission issues), so **after a reboot run `ais start` again** — or add AI Schedule.app to your Login Items.
- Tasks run with `--permission-mode acceptEdits`: file reads/edits are auto-approved; anything riskier is auto-denied in headless mode.
- Your schedule and history live in `tasks.json` (gitignored) — nothing is uploaded anywhere.

## 한국어 요약

Claude 구독의 5시간/위클리 할당량이 조건에 맞을 때(예: "잔여 50% 이상 + 리셋 1시간 전") 미리 등록해둔 작업을 자동 실행하는 macOS 도구입니다. `./install.sh` 후 `ais add`로 예약, `ais start`로 자동 실행을 켜면 됩니다. 시작/완료 시 macOS 알림이 오고, 완료 알림을 클릭하면 해당 세션이 터미널로 열립니다. 대시보드는 `ais ui`. CLI/화면 언어는 시스템/브라우저 언어를 따릅니다.

## License

[MIT](LICENSE)
