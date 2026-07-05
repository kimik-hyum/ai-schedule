import json
import subprocess
import threading
from datetime import datetime

import notify
import store
import usage as usage_mod
from i18n import t, day_name
from wizard import WEEKDAY_ALIASES

RUN_LOCK = threading.Lock()  # 폴링 루프와 웹 UI의 '지금 확인'이 동시에 돌지 않도록


def _same_five_hour_window(stored_iso, current_reset: datetime) -> bool:
    if not stored_iso:
        return False
    stored = datetime.fromisoformat(stored_iso)
    # resets_at은 호출마다 초 단위로 미세하게 흔들리는 게 실측으로 확인되어, 근접하면 같은 윈도우로 간주
    return abs((stored - current_reset).total_seconds()) < 600


def check_five_hour(task: dict, u: "usage_mod.Usage"):
    cond = task.get("five_hour")
    if not cond:
        return False, t("r.cond.unset")
    if _same_five_hour_window(task["last_fired"].get("five_hour_reset"), u.five_hour.resets_at):
        return False, t("r.5h.fired")
    remaining = u.five_hour.remaining_pct
    if remaining < cond["min_remaining_pct"]:
        return False, t("r.5h.low", r=remaining, m=cond["min_remaining_pct"])
    hours_left = u.five_hour.seconds_to_reset / 3600
    if hours_left <= 0:
        return False, t("r.5h.past")
    if hours_left > cond["hours_before_reset"]:
        return False, t("r.5h.early", h=hours_left, b=cond["hours_before_reset"])
    return True, t("r.5h.met", r=remaining, h=hours_left)


def check_weekly(task: dict, u: "usage_mod.Usage", now: datetime):
    cond = task.get("weekly")
    if not cond:
        return False, t("r.cond.unset")
    iso_year, iso_week, _ = now.isocalendar()
    week_key = f"{iso_year}-W{iso_week}"
    if task["last_fired"].get("weekly_key") == week_key:
        return False, t("r.w.fired")
    target_weekday = WEEKDAY_ALIASES[cond["day_of_week"]]
    if now.weekday() != target_weekday:
        return False, t("r.w.day", d=day_name(target_weekday))
    # 설정 시각이 지난 뒤의 첫 확인 때 실행 — 확인 주기가 몇 시간이어도 그날 안에는 잡힘 (주 1회 기록이 중복 방지)
    target_h, target_m = map(int, cond["time"].split(":"))
    if (now.hour, now.minute) < (target_h, target_m):
        return False, t("r.w.early", t=cond["time"], n=now.strftime("%H:%M"))
    remaining = u.seven_day.remaining_pct
    if remaining < cond["min_remaining_pct"]:
        return False, t("r.w.low", r=remaining, m=cond["min_remaining_pct"])
    return True, t("r.w.met", r=remaining, d=day_name(target_weekday), t=cond["time"])


def evaluate_task(task: dict, u: "usage_mod.Usage", now: datetime):
    """예약의 현재 상태 평가. (kind, ok, reason) — 웹 UI와 run_once가 공용."""
    if task.get("five_hour"):
        ok, reason = check_five_hour(task, u)
        return "five_hour", ok, reason
    if task.get("weekly"):
        ok, reason = check_weekly(task, u, now)
        return "weekly", ok, reason
    return None, False, t("r.cond.unset")


