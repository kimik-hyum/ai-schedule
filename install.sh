#!/bin/bash
# AI Schedule installer — creates the `ais` command, and optionally the app and Claude Code skill.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "✗ AI Schedule requires macOS (Keychain / notifications / Finder integration)."
  exit 1
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "✗ Claude Code not found. Install it and log in first: https://claude.com/claude-code"
  exit 1
fi

if ! command -v terminal-notifier >/dev/null 2>&1; then
  echo "ℹ (optional) 'brew install terminal-notifier' enables click-to-open-session notifications."
fi

# ---- 1. global `ais` command ----
BIN=""
for CAND in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  if [[ -d "$CAND" && -w "$CAND" ]]; then BIN="$CAND"; break; fi
done
if [[ -z "$BIN" ]]; then
  BIN="$HOME/.local/bin"
  mkdir -p "$BIN"
  echo "ℹ Installed to $BIN — make sure it is on your PATH."
fi

cat > "$BIN/ais" <<WRAP
#!/bin/bash
# AI Schedule — quota-aware task scheduler for Claude Code
exec /usr/bin/python3 "$DIR/scheduler.py" "\$@"
WRAP
chmod +x "$BIN/ais"
echo "✓ Command installed: $BIN/ais"

# ---- 2. optional: double-clickable app ----
read -r -p "Create 'AI Schedule.app' (double-click = start daemon + open dashboard)? [y/N] " YN
if [[ "$YN" == y* || "$YN" == Y* ]]; then
  APP_DIR="/Applications"
  [[ -w "$APP_DIR" ]] || APP_DIR="$HOME/Applications"
  mkdir -p "$APP_DIR"
  osacompile -o "$APP_DIR/AI Schedule.app" <<APPLESCRIPT
do shell script "'$BIN/ais' start >/dev/null 2>&1; sleep 1"
do shell script "open 'http://localhost:8787'"
APPLESCRIPT
  echo "✓ App created: $APP_DIR/AI Schedule.app"
  echo "  (add it to System Settings → Login Items to auto-start after reboot)"
fi

# ---- 3. optional: Claude Code skill ----
read -r -p "Install the Claude Code skill (lets Claude schedule tasks for you)? [y/N] " YN
if [[ "$YN" == y* || "$YN" == Y* ]]; then
  SKILL_DIR="$HOME/.claude/skills/ai-schedule"
  mkdir -p "$SKILL_DIR"
  cp "$DIR/skill/SKILL.md" "$SKILL_DIR/SKILL.md"
  echo "✓ Skill installed: $SKILL_DIR (available in new Claude Code sessions)"
fi

echo ""
echo "Done! Try:"
echo "  ais usage   # check your quota"
echo "  ais add     # schedule a task"
echo "  ais start   # turn on auto-run"
