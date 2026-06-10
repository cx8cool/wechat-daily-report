#!/bin/bash
# WeChat Daily Report — First-time setup wizard

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; }

echo ""
echo "========================================="
echo "   WeChat Daily Report — Setup Wizard"
echo "========================================="
echo ""

# ── 1. Python ──────────────────────────────────────────────────────────────

echo "[ 1/4 ] Checking Python..."
if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org"
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 9 ]; then
  fail "Python $PY_VER found, need 3.9+. Please upgrade."
  exit 1
fi
ok "Python $PY_VER"

# ── 2. wechat-cli ──────────────────────────────────────────────────────────

echo ""
echo "[ 2/4 ] Checking wechat-cli..."
if ! command -v wechat-cli &>/dev/null; then
  warn "wechat-cli not installed. Installing now..."
  npm install -g @canghe_ai/wechat-cli
fi

# Quick connectivity test
TEST=$(wechat-cli sessions --limit 1 2>/dev/null)
if echo "$TEST" | grep -q '"ok":true'; then
  ok "wechat-cli connected to WeChat"
else
  warn "wechat-cli installed but can't read WeChat data."
  echo ""
  echo "  Make sure WeChat is running, then run:"
  echo "  sudo wechat-cli init"
  echo ""
  echo "  Then re-run this setup script."
  exit 1
fi

# ── 3. Python deps ─────────────────────────────────────────────────────────

echo ""
echo "[ 3/4 ] Installing Python dependencies..."
pip3 install -r requirements.txt -q
ok "anthropic installed"

# ── 4. Config ──────────────────────────────────────────────────────────────

echo ""
echo "[ 4/4 ] Setting up config..."

if [ -f config.json ]; then
  ok "config.json already exists — skipping"
else
  cp config.example.json config.json
  echo ""
  echo "  Created config.json. Now fill in your WeChat ID."
  echo ""
  echo "  Your recent WeChat account folders:"
  ls -lt ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/ 2>/dev/null \
    | grep -v "^total" | grep -v "all_users" | grep -v "Backup" | grep -v "old_backup" \
    | awk '{print "    " $NF}' | head -3
  echo ""
  echo "  → Open config.json and set self_wxid to the folder name above."
  echo "  → Add your group names to priority_groups."
  echo "  → (Optional) Add your Anthropic API key for AI summaries."
fi

# ── Done ───────────────────────────────────────────────────────────────────

echo ""
echo "========================================="
ok "Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Edit config.json (fill in self_wxid and groups)"
echo "  2. Run: python3 daily_report.py"
echo ""
echo "  Optional — enable full AI analysis:"
echo "  Run: wxkey bootstrap   (enter your Mac password)"
echo "  Then add anthropic_api_key to config.json"
echo "========================================="
echo ""
