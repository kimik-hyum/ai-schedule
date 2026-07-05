import shutil
import subprocess
from pathlib import Path

from i18n import t

APP_TITLE = "AI Schedule"
OPEN_SESSION_HELPER = Path(__file__).resolve().parent / "open_session.sh"


def _terminal_notifier():
    return shutil.which("terminal-notifier")


def _send(title: str, message: str, execute: str = None, sound: str = "Glass", group: str = None):
    tn = _terminal_notifier()
    if tn:
        cmd = [tn, "-title", APP_TITLE, "-subtitle", title, "-message", message, "-sound", sound]
        if group:
            cmd += ["-group", group]
        if execute:
            cmd += ["-execute", execute]
        subprocess.run(cmd, capture_output=True)
    else:
        # terminal-notifier가 없으면 기본 알림으로 폴백 (클릭 액션은 미지원)
        script = f'display notification "{message}" with title "{APP_TITLE}" subtitle "{title}" sound name "{sound}"'
        subprocess.run(["osascript", "-e", script], capture_output=True)


def notify_start(task: dict):
    _send(
        title=t("n.start"),
        message=task["prompt"][:60],
        group=f"ai-schedule-{task['id']}",
    )


def notify_done(task: dict, session_id: str, cost: float, claude_bin: str):
    execute = f"'{OPEN_SESSION_HELPER}' '{task['working_dir']}' '{session_id}' '{claude_bin}'"
    _send(
        title=t("n.done"),
        message=t("n.done.msg", p=task["prompt"][:50], c=cost),
        execute=execute,
        group=f"ai-schedule-{task['id']}",
    )


def notify_budget_exceeded(task: dict, cost: float, budget: float, session_id: str, claude_bin: str):
    execute = None
    if session_id:
        execute = f"'{OPEN_SESSION_HELPER}' '{task['working_dir']}' '{session_id}' '{claude_bin}'"
    _send(
        title=t("n.budget"),
        message=t("n.budget.msg", p=task["prompt"][:40], c=cost, b=budget),
        execute=execute,
        sound="Basso",
        group=f"ai-schedule-{task['id']}",
    )


def notify_error(task: dict, reason: str):
    _send(
        title=t("n.fail"),
        message=f"{task['prompt'][:40]} — {reason[:80]}",
        sound="Basso",
        group=f"ai-schedule-{task['id']}",
    )


def notify_job_digest(job: dict, ok: int, fail: int, cost: float, done: int, total: int):
    _send(
        title=t("n.job.digest", d=done, n=total),
        message=t("n.job.digest.msg", ok=ok, fail=fail, c=cost, p=job["request"][:40]),
        group=f"ai-schedule-job-{job['id']}",
    )


def notify_job_done(job: dict, report_path: str):
    _send(
        title=t("n.job.done"),
        message=job["request"][:60],
        execute=f"open '{report_path}'",
        group=f"ai-schedule-job-{job['id']}",
    )


def notify_job_failed(job: dict):
    _send(
        title=t("n.job.failed"),
        message=job["request"][:60],
        sound="Basso",
        group=f"ai-schedule-job-{job['id']}",
    )
