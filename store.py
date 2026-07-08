import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 기본은 프로젝트 폴더의 tasks.json. AIS_STORE로 재정의하면 격리된 인스턴스(데모/테스트) 실행 가능
STORE_PATH = Path(os.environ.get("AIS_STORE") or (Path(__file__).resolve().parent / "tasks.json"))
# 데몬 루프와 웹 서버 스레드가 같은 프로세스에서 읽기-수정-쓰기를 하므로 락 필수
_LOCK = threading.RLock()


def _load_raw() -> dict:
    if not STORE_PATH.exists():
        return {"tasks": []}
    with open(STORE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(data: dict) -> None:
    tmp_path = STORE_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STORE_PATH)


def list_tasks() -> list:
    with _LOCK:
        return _load_raw()["tasks"]


def get_task(task_id: str):
    for t in list_tasks():
        if t["id"] == task_id:
            return t
    return None


def add_task(prompt: str, working_dir: str, five_hour=None, weekly=None, model=None,
             effort=None, max_budget_usd=None, add_dirs=None, min_scoped_pct=None) -> dict:
    task = {
        "id": uuid.uuid4().hex[:8],
        "prompt": prompt,
        "working_dir": working_dir,
        "add_dirs": add_dirs or [],
        "five_hour": five_hour,
        "weekly": weekly,
        "model": model,
        "effort": effort,
        "max_budget_usd": max_budget_usd,
        "min_scoped_pct": min_scoped_pct,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_session_id": None,
        "last_fired": {"five_hour_reset": None, "weekly_key": None},
    }
    with _LOCK:
        data = _load_raw()
        data["tasks"].append(task)
        _save_raw(data)
    return task


def remove_task(task_id: str) -> bool:
    with _LOCK:
        data = _load_raw()
        before = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if t["id"] != task_id]
        _save_raw(data)
        return len(data["tasks"]) < before


def update_task(task_id: str, **fields) -> None:
    with _LOCK:
        data = _load_raw()
        for t in data["tasks"]:
            if t["id"] == task_id:
                t.update(fields)
                break
        _save_raw(data)


def add_job(job: dict) -> None:
    with _LOCK:
        data = _load_raw()
        data.setdefault("jobs", []).append(job)
        _save_raw(data)


def list_jobs() -> list:
    with _LOCK:
        return _load_raw().get("jobs", [])


def get_job(job_id: str):
    for j in list_jobs():
        if j["id"] == job_id:
            return j
    return None


def update_job(job_id: str, **fields) -> None:
    with _LOCK:
        data = _load_raw()
        for j in data.get("jobs", []):
            if j["id"] == job_id:
                j.update(fields)
                break
        _save_raw(data)


def update_chunk(job_id: str, idx: int, **fields) -> None:
    with _LOCK:
        data = _load_raw()
        for j in data.get("jobs", []):
            if j["id"] == job_id:
                for c in j["chunks"]:
                    if c["idx"] == idx:
                        c.update(fields)
                        break
                break
        _save_raw(data)


def remove_job(job_id: str) -> bool:
    with _LOCK:
        data = _load_raw()
        before = len(data.get("jobs", []))
        data["jobs"] = [j for j in data.get("jobs", []) if j["id"] != job_id]
        _save_raw(data)
        return len(data["jobs"]) < before


def add_history(record: dict) -> None:
    with _LOCK:
        data = _load_raw()
        data.setdefault("history", []).insert(0, record)
        data["history"] = data["history"][:100]  # 최근 100건만 보관
        _save_raw(data)


def list_history() -> list:
    with _LOCK:
        return _load_raw().get("history", [])
