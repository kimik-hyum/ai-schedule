import json
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import runner
import store
import usage as usage_mod
from i18n import t
from wizard import EFFORT_VALUES, MODEL_VALUES, WEEKDAY_ALIASES

PROJECT_DIR = Path(__file__).resolve().parent
DASHBOARD = PROJECT_DIR / "dashboard.html"
OPEN_SESSION_HELPER = PROJECT_DIR / "open_session.sh"
DEFAULT_PORT = 8787

_usage_cache = {"data": None, "err": None, "ts": 0.0}


def _get_usage_cached(max_age: float = 60):
    if time.time() - _usage_cache["ts"] > max_age:
        try:
            _usage_cache.update(data=usage_mod.fetch_usage(), err=None, ts=time.time())
        except Exception as e:
            _usage_cache.update(data=None, err=str(e), ts=time.time())
    return _usage_cache["data"], _usage_cache["err"]


def _state() -> dict:
    u, err = _get_usage_cached()
    now = datetime.now()
    fired_reasons = (t("r.5h.fired"), t("r.w.fired"))
    tasks_out = []
    for task in store.list_tasks():
        if u:
            kind, ok, reason = runner.evaluate_task(task, u, now)
        else:
            kind, ok, reason = None, False, t("r.usagefail", e=err)
        tasks_out.append({
            **task, "kind": kind, "eligible": ok, "reason": reason,
            "fired": reason in fired_reasons,  # 언어 무관하게 '이번 윈도우 실행됨' 판별
        })

    usage_out = None
    if u:
        usage_out = {
            "five_hour": {"remaining_pct": u.five_hour.remaining_pct, "resets_at": u.five_hour.resets_at.isoformat()},
            "seven_day": {"remaining_pct": u.seven_day.remaining_pct, "resets_at": u.seven_day.resets_at.isoformat()},
            "scoped": [
                {"name": s.name, "remaining_pct": s.remaining_pct, "resets_at": s.resets_at.isoformat()}
                for s in (u.scoped or [])
            ],
        }

    jobs_out = []
    for j in store.list_jobs():
        if j["status"] == "cancelled":
            continue
        done = sum(1 for c in j["chunks"] if c["status"] == "ok")
        failed = sum(1 for c in j["chunks"] if c["status"] == "failed")
        quota_wait = False
        if j["status"] == "running" and u:
            p = j["policy"]
            quota_wait = not (u.five_hour.remaining_pct >= p["min_five_hour_pct"]
                              and u.seven_day.remaining_pct >= p["min_weekly_pct"])
        jobs_out.append({**j, "done": done, "failed_count": failed, "quota_wait": quota_wait})

    import daemon as daemon_mod
    pid = daemon_mod._alive_pid()
    return {
        "usage": usage_out,
        "usage_error": err,
        "daemon": {"running": bool(pid), "pid": pid},
        "tasks": tasks_out,
        "jobs": jobs_out,
        "history": store.list_history(),
        "now": now.isoformat(timespec="seconds"),
    }


