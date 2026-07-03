#!/bin/bash
# 알림 클릭 시 terminal-notifier가 호출하는 헬퍼.
# 새 Terminal 창에서 해당 세션을 claude --resume으로 연다.
# 사용법: open_session.sh <working_dir> <session_id> <claude_binary_path>
WD="$1"
SID="$2"
CLAUDE_BIN="$3"

# GUI 컨텍스트에서 실행되므로 PATH에 nvm 경로가 없을 수 있음 → 인자로 받은 절대경로 우선
if [ -z "$CLAUDE_BIN" ] || [ ! -x "$CLAUDE_BIN" ]; then
    CLAUDE_BIN="$(command -v claude)"
fi

/usr/bin/osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$WD' && '$CLAUDE_BIN' --resume '$SID'"
end tell
EOF