def execute_task(task: dict) -> dict:
    """작업 실행 후 이력 레코드를 반환. status: ok | budget_exceeded | error | timeout"""
    claude_bin = usage_mod.resolve_claude_binary()
    budget = task.get("max_budget_usd")
    model = task.get("model")
    cmd = [claude_bin, "-p", task["prompt"], "--output-format", "json", "--permission-mode", "acceptEdits"]
    for d in task.get("add_dirs") or []:
        cmd += ["--add-dir", d]  # 방식 A: 대표 폴더에서 실행하되 추가 폴더도 접근 허용
    if model:
        cmd += ["--model", model]
    if task.get("effort"):
        cmd += ["--effort", task["effort"]]
    if budget:
        cmd += ["--max-budget-usd", str(budget)]
    if task.get("last_session_id"):
        cmd += ["--resume", task["last_session_id"]]

    record = {
        "task_id": task["id"],
        "prompt": task["prompt"],
        "working_dir": task["working_dir"],
        "model": model,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "status": None,
        "session_id": None,
        "cost": None,
        "result_preview": None,
    }

    budget_label = f"${budget:g}" if budget else t("r.none")
    print(t("r.exec", d=task["working_dir"], m=model or t("r.default"), b=budget_label))
    notify.notify_start(task)
    try:
        result = subprocess.run(
            cmd, cwd=task["working_dir"], capture_output=True, text=True,
            timeout=task.get("timeout_seconds", 600),
        )
        payload = json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        print(t("r.timeout"))
        notify.notify_error(task, t("r.timeout.short"))
        record.update(status="timeout", finished_at=datetime.now().isoformat(timespec="seconds"))
        return record
    except json.JSONDecodeError:
        err_out = (result.stdout or "") + (result.stderr or "")
        print(t("r.parsefail", e=err_out[:500]))
        notify.notify_error(task, err_out[:80] or t("r.fail.short"))
        # --resume 대상 세션이 삭제된 경우: 참조를 초기화해 다음 윈도우에서 새 세션으로 복구
        if "No conversation found" in err_out and task.get("last_session_id"):
            store.update_task(task["id"], last_session_id=None)
            print(t("r.session.reset"))
        record.update(
            status="error", finished_at=datetime.now().isoformat(timespec="seconds"),
            result_preview=err_out[:300],
        )
        return record

    session_id = payload.get("session_id")
    cost = payload.get("total_cost_usd", 0)
    record.update(
        finished_at=datetime.now().isoformat(timespec="seconds"),
        session_id=session_id, cost=cost,
    )

    if payload.get("subtype") == "error_max_budget_usd":
        print(t("r.budget", c=cost, b=budget, s=session_id))
        notify.notify_budget_exceeded(task, cost, budget, session_id, claude_bin)
        record["status"] = "budget_exceeded"  # 세션은 저장되어 있어 다음 윈도우에서 이어서 진행 가능
        return record

    if payload.get("is_error"):
        print(t("r.error", e=payload.get("result")))
        notify.notify_error(task, str(payload.get("result"))[:80])
        record.update(status="error", result_preview=str(payload.get("result"))[:300])
        return record

    print(t("r.done", s=session_id, c=cost))
    print(t("r.result", r=str(payload.get("result"))[:200]))
    notify.notify_done(task, session_id, cost, claude_bin)
    record.update(status="ok", result_preview=str(payload.get("result"))[:300])
    return record


def run_once() -> None:
    with RUN_LOCK:
        _run_once_locked()


def _run_once_locked() -> None:
    try:
        u = usage_mod.fetch_usage()
    except usage_mod.UsageError as e:
        print(t("r.usagefail", e=e))
        return

    now = datetime.now()
    print(f"===== {now:%Y-%m-%d %H:%M:%S} =====")
    print(t(
        "r.header",
        p5=u.five_hour.remaining_pct, t5=u.five_hour.resets_at.astimezone().strftime("%H:%M"),
        p7=u.seven_day.remaining_pct, t7=u.seven_day.resets_at.astimezone().strftime("%m/%d %H:%M"),
    ) + "\n")

    tasks = store.list_tasks()
    if not tasks:
        print(t("r.notasks"))
        return

    label_map = {"five_hour": t("r.label.5h"), "weekly": t("r.label.w"), None: t("r.label.none")}
    for task in tasks:
        print(f"[{task['id']}] {task['prompt'][:40]}")
        kind, ok, reason = evaluate_task(task, u, now)
        print(f"  - {label_map[kind]}: {reason}")
        if ok:
            record = execute_task(task)
            store.add_history(record)
            # 실패해도 이번 윈도우는 '시도함'으로 기록 — 폴링마다 재시도하며 예산을 태우는 루프 방지
            if kind == "five_hour":
                updates = {"last_fired": {**task["last_fired"], "five_hour_reset": u.five_hour.resets_at.isoformat()}}
            else:
                iso_year, iso_week, _ = now.isocalendar()
                updates = {"last_fired": {**task["last_fired"], "weekly_key": f"{iso_year}-W{iso_week}"}}
            if record.get("session_id"):
                updates["last_session_id"] = record["session_id"]
            store.update_task(task["id"], **updates)
        print()

    # 단일 예약 처리 후 대형 작업(Job) 큐 진행
    import jobrunner
    jobrunner.process_jobs()
