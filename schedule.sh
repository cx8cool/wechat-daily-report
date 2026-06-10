#!/bin/bash
# Set up automatic daily report at 8:00 AM via macOS launchd
# Usage: bash schedule.sh [hour]   (default: 8)

HOUR=${1:-8}
SCRIPT_DIR="$(cd "$(dirname "${BASH__SOURCE[0]}")" && pwd)"
LABEL="com.wechat-daily-report"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

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
    <string>$(which python3)</string>
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
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ Scheduled: daily report will run every day at ${HOUR}:00 AM"
echo "   Report opens automatically in your browser."
echo ""
echo "   To cancel: bash schedule.sh --uninstall"

if [ "$1" = "--uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null
  rm -f "$PLIST"
  echo "✅ Uninstalled scheduled task."
fi
