from datetime import datetime
from pathlib import Path

import store
import ui
from i18n import t, day_name

WEEKDAY_ALIASES = {
    "월": 0, "월요일": 0, "mon": 0, "monday": 0,
    "화": 1, "화요일": 1, "tue": 1, "tuesday": 1,
    "수": 2, "수요일": 2, "wed": 2, "wednesday": 2,
    "목": 3, "목요일": 3, "thu": 3, "thursday": 3,
    "금": 4, "금요일": 4, "fri": 4, "friday": 4,
    "토": 5, "토요일": 5, "sat": 5, "saturday": 5,
    "일": 6, "일요일": 6, "sun": 6, "sunday": 6,
}
WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"]  # 저장용 정규 표기 (표시할 땐 i18n.day_name 사용)


def _ask(prompt: str) -> str:
    return input(prompt).strip()


def _ask_yes_no(prompt: str) -> bool:
    return ui.confirm(prompt)


def _ask_float(prompt: str, lo: float, hi: float) -> float:
    while True:
        raw = _ask(prompt)
        try:
            val = float(raw)
        except ValueError:
            print(t("w.num"))
            continue
        if not (lo <= val <= hi):
            print(t("w.range", lo=lo, hi=hi))
            continue
        return val


def _ask_mode() -> str:
    idx = ui.select_menu(t("w.mode.q"), [t("w.mode.5h"), t("w.mode.weekly")])
    return "1" if idx == 0 else "2"


def _ask_working_dir() -> str:
    while True:
        raw = _ask(t("w.ask.dir"))
        path = Path(raw).expanduser().resolve()
        if path.is_dir():
            return str(path)
        if not path.exists():
            if _ask_yes_no(t("w.ask.mkdir", path=path)):
                path.mkdir(parents=True)
                return str(path)
            continue
        print(t("w.err.notdir"))


def _ask_add_dirs() -> list:
    raw = _ask(t("w.ask.adddirs"))
    dirs = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        path = Path(part).expanduser().resolve()
        if path.is_dir():
            dirs.append(str(path))
        else:
            print(t("w.skip.missing", p=part))
    return dirs


def _ask_prompt() -> str:
    while True:
        raw = _ask(t("w.ask.prompt"))
        if raw:
            return raw
        print(t("w.err.empty"))


MODEL_CHOICES = [
    (None, "w.model.default"),
    ("fable", "w.model.fable"),
    ("opus", "w.model.opus"),
    ("sonnet", "w.model.sonnet"),
    ("haiku", "w.model.haiku"),
]
MODEL_VALUES = [m for m, _ in MODEL_CHOICES if m]

EFFORT_CHOICES = [
    (None, "w.effort.default"),
    ("low", "w.effort.low"),
    ("medium", "w.effort.medium"),
    ("high", "w.effort.high"),
    ("xhigh", "w.effort.xhigh"),
    ("max", "w.effort.max"),
]
EFFORT_VALUES = [e for e, _ in EFFORT_CHOICES if e]


def _ask_model():
    idx = ui.select_menu(t("w.ask.model"), [t(key) for _, key in MODEL_CHOICES])
    return MODEL_CHOICES[idx][0]


def _ask_effort():
    idx = ui.select_menu(t("w.ask.effort"), [t(key) for _, key in EFFORT_CHOICES])
    return EFFORT_CHOICES[idx][0]


def _ask_budget():
    while True:
        raw = _ask(t("w.ask.budget"))
        if not raw:
            return None
        try:
            val = float(raw)
            if val > 0:
                return val
        except ValueError:
            pass
        print(t("w.err.budget"))


def _ask_weekday() -> str:
    idx = ui.select_menu(t("w.ask.weekday"), [day_name(i) for i in range(7)])
    return WEEKDAY_LABELS[idx]


def _ask_time() -> str:
    while True:
        raw = _ask(t("w.ask.time"))
        try:
            datetime.strptime(raw, "%H:%M")
            return raw
        except ValueError:
            print(t("w.err.time"))


def format_task(task: dict) -> str:
    lines = [f"[{task['id']}] {task['prompt']}"]
    lines.append(t("f.dir", v=task["working_dir"]))
    if task.get("add_dirs"):
        lines.append(t("f.adddirs", v=", ".join(task["add_dirs"])))
    if task.get("five_hour"):
        c = task["five_hour"]
        lines.append(t("f.cond5", p=c["min_remaining_pct"], h=c["hours_before_reset"]))
    if task.get("weekly"):
        c = task["weekly"]
        day = day_name(WEEKDAY_ALIASES[c["day_of_week"]])
        lines.append(t("f.condw", p=c["min_remaining_pct"], d=day, t=c["time"]))
    model = task.get("model")
    lines.append(t("f.model", v=model or t("f.model.default")))
    if task.get("effort"):
        lines.append(t("f.effort", v=task["effort"]))
    budget = task.get("max_budget_usd")
    lines.append(t("f.budget", v=budget) if budget else t("f.budget.none"))
    lines.append(t("f.session", v=task.get("last_session_id") or t("f.session.none")))
    return "\n".join(lines)


def run_wizard() -> None:
    print(t("w.title") + "\n")

    # 1. 언제 실행할지: 기준 선택 + 조건 입력
    mode = _ask_mode()
    five_hour = None
    weekly = None
    if mode == "1":
        pct = _ask_float(t("w.ask.remain5"), 0, 100)
        hours = _ask_float(t("w.ask.before"), 0, 5)
        five_hour = {"enabled": True, "min_remaining_pct": pct, "hours_before_reset": hours}
    else:
        pct = _ask_float(t("w.ask.remainw"), 0, 100)
        day = _ask_weekday()
        time_str = _ask_time()
        weekly = {"enabled": True, "min_remaining_pct": pct, "day_of_week": day, "time": time_str}

    # 2. 어떤 폴더에서 뭘 할지
    working_dir = _ask_working_dir()
    add_dirs = _ask_add_dirs()
    prompt = _ask_prompt()
    model = _ask_model()
    effort = _ask_effort()
    max_budget = _ask_budget()

    task = store.add_task(
        prompt=prompt, working_dir=working_dir, add_dirs=add_dirs,
        five_hour=five_hour, weekly=weekly, model=model, effort=effort, max_budget_usd=max_budget,
    )

    print("\n" + t("w.done"))
    print(format_task(task))
    print("\n" + t("w.hint.list"))
