import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from i18n import t

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = PROJECT_DIR / "logs"
LOG_FILE = LOG_DIR / "scheduler.log"
PID_FILE = LOG_DIR / "daemon.pid"
LEGACY_LABEL = "com.ai-schedule.runner"


def _cleanup_legacy_launchd():
    # 초기 버전이 쓰던 launchd 등록이 남아있으면 제거 (Documents 접근 권한 문제로 폐기됨)
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_LABEL}"], capture_output=True)
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LEGACY_LABEL}.plist"
    if plist.exists():
        plist.unlink()


def _alive_pid():
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)  # 생존 확인만
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def start(hours: float) -> None:
    _cleanup_legacy_launchd()
    pid = _alive_pid()
    if pid:
        print(t("d.already", p=pid))
        return
    LOG_DIR.mkdir(exist_ok=True)
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5_000_000:
        LOG_FILE.replace(LOG_FILE.with_name("scheduler.log.1"))  # 단순 로테이션 (무한 증가 방지)
    log = open(LOG_FILE, "a")
    proc = subprocess.Popen(
        # -u: 출력 버퍼링 해제 — 작업 진행 중에도 로그가 실시간으로 기록되도록
        [sys.executable, "-u", str(PROJECT_DIR / "scheduler.py"), "run-loop", "--hours", str(hours)],
        stdout=log, stderr=log, start_new_session=True, cwd=PROJECT_DIR,
    )
    PID_FILE.write_text(str(proc.pid))
    print(t("d.on", h=hours, p=proc.pid))
    print(t("d.first"))
    print(t("d.dash"))
    print(t("d.status"))
    print(t("d.stopcmd"))
    print(t("d.reboot"))


def stop() -> None:
    _cleanup_legacy_launchd()
    pid = _alive_pid()
    if not pid:
        print(t("d.notrunning"))
        if PID_FILE.exists():
            PID_FILE.unlink()
        return
    try:
        # start_new_session으로 띄웠으므로 pgid == pid — 실행 중인 claude 자식까지 함께 종료
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        os.kill(pid, signal.SIGTERM)
    if PID_FILE.exists():
        PID_FILE.unlink()
    print(t("d.off", p=pid))


def status() -> None:
    pid = _alive_pid()
    if pid:
        print(t("d.st.on", p=pid))
    else:
        print(t("d.st.off"))
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
        print("\n" + t("d.log"))
        for line in lines[-15:]:
            print(line)


def run_loop(hours: float) -> None:
    import runner
    import web

    web.start_in_thread()  # 대시보드 서버를 같은 프로세스에서 서빙
    interval = hours * 3600
    while True:
        try:
            runner.run_once()
        except Exception as e:
            print(t("d.loop.err", e=e))
        sys.stdout.flush()
        time.sleep(interval)