def _build_task_from_payload(p: dict) -> dict:
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        raise ValueError(t("a.err.prompt"))
    wd = Path(p.get("dir") or "").expanduser()
    if not wd.is_dir():
        raise ValueError(t("s.err.dir", d=wd))

    mode = p.get("mode")
    five_hour = None
    weekly = None
    if mode == "five_hour":
        five_hour = {
            "enabled": True,
            "min_remaining_pct": float(p.get("min_remaining", 50)),
            "hours_before_reset": float(p.get("before_reset", 1)),
        }
    elif mode == "weekly":
        day = (p.get("day") or "").lower()
        if day not in WEEKDAY_ALIASES:
            raise ValueError(t("s.err.day", d=p.get("day")))
        t = p.get("time") or ""
        datetime.strptime(t, "%H:%M")
        weekly = {
            "enabled": True,
            "min_remaining_pct": float(p.get("min_remaining", 30)),
            "day_of_week": day,
            "time": t,
        }
    else:
        raise ValueError(t("a.err.mode"))

    add_dirs = []
    for d in p.get("add_dirs") or []:
        pd = Path(d).expanduser()
        if not pd.is_dir():
            raise ValueError(t("s.err.adddir", d=d))
        add_dirs.append(str(pd.resolve()))

    model = p.get("model") or None
    if model and model not in MODEL_VALUES:
        raise ValueError(t("a.err.model", m=model))
    effort = p.get("effort") or None
    if effort and effort not in EFFORT_VALUES:
        raise ValueError(t("a.err.effort", v=effort))
    budget = float(p["budget"]) if p.get("budget") else None
    min_scoped = float(p["min_scoped"]) if p.get("min_scoped") not in (None, "") else None

    return store.add_task(
        prompt=prompt, working_dir=str(wd.resolve()), add_dirs=add_dirs,
        five_hour=five_hour, weekly=weekly, model=model, effort=effort,
        max_budget_usd=budget, min_scoped_pct=min_scoped,
    )


def _recent_dirs() -> list:
    seen, out = set(), []
    for t in store.list_tasks():
        for d in [t["working_dir"], *(t.get("add_dirs") or [])]:
            if d not in seen:
                seen.add(d)
                out.append(d)
    for h in store.list_history():
        d = h.get("working_dir")
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out[:15]


