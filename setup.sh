#!/bin/bash
# 微信日报 — 首次配置向导
# 运行方式：bash setup.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
fail() { echo -e "${RED}❌ $1${NC}"; exit 1; }
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
step() { echo -e "\n${BOLD}[ $1 ]${NC} $2"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${BOLD}=================================================${NC}"
echo -e "${BOLD}       微信日报 — 一键配置向导                   ${NC}"
echo -e "${BOLD}=================================================${NC}"
echo ""
echo "  这个工具每天自动读取你的微信私聊数据，"
echo "  用 AI 分析哪些消息最重要，生成一份本地日报。"
echo ""
echo "  配置大约需要 5 分钟，全程引导，跟着走就行。"
echo ""
read -p "  按回车开始配置..." _

# ── Step 1: Python ─────────────────────────────────────────────────────────

step "1/5" "检查 Python 环境"

if ! command -v python3 &>/dev/null; then
  fail "未找到 Python 3。请先安装：https://www.python.org/downloads/"
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
PY_MINOR=$(echo $PY_VER | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MINOR" -lt 9 ]; then
  fail "Python $PY_VER 版本太低，需要 3.9 或以上。请升级后重新运行。"
fi
ok "Python $PY_VER"

echo "  安装 Python 依赖..."
pip3 install -r requirements.txt -q && ok "依赖安装完成" || {
  warn "pip3 安装失败，尝试加 --break-system-packages..."
  pip3 install --break-system-packages -r requirements.txt -q && ok "依赖安装完成"
}

# ── Step 2: wechat-cli ─────────────────────────────────────────────────────

step "2/5" "检查 wechat-cli"

if ! command -v wechat-cli &>/dev/null; then
  echo "  未找到 wechat-cli，正在安装..."
  if ! command -v npm &>/dev/null; then
    fail "未找到 npm。请先安装 Node.js：https://nodejs.org（选 LTS 版本）"
  fi
  npm install -g @canghe_ai/wechat-cli && ok "wechat-cli 安装完成" || fail "wechat-cli 安装失败，请检查 npm 网络连接"
else
  ok "wechat-cli 已安装"
fi

echo "  测试 wechat-cli 连接（需要微信正在运行）..."
TEST=$(wechat-cli sessions --limit 1 2>/dev/null || echo "")
if echo "$TEST" | grep -q '"ok":true'; then
  ok "wechat-cli 已连接微信"
else
  echo ""
  warn "wechat-cli 暂时无法读取微信数据。"
  echo ""
  echo "  请确认："
  echo "  1. 微信 Mac 版正在运行且已登录"
  echo "  2. 运行过 sudo wechat-cli init（只需一次）"
  echo ""
  read -p "  现在运行 sudo wechat-cli init 吗？[y/N] " RUN_INIT
  if [[ "$RUN_INIT" =~ ^[Yy]$ ]]; then
    sudo wechat-cli init && ok "初始化完成" || warn "初始化失败，请手动运行 sudo wechat-cli init 后重试"
  else
    warn "跳过 wechat-cli 初始化。配置完成后手动运行 sudo wechat-cli init"
  fi
fi

# ── Step 3: 配置文件 ───────────────────────────────────────────────────────

step "3/5" "配置 config.json"

if [ -f config.json ]; then
  ok "config.json 已存在，跳过"
else
  cp config.example.json config.json

  # 自动检测微信 ID
  WXFILES=~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files
  AUTO_WXID=""
  if [ -d "$WXFILES" ]; then
    AUTO_WXID=$(ls -lt "$WXFILES" 2>/dev/null \
      | grep -v "^total" | grep -v "all_users" | grep -v "Backup" | grep -v "old_backup" \
      | awk 'NR==1{print $NF}' | tr -d '/')
  fi

  echo ""
  if [ -n "$AUTO_WXID" ]; then
    echo "  自动检测到微信 ID：${BOLD}$AUTO_WXID${NC}"
    read -p "  用这个 ID 吗？[Y/n] " USE_AUTO
    if [[ "$USE_AUTO" =~ ^[Nn]$ ]]; then
      read -p "  请输入你的微信 ID: " WXID
    else
      WXID="$AUTO_WXID"
    fi
  else
    echo "  未能自动检测微信 ID。"
    echo "  提示：运行 ls ~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files/"
    echo "  找到类似 wxid_xxx 或 qq123_xxx 的文件夹名即为你的 ID"
    read -p "  请输入你的微信 ID: " WXID
  fi

  # 写入 wxid
  python3 -c "
import json, pathlib
p = pathlib.Path('config.json')
c = json.loads(p.read_text())
c['self_wxid'] = '$WXID'
p.write_text(json.dumps(c, ensure_ascii=False, indent=2))
print('OK')
" && ok "微信 ID 已写入"

  # Anthropic API Key
  echo ""
  echo "  ${BOLD}Anthropic API Key${NC}（用于 AI 分析，可选）"
  echo "  申请地址：https://console.anthropic.com → API Keys → Create Key"
  echo "  Key 格式：sk-ant-... （不填则跳过，AI 分析功能不可用）"
  echo ""
  read -p "  粘贴 API Key（直接回车跳过）: " API_KEY
  if [ -n "$API_KEY" ]; then
    python3 -c "
import json, pathlib
p = pathlib.Path('config.json')
c = json.loads(p.read_text())
c['anthropic_api_key'] = '$API_KEY'
p.write_text(json.dumps(c, ensure_ascii=False, indent=2))
" && ok "API Key 已写入"
  else
    warn "跳过 API Key，AI 分析不可用（之后在 config.json 里填写）"
  fi
fi

# ── Step 4: 定时任务 ──────────────────────────────────────────────────────

step "4/5" "设置每日自动生成"

echo ""
echo "  配置成功后，日报可以每天早上自动生成并打开浏览器。"
echo ""
read -p "  设置每日自动生成吗？[Y/n] " SET_SCHEDULE
if [[ ! "$SET_SCHEDULE" =~ ^[Nn]$ ]]; then
  read -p "  每天几点生成？（默认 8，输入 0-23）: " HOUR
  HOUR=${HOUR:-8}
  bash schedule.sh "$HOUR" && ok "定时任务已设置（每天 ${HOUR}:00）"
else
  info "跳过定时任务。之后运行 bash schedule.sh 来设置"
fi

# ── Step 5: 测试运行 ──────────────────────────────────────────────────────

step "5/5" "生成测试日报"

echo ""
read -p "  现在生成一份测试日报？[Y/n] " RUN_TEST
if [[ ! "$RUN_TEST" =~ ^[Nn]$ ]]; then
  echo "  生成中..."
  python3 daily_report.py && ok "日报生成成功，已在浏览器打开" || warn "生成失败，请检查上方错误信息"
else
  info "跳过测试。之后运行 python3 daily_report.py 生成日报"
fi

# ── 完成 ──────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}=================================================${NC}"
echo -e "${GREEN}${BOLD}  🎉 配置完成！${NC}"
echo ""
echo "  日常使用："
echo "    python3 daily_report.py          # 生成日报"
echo "    python3 daily_report.py --days 2  # 看最近2天"
echo ""
echo "  如需修改配置，编辑 config.json 文件"
echo -e "${BOLD}=================================================${NC}"
echo ""
