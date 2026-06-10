#!/bin/bash
# 设置 / 取消微信日报每日定时任务（macOS launchd）
# 用法：
#   bash schedule.sh         # 设置，默认每天 8:00
#   bash schedule.sh 7       # 设置，每天 7:00
#   bash schedule.sh --uninstall   # 取消

HOUR=${1:-8}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.wechat-daily-report"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "--uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ 已取消定时任务"
  exit 0
fi

# 自动检测 python3 路径（兼容 nvm / homebrew / 系统）
PYTHON=$(which python3 2>/dev/null || echo "/usr/bin/python3")

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$SCRIPT_DIR/daily_report.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$SCRIPT_DIR</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$HOUR</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$SCRIPT_DIR/run.log</string>
  <key>StandardErrorPath</key>
  <string>$SCRIPT_DIR/run.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$(dirname $PYTHON)</string>
  </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ 定时任务已设置：每天 ${HOUR}:00 自动生成日报"
echo "   日报生成后自动在浏览器打开"
echo "   运行日志：$SCRIPT_DIR/run.log"
echo ""
echo "   取消定时任务：bash schedule.sh --uninstall"
