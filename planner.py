"""대형 작업(Job) 플래너.

큰 요청을 받아 ① 인벤토리 수집(haiku) ② 청크 분해(강한 모델) 두 단계로 실행 계획을 만든다.
계획은 사용자 승인 전에는 절대 실행되지 않는다.
"""
import json
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import usage as usage_mod
from i18n import t
from wizard import EFFORT_VALUES, MODEL_VALUES

PROJECT_DIR = Path(__file__).resolve().parent
JOBS_DIR = PROJECT_DIR / "jobs"

# 청크당 대략적 비용 범위(달러) — 승인 카드의 예상치 표시에만 사용
COST_RANGE = {
    "haiku": (0.05, 0.3),
    "sonnet": (0.2, 0.8),
    "opus": (0.5, 2.0),
    "fable": (0.5, 2.5),
    None: (0.4, 1.5),
}

INVENTORY_PROMPT = """You are indexing a codebase to help plan a larger job.
Job request: "{request}"

Survey the current working directory{extra} and list the sub-projects or major modules relevant to the request.
Output ONLY JSON, no prose:
{{"items": [{{"name": "...", "path": "relative/or/absolute/path", "note": "one line"}}]}}
Maximum 25 items."""

PLAN_PROMPT = """You are splitting a large job into independent execution chunks.
Each chunk will later run as a SEPARATE headless Claude Code session with no shared memory — results are written to files and synthesized at the end.

Job request: "{request}"
Working directory: {wd}
Inventory of the codebase:
{inventory}

Rules:
- At most {max_chunks} chunks. Fewer is better if chunks stay focused.
- Each chunk prompt must be fully self-contained: name the exact directories/paths to examine. Never reference other chunks.
- Chunks do analysis/reporting; they must NOT modify project files unless the job request explicitly asks for changes.
- Pick the cheapest sufficient model per chunk: "haiku" (simple, mechanical), "sonnet" (moderate reasoning), null (complex — default model).
- Optionally set "effort": "low" for mechanical chunks, otherwise null.
- Do NOT add a synthesis/summary chunk — it is appended automatically.
Output ONLY JSON, no prose:
{{"chunks": [{{"title": "short title", "prompt": "complete self-contained instruction", "model": "haiku" | "sonnet" | null, "effort": "low" | null}}]}}"""


def _run_claude(prompt: str, cwd: str, model=None, effort=None, add_dirs=(), timeout=600) -> dict:
    # 계획 단계는 읽기 전용이어야 하므로 acceptEdits를 쓰지 않는다
    cmd = [usage_mod.resolve_claude_binary(), "-p", prompt, "--output-format", "json", "--permission-mode", "default"]
    for d in add_dirs:
        cmd += ["--add-dir", d]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    payload = json.loads(result.stdout)
    if payload.get("is_error"):
        raise RuntimeError(str(payload.get("result"))[:300])
    return payload


def _extract_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text.strip(), re.S)
    if not m:
        raise ValueError(t("p.err.json"))
    return json.loads(m.group(0))


def estimate_cost(chunks: list) -> tuple:
    lo = hi = 0.0
    for c in chunks:
        a, b = COST_RANGE.get(c.get("model"), COST_RANGE[None])
        lo += a
        hi += b
    return lo, hi


