#!/usr/bin/env python3
import argparse
import sys
from datetime import datetime
from pathlib import Path

import daemon
import runner
import store
import usage as usage_mod
import wizard
from i18n import t


def cmd_add(args):
    if not args.prompt:
        wizard.run_wizard()
        return

    # 비대화형 등록 (스킬/스크립트용)
    five_hour = None
    weekly = None
    if args.five_hour_remaining is not None:
        five_hour = {
            "enabled": True,
            "min_remaining_pct": args.five_hour_remaining,
            "hours_before_reset": args.before_reset if args.before_reset is not None else 1.0,
        }
    if args.weekly_remaining is not None:
        if not args.day or not args.at:
            sys.exit(t("s.err.weeklyneeds"))
        day = args.day.lower()
        if day not in wizard.WEEKDAY_ALIASES:
            sys.exit(t("s.err.day", d=args.day))
        try:
            datetime.strptime(args.at, "%H:%M")
        except ValueError:
            sys.exit(t("s.err.time", t=args.at))
        weekly = {"enabled": True, "min_remaining_pct": args.weekly_remaining, "day_of_week": day, "time": args.at}

    if (five_hour is None) == (weekly is None):
        sys.exit(t("s.err.onecond"))

    if not args.dir:
        sys.exit(t("s.err.dirreq"))
    wd = Path(args.dir).expanduser().resolve()
    if not wd.is_dir():
        sys.exit(t("s.err.dir", d=wd))

    add_dirs = []
    for d in args.add_dirs or []:
        pd = Path(d).expanduser().resolve()
        if not pd.is_dir():
            sys.exit(t("s.err.adddir", d=d))
        add_dirs.append(str(pd))

    task = store.add_task(
        prompt=args.prompt, working_dir=str(wd), add_dirs=add_dirs,
        five_hour=five_hour, weekly=weekly, model=args.model, effort=args.effort,
        max_budget_usd=args.budget, min_scoped_pct=args.min_scoped,
    )
    print(t("w.done"))
    print(wizard.format_task(task))


def cmd_list(_args):
    tasks = store.list_tasks()
    if not tasks:
        print(t("s.nolist"))
        return
    print(t("s.listtitle", n=len(tasks)) + "\n")
    for tsk in tasks:
        print(wizard.format_task(tsk))
        print()


def cmd_remove(args):
    if store.remove_task(args.id):
        print(t("s.removed", id=args.id))
    else:
        print(t("s.notfound", id=args.id))


def cmd_run_once(_args):
    runner.run_once()


def cmd_plan(args):
    import planner
    if not args.dir:
        sys.exit(t("s.err.dirreq"))
    wd = Path(args.dir).expanduser().resolve()
    if not wd.is_dir():
        sys.exit(t("s.err.dir", d=wd))
    add_dirs = []
    for d in args.add_dirs or []:
        pd = Path(d).expanduser().resolve()
        if not pd.is_dir():
            sys.exit(t("s.err.adddir", d=d))
        add_dirs.append(str(pd))

    try:
        job = planner.create_plan(
            args.request, str(wd), add_dirs=add_dirs,
            max_chunks=args.max_chunks, plan_model=args.plan_model,
            synthesis_model=args.synthesis_model,
            min_five=args.min_five, min_weekly=args.min_weekly,
            min_scoped=args.min_scoped, before_reset=args.before_reset,
        )
    except Exception as e:
        sys.exit(t("p.err.fail", e=e))

    lo, hi = planner.estimate_cost(job["chunks"])
    print("\n" + t("p.card.title", id=job["id"]))
    print(planner.format_job(job))
    print(t("p.card.estimate", lo=lo, hi=hi, pc=job["planning_cost"]))

    if args.yes:
        job["status"] = "running"
    elif sys.stdin.isatty():
        ans = input(t("p.confirm")).strip().lower()
        if ans not in ("y", "yes"):
            print(t("p.discarded"))
            return
        job["status"] = "running"
    # 비대화형 + --yes 없음 → 승인 대기로 저장 (대시보드나 `ais job <id> approve`로 승인)
    store.add_job(job)
    if job["status"] == "running":
        print(t("p.registered.running"))
    else:
        print(t("p.registered.awaiting", id=job["id"]))


def cmd_jobs(_args):
    import planner
    jobs = store.list_jobs()
    if not jobs:
        print(t("j.nolist"))
        return
    for j in jobs:
        print(planner.format_job(j))
        print()


JOB_TRANSITIONS = {
    "approve": ("awaiting_approval", "running"),
    "pause": ("running", "paused"),
    "resume": ("paused", "running"),
}


def cmd_job(args):
    import planner
    job = store.get_job(args.id)
    if not job:
        sys.exit(t("s.err.jobnotfound", id=args.id))
    if args.action == "cancel":
        if job["status"] in ("done", "cancelled"):
            sys.exit(t("s.err.jobaction", a=args.action, s=job["status"]))
        store.update_job(args.id, status="cancelled")
    elif args.action in JOB_TRANSITIONS:
        src, dst = JOB_TRANSITIONS[args.action]
        if job["status"] != src:
            sys.exit(t("s.err.jobaction", a=args.action, s=job["status"]))
        store.update_job(args.id, status=dst)
    print(planner.format_job(store.get_job(args.id)))


