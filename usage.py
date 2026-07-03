import json
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


class UsageError(RuntimeError):
    pass


def _get_oauth_token() -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise UsageError(f"macOS Keychain에서 Claude Code 인증정보를 읽지 못했습니다: {e}")

    try:
        creds = json.loads(result.stdout)
        return creds["claudeAiOauth"]["accessToken"]
    except (json.JSONDecodeError, KeyError) as e:
        raise UsageError(f"인증정보 파싱 실패: {e}")


@dataclass
class WindowUsage:
    utilization: float
    resets_at: datetime

    @property
    def remaining_pct(self) -> float:
        return 100.0 - self.utilization

    @property
    def seconds_to_reset(self) -> float:
        return (self.resets_at - datetime.now(timezone.utc)).total_seconds()


@dataclass
class Usage:
    five_hour: WindowUsage
    seven_day: WindowUsage


def fetch_usage() -> Usage:
    token = _get_oauth_token()
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            # 이 UA가 없으면 훨씬 엄격한 rate-limit 버킷에 걸려 429가 날 수 있음 (커뮤니티 확인 사항)
            "User-Agent": "claude-code/2.1.197",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        # 비공식 엔드포인트이므로 스키마/가용성이 예고 없이 바뀔 수 있음
        raise UsageError(f"사용량 조회 실패: {e}")

    def parse(key: str) -> WindowUsage:
        w = data[key]
        return WindowUsage(utilization=w["utilization"], resets_at=datetime.fromisoformat(w["resets_at"]))

    return Usage(five_hour=parse("five_hour"), seven_day=parse("seven_day"))


def resolve_claude_binary() -> str:
    path = shutil.which("claude")
    if path:
        return path
    # GUI/데몬 컨텍스트에서는 PATH에 설치 경로가 없을 수 있어 알려진 위치를 순서대로 탐색
    from pathlib import Path
    candidates = [
        Path.home() / ".local/bin/claude",
        Path("/opt/homebrew/bin/claude"),
        Path("/usr/local/bin/claude"),
        *sorted(Path.home().glob(".nvm/versions/node/*/bin/claude"), reverse=True),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    raise UsageError("claude binary not found (checked PATH and known install locations)")