def create_plan(request: str, working_dir: str, add_dirs=(), max_chunks: int = 12,
                plan_model=None, synthesis_model=None,
                min_five: float = 30.0, min_weekly: float = 40.0,
                min_scoped=None, before_reset=None, job_id=None) -> dict:
    wd = str(Path(working_dir).expanduser().resolve())

    print(t("p.phase.inventory"))
    extra = f" and also these directories: {', '.join(add_dirs)}" if add_dirs else ""
    inv_payload = _run_claude(
        INVENTORY_PROMPT.format(request=request, extra=extra),
        cwd=wd, model="haiku", effort="low", add_dirs=add_dirs,
    )
    inventory = _extract_json(str(inv_payload.get("result")))
    inv_cost = inv_payload.get("total_cost_usd") or 0

    print(t("p.phase.plan"))
    plan_payload = _run_claude(
        PLAN_PROMPT.format(
            request=request, wd=wd, max_chunks=max_chunks,
            inventory=json.dumps(inventory, ensure_ascii=False, indent=1)[:6000],
        ),
        cwd=wd, model=plan_model, add_dirs=add_dirs, timeout=900,
    )
    plan = _extract_json(str(plan_payload.get("result")))
    plan_cost = plan_payload.get("total_cost_usd") or 0

    job_id = job_id or uuid.uuid4().hex[:8]
    out_dir = JOBS_DIR / job_id

    def _blank(idx, title, prompt, model, effort, synthesis, filename):
        return {
            "idx": idx, "title": title, "prompt": prompt,
            "model": model, "effort": effort, "synthesis": synthesis,
            "status": "pending", "attempts": 0, "cost": None, "session_id": None,
            "output_file": str(out_dir / filename), "started_at": None, "finished_at": None,
        }

    chunks = []
    for i, c in enumerate(plan.get("chunks", [])[:max_chunks], start=1):
        model = c.get("model") if c.get("model") in MODEL_VALUES else None
        effort = c.get("effort") if c.get("effort") in EFFORT_VALUES else None
        chunks.append(_blank(i, str(c.get("title", f"chunk {i}"))[:80], str(c.get("prompt", "")),
                             model, effort, False, f"{i:02d}.md"))
    if not chunks:
        raise RuntimeError(t("p.err.empty"))
    chunks.append(_blank(len(chunks) + 1, "Final synthesis report", "",
                         synthesis_model, None, True, "report.md"))

    return {
        "id": job_id,
        "request": request,
        "working_dir": wd,
        "add_dirs": list(add_dirs),
        "status": "awaiting_approval",
        "policy": {"min_five_hour_pct": min_five, "min_weekly_pct": min_weekly,
                   "min_scoped_pct": min_scoped, "before_reset_hours": before_reset},
        "output_dir": str(out_dir),
        "chunks": chunks,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "total_cost": round(inv_cost + plan_cost, 4),
        "planning_cost": round(inv_cost + plan_cost, 4),
    }


STATUS_LABEL_KEYS = {
    "planning": "j.st.planning",
    "plan_failed": "j.st.plan_failed",
    "awaiting_approval": "j.st.awaiting",
    "running": "j.st.running",
    "paused": "j.st.paused",
    "done": "j.st.done",
    "failed": "j.st.failed",
    "cancelled": "j.st.cancelled",
}


def format_job(job: dict) -> str:
    chunks = job["chunks"]
    done = sum(1 for c in chunks if c["status"] == "ok")
    failed = sum(1 for c in chunks if c["status"] == "failed")
    lines = [f"[{job['id']}] {job['request']}"]
    lines.append(t("j.f.status", v=t(STATUS_LABEL_KEYS.get(job["status"], job["status"]))))
    lines.append(t("f.dir", v=job["working_dir"]))
    if job.get("add_dirs"):
        lines.append(t("f.adddirs", v=", ".join(job["add_dirs"])))
    policy_line = t("j.f.policy", f5=job["policy"]["min_five_hour_pct"], f7=job["policy"]["min_weekly_pct"])
    if job["policy"].get("min_scoped_pct") is not None:
        policy_line += t("j.f.policy.scoped", v=job["policy"]["min_scoped_pct"])
    if job["policy"].get("before_reset_hours") is not None:
        policy_line += t("j.f.policy.reset", v=job["policy"]["before_reset_hours"])
    lines.append(policy_line)
    lines.append(t("j.f.progress", d=done, n=len(chunks), f=failed, c=job.get("total_cost") or 0))
    lines.append(t("j.f.outdir", v=job["output_dir"]))
    for c in chunks:
        mark = {"ok": "✓", "failed": "✗", "running": "▶"}.get(c["status"], "·")
        model = c.get("model") or "default"
        eff = f"·{c['effort']}" if c.get("effort") else ""
        lines.append(f"    {mark} {c['idx']:02d}. {c['title']}  ({model}{eff})")
    return "\n".join(lines)
