"""대형 작업(Job) 큐 실행기.

데몬의 run_once 끝에서 호출되어, 실행 중(running) 상태의 잡을 처리한다.
큐 정책(5시간/위클리 잔여 %)이 충족되는 동안 청크를 순차 실행하고,
청크마다 사용량을 다시 조회해 정책이 깨지면 즉시 멈춘다(다음 윈도우에 자동 재개).
각 청크는 새 세션으로 실행되며 결과를 파일로 남긴다 — 파일이 곧 기억이다.
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

import notify
import store
import usage as usage_mod
from i18n import t

CHUNK_TIMEOUT = 900       # 청크당 최대 15분
MAX_ATTEMPTS = 2          # 청크당 최대 시도 횟수 (초과 시 해당 청크 포기, 종합에 누락 명시)
MAX_BURST_FAILURES = 2    # 한 버스트에서 실패 2회면 중단 (다음 윈도우에 재시도)

OUTPUT_INSTRUCTION = """

=== Output requirement ===
Write your complete result as Markdown to this exact file (create or overwrite it): {out}
That file is the only thing kept from this session — it must contain your full findings.
Do not modify project files unless the task above explicitly requires it."""

SYNTHESIS_PROMPT = """You are writing the final synthesis report for a larger job that was executed in independent chunks.

Original request: "{request}"

Read these chunk result files and synthesize them into one coherent, well-structured report:
{files}
{missing}
Write the final report as Markdown to this exact file: {out}"""


def _policy_ok(job: dict, u) -> bool:
    p = job["policy"]
    return (u.five_hour.remaining_pct >= p["min_five_hour_pct"]
            and u.seven_day.remaining_pct >= p["min_weekly_pct"])


def _terminal_failed(c: dict) -> bool:
    return c["status"] == "failed" and c["attempts"] >= MAX_ATTEMPTS


def _next_chunk(job: dict, tried: set):
    normal = [c for c in job["chunks"] if not c.get("synthesis")]
    for c in normal:
        if c["idx"] in tried:
            continue
        if c["status"] == "pending" or (c["status"] == "failed" and c["attempts"] < MAX_ATTEMPTS):
            return c
    # 일반 청크가 모두 종결(성공 또는 포기)됐을 때만 종합 청크 실행
    if all(c["status"] == "ok" or _terminal_failed(c) for c in normal):
        for c in job["chunks"]:
            if c.get("synthesis") and c["idx"] not in tried:
                if c["status"] == "pending" or (c["status"] == "failed" and c["attempts"] < MAX_ATTEMPTS):
                    return c
    return None


def _build_prompt(job: dict, chunk: dict) -> str:
    out = chunk["output_file"]
    if chunk.get("synthesis"):
        ok_files = [c["output_file"] for c in job["chunks"] if not c.get("synthesis") and c["status"] == "ok"]
        failed = [f"{c['idx']:02d}. {c['title']}" for c in job["chunks"]
                  if not c.get("synthesis") and _terminal_failed(c)]
        missing = ""
        if failed:
            missing = "\nNote — these chunks FAILED and their results are missing; state this clearly in the report:\n" + "\n".join(failed) + "\n"
        return SYNTHESIS_PROMPT.format(request=job["request"], files="\n".join(ok_files), missing=missing, out=out)
    return chunk["prompt"] + OUTPUT_INSTRUCTION.format(out=out)


def execute_chunk(job: dict, chunk: dict) -> dict:
    out = Path(chunk["output_file"])
    out.parent.mkdir(parents=True, exist_ok=True)

    cmd = [usage_mod.resolve_claude_binary(), "-p", _build_prompt(job, chunk),
           "--output-format", "json", "--permission-mode", "acceptEdits",
           "--add-dir", str(out.parent)]  # 결과 파일은 작업폴더 밖에 있으므로 접근 허용 필요
    for d in job.get("add_dirs") or []:
        cmd += ["--add-dir", d]
    if chunk.get("model"):
        cmd += ["--model", chunk["model"]]
    if chunk.get("effort"):
        cmd += ["--effort", chunk["effort"]]

    print(t("j.chunk.run", i=chunk["idx"], n=len(job["chunks"]), title=chunk["title"],
            m=chunk.get("model") or t("r.default")))
    store.update_chunk(job["id"], chunk["idx"], status="running",
                       started_at=datetime.now().isoformat(timespec="seconds"))
    ok = False
    cost = 0.0
    session_id = None
    try:
        result = subprocess.run(cmd, cwd=job["working_dir"], capture_output=True, text=True,
                                timeout=CHUNK_TIMEOUT)
        payload = json.loads(result.stdout)
        cost = payload.get("total_cost_usd") or 0
        session_id = payload.get("session_id")
        # 검증: 에러가 아니고, 결과 파일이 실제로 의미 있는 크기로 존재해야 성공
        ok = (not payload.get("is_error")) and out.exists() and out.stat().st_size > 50
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass

    store.update_chunk(
        job["id"], chunk["idx"],
        status="ok" if ok else "failed",
        attempts=chunk["attempts"] + (0 if ok else 1),
        cost=round(cost, 4), session_id=session_id,
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )
    total = round((store.get_job(job["id"]).get("total_cost") or 0) + cost, 4)
    store.update_job(job["id"], total_cost=total)
    print(t("j.chunk.ok", c=cost) if ok else t("j.chunk.fail"))
    return {"ok": ok, "cost": cost}


def process_jobs() -> None:
    for job in store.list_jobs():
        if job["status"] == "running":
            _process_one(job["id"])


def _process_one(job_id: str) -> None:
    tried = set()
    burst_ok = 0
    burst_fail = 0
    burst_cost = 0.0
    quota_paused = False

    while True:
        job = store.get_job(job_id)
        if not job or job["status"] != "running":
            break
        try:
            u = usage_mod.fetch_usage()  # 청크마다 재조회 — 정책이 깨지는 즉시 멈추기 위해
        except usage_mod.UsageError as e:
            print(t("r.usagefail", e=e))
            break
        if not _policy_ok(job, u):
            quota_paused = True
            break
        chunk = _next_chunk(job, tried)
        if chunk is None:
            break
        tried.add(chunk["idx"])
        res = execute_chunk(job, chunk)
        burst_cost += res["cost"]
        if res["ok"]:
            burst_ok += 1
        else:
            burst_fail += 1
            if burst_fail >= MAX_BURST_FAILURES:
                break

    job = store.get_job(job_id)
    if not job:
        return
    synth = next(c for c in job["chunks"] if c.get("synthesis"))
    if synth["status"] == "ok":
        store.update_job(job_id, status="done")
        notify.notify_job_done(job, synth["output_file"])
        print(t("j.done", id=job_id))
    elif _terminal_failed(synth):
        store.update_job(job_id, status="failed")
        notify.notify_job_failed(job)
    elif burst_ok or burst_fail:
        done = sum(1 for c in job["chunks"] if c["status"] == "ok")
        notify.notify_job_digest(job, burst_ok, burst_fail, burst_cost, done, len(job["chunks"]))
        if quota_paused:
            print(t("j.quota.wait", id=job_id))