def cmd_web(args):
    import web
    print(t("s.web", p=args.port))
    try:
        web.serve(args.port)
    except OSError as e:
        sys.exit(t("d.web.fail", p=args.port, e=e))


def cmd_ui(_args):
    import subprocess
    subprocess.run(["open", "http://localhost:8787"])
    print(t("s.ui"))


def cmd_usage(_args):
    try:
        u = usage_mod.fetch_usage()
    except usage_mod.UsageError as e:
        print(t("r.usagefail", e=e))
        sys.exit(1)
    print(t("s.usage.5h", u=u.five_hour.utilization, r=u.five_hour.remaining_pct,
            t=u.five_hour.resets_at.astimezone().strftime("%Y-%m-%d %H:%M")))
    print(t("s.usage.7d", u=u.seven_day.utilization, r=u.seven_day.remaining_pct,
            t=u.seven_day.resets_at.astimezone().strftime("%Y-%m-%d %H:%M")))
    for s in u.scoped or []:
        print(t("s.usage.scoped", n=s.name, u=s.utilization, r=s.remaining_pct,
                t=s.resets_at.astimezone().strftime("%Y-%m-%d %H:%M")))


def main():
    parser = argparse.ArgumentParser(prog="ais", description=t("s.desc"))
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help=t("s.h.add"))
    p_add.add_argument("--prompt")
    p_add.add_argument("--dir")
    p_add.add_argument("--add-dir", action="append", dest="add_dirs")
    p_add.add_argument("--five-hour-remaining", type=float, dest="five_hour_remaining")
    p_add.add_argument("--before-reset", type=float, dest="before_reset")
    p_add.add_argument("--weekly-remaining", type=float, dest="weekly_remaining")
    p_add.add_argument("--day")
    p_add.add_argument("--time", dest="at")
    p_add.add_argument("--model", choices=wizard.MODEL_VALUES)
    p_add.add_argument("--effort", choices=wizard.EFFORT_VALUES)
    p_add.add_argument("--budget", type=float)
    p_add.add_argument("--min-scoped", type=float, dest="min_scoped",
                       help="모델 전용 위클리(예: Fable) 잔여 퍼센트 조건")
    p_add.set_defaults(func=cmd_add)

    p_plan = sub.add_parser("plan", help=t("s.h.plan"))
    p_plan.add_argument("request")
    p_plan.add_argument("--dir")
    p_plan.add_argument("--add-dir", action="append", dest="add_dirs")
    p_plan.add_argument("--max-chunks", type=int, default=12, dest="max_chunks")
    p_plan.add_argument("--plan-model", choices=wizard.MODEL_VALUES, dest="plan_model")
    p_plan.add_argument("--synthesis-model", choices=wizard.MODEL_VALUES, dest="synthesis_model")
    p_plan.add_argument("--min-five", type=float, default=30, dest="min_five")
    p_plan.add_argument("--min-weekly", type=float, default=40, dest="min_weekly")
    p_plan.add_argument("--min-scoped", type=float, dest="min_scoped")
    p_plan.add_argument("--before-reset", type=float, dest="before_reset",
                        help="5시간 리셋 N시간 전부터만 청크 실행 (생략 시 항상)")
    p_plan.add_argument("--yes", action="store_true")
    p_plan.set_defaults(func=cmd_plan)

    sub.add_parser("jobs", help=t("s.h.jobs")).set_defaults(func=cmd_jobs)
    p_job = sub.add_parser("job", help=t("s.h.job"))
    p_job.add_argument("id")
    p_job.add_argument("action", nargs="?", choices=["approve", "pause", "resume", "cancel"])
    p_job.set_defaults(func=cmd_job)

    sub.add_parser("list", help=t("s.h.list")).set_defaults(func=cmd_list)
    sub.add_parser("run-once", help=t("s.h.runonce")).set_defaults(func=cmd_run_once)
    sub.add_parser("usage", help=t("s.h.usage")).set_defaults(func=cmd_usage)

    p_start = sub.add_parser("start", help=t("s.h.start"))
    p_start.add_argument("--hours", type=float, default=1, help=t("s.h.hours"))
    p_start.set_defaults(func=lambda a: daemon.start(a.hours))
    sub.add_parser("stop", help=t("s.h.stop")).set_defaults(func=lambda a: daemon.stop())
    sub.add_parser("status", help=t("s.h.status")).set_defaults(func=lambda a: daemon.status())

    p_remove = sub.add_parser("remove", help=t("s.h.remove"))
    p_remove.add_argument("id")
    p_remove.set_defaults(func=cmd_remove)

    p_loop = sub.add_parser("run-loop")
    p_loop.add_argument("--hours", type=float, default=1)
    p_loop.set_defaults(func=lambda a: daemon.run_loop(a.hours))

    p_web = sub.add_parser("web", help=t("s.h.web"))
    p_web.add_argument("--port", type=int, default=8787)
    p_web.set_defaults(func=cmd_web)
    sub.add_parser("ui", help=t("s.h.ui")).set_defaults(func=cmd_ui)

    args = parser.parse_args()
    try:
        args.func(args)
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C / 입력 스트림 종료 시 traceback 없이 조용히 종료
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