def _plan_in_background(job_id: str, request_text: str, wd: str, kwargs: dict) -> None:
    import planner
    try:
        job = planner.create_plan(request_text, wd, job_id=job_id, **kwargs)
    except Exception as e:
        cur = store.get_job(job_id)
        if cur and cur["status"] == "planning":
            store.update_job(job_id, status="plan_failed", plan_error=str(e)[:200])
        return
    cur = store.get_job(job_id)
    if not cur or cur["status"] != "planning":  # 계획 중 사용자가 취소한 경우
        return
    store.update_job(
        job_id, status="awaiting_approval",
        chunks=job["chunks"], output_dir=job["output_dir"],
        total_cost=job["total_cost"], planning_cost=job["planning_cost"],
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 액세스 로그로 스케줄러 로그가 오염되지 않도록
        pass

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    def do_GET(self):
        path = self.path.split("?", 1)[0]  # 쿼리스트링(?lang=en 등) 제거
        if path == "/" or path == "/index.html":
            body = DASHBOARD.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/state":
            self._json(_state())
        elif path == "/api/recent-dirs":
            self._json({"dirs": _recent_dirs()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        try:
            if self.path.startswith("/api/jobs/"):
                parts = self.path.strip("/").split("/")  # api / jobs / <id> / <action>
                if len(parts) != 4:
                    self._json({"error": "not found"}, 404)
                    return
                job_id, action = parts[2], parts[3]
                job = store.get_job(job_id)
                if not job:
                    self._json({"ok": False, "error": t("s.err.jobnotfound", id=job_id)}, 404)
                    return
                transitions = {
                    "approve": ("awaiting_approval", "running"),
                    "pause": ("running", "paused"),
                    "resume": ("paused", "running"),
                }
                if action == "cancel" and job["status"] not in ("done", "cancelled"):
                    store.update_job(job_id, status="cancelled")
                    self._json({"ok": True})
                elif action in transitions and job["status"] == transitions[action][0]:
                    store.update_job(job_id, status=transitions[action][1])
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": t("s.err.jobaction", a=action, s=job["status"])}, 400)
            elif self.path == "/api/plan":
                import uuid as uuid_mod

                import planner
                from wizard import MODEL_VALUES as _MV
                p = self._read_body()
                request_text = (p.get("request") or "").strip()
                if not request_text:
                    raise ValueError(t("a.err.prompt"))
                wd = Path(p.get("dir") or "").expanduser()
                if not wd.is_dir():
                    raise ValueError(t("s.err.dir", d=wd))
                add_dirs = []
                for d in p.get("add_dirs") or []:
                    pd = Path(d).expanduser()
                    if not pd.is_dir():
                        raise ValueError(t("s.err.adddir", d=d))
                    add_dirs.append(str(pd.resolve()))
                for key in ("plan_model", "synthesis_model"):
                    if p.get(key) and p[key] not in _MV:
                        raise ValueError(t("a.err.model", m=p[key]))

                job_id = uuid_mod.uuid4().hex[:8]
                min_five = float(p.get("min_five") or 30)
                min_weekly = float(p.get("min_weekly") or 40)
                min_scoped = float(p["min_scoped"]) if p.get("min_scoped") not in (None, "") else None
                store.add_job({
                    "id": job_id, "request": request_text,
                    "working_dir": str(wd.resolve()), "add_dirs": add_dirs,
                    "status": "planning",
                    "policy": {"min_five_hour_pct": min_five, "min_weekly_pct": min_weekly,
                               "min_scoped_pct": min_scoped},
                    "output_dir": str(planner.JOBS_DIR / job_id),
                    "chunks": [],
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "total_cost": 0, "planning_cost": 0,
                })
                kwargs = dict(
                    add_dirs=add_dirs, max_chunks=int(p.get("max_chunks") or 12),
                    plan_model=p.get("plan_model") or None,
                    synthesis_model=p.get("synthesis_model") or None,
                    min_five=min_five, min_weekly=min_weekly, min_scoped=min_scoped,
                )
                threading.Thread(
                    target=_plan_in_background,
                    args=(job_id, request_text, str(wd.resolve()), kwargs),
                    daemon=True,
                ).start()
                self._json({"ok": True, "job_id": job_id})
            elif self.path == "/api/open-path":
                import planner
                p = self._read_body()
                path = Path(p["path"]).resolve()
                if not str(path).startswith(str(planner.JOBS_DIR)):  # 잡 결과 폴더만 허용
                    self._json({"ok": False, "error": "forbidden"}, 403)
                    return
                subprocess.Popen(["open", str(path)])
                self._json({"ok": True})
            elif self.path == "/api/tasks":
                task = _build_task_from_payload(self._read_body())
                self._json({"ok": True, "task": task})
            elif self.path == "/api/run-once":
                threading.Thread(target=runner.run_once, daemon=True).start()
                self._json({"ok": True, "message": t("a.runonce")})
            elif self.path == "/api/pick-folder":
                # macOS 네이티브 Finder 폴더 선택 다이얼로그 (최초 사용 시 자동화 권한 승인 필요할 수 있음)
                r = subprocess.run(
                    ["osascript",
                     "-e", 'tell application "Finder" to activate',
                     "-e", 'tell application "Finder" to set f to (choose folder with prompt "작업 폴더를 선택하세요")',
                     "-e", "POSIX path of f"],
                    capture_output=True, text=True, timeout=180,
                )
                if r.returncode != 0:
                    self._json({"ok": False, "cancelled": True})
                else:
                    self._json({"ok": True, "path": r.stdout.strip().rstrip("/")})
            elif self.path == "/api/open-session":
                p = self._read_body()
                claude_bin = usage_mod.resolve_claude_binary()
                subprocess.Popen(
                    [str(OPEN_SESSION_HELPER), p["working_dir"], p["session_id"], claude_bin],
                )
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except (ValueError, KeyError) as e:
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            self._json({"ok": False, "error": t("a.err.server", e=e)}, 500)

    def do_DELETE(self):
        if self.path.startswith("/api/tasks/"):
            task_id = self.path.rsplit("/", 1)[-1]
            removed = store.remove_task(task_id)
            self._json({"ok": removed})
        else:
            self._json({"error": "not found"}, 404)


def serve(port: int = DEFAULT_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # 로컬 전용 바인딩
    server.serve_forever()


def start_in_thread(port: int = DEFAULT_PORT) -> bool:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        print(t("d.web.fail", p=port, e=e))
        return False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(t("d.web.on", p=port))
    return True
