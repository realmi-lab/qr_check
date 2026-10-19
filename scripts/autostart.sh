#!/bin/zsh
# 회의실봇 자동 실행 등록/해제 (macOS launchd, 로그인한 사용자 기준)
#
#   scripts/autostart.sh install     DB·서버·터널을 로그인 시 자동 실행, 멈추면 다시 실행
#   scripts/autostart.sh uninstall   자동 실행 해제 (DB는 그대로 둠)
#   scripts/autostart.sh status      상태 보기
#   scripts/autostart.sh restart     서버만 다시 시작
#
# 서버는 caffeinate로 감싸서, 서버가 도는 동안 Mac이 잠자기에 들어가지 않게 함 (전원 연결 시).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
LOGS="$ROOT/logs"
PG_BIN="/Applications/Postgres.app/Contents/Versions/16/bin"
PG_DATA="$HOME/Library/Application Support/Postgres/var-16"
DOMAIN="gui/$(id -u)"
LABELS=(com.roombot.postgres com.roombot.server com.roombot.tunnel)

plist() {  # plist <label> <log-name> <args...>
  local label=$1 name=$2; shift 2
  local args=""
  for a in "$@"; do args+="    <string>${a//&/&amp;}</string>"$'\n'; done
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
$args  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$LOGS/$name.log</string>
  <key>StandardErrorPath</key><string>$LOGS/$name.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
</dict>
</plist>
EOF
}

install() {
  mkdir -p "$AGENTS" "$LOGS"
  # 수동으로 켜 둔 PostgreSQL이 있으면 끄고 launchd가 관리하게 함
  if "$PG_BIN/pg_ctl" -D "$PG_DATA" status >/dev/null 2>&1; then
    "$PG_BIN/pg_ctl" -D "$PG_DATA" -m fast stop >/dev/null
  fi
  plist com.roombot.postgres postgres "$PG_BIN/postgres" -D "$PG_DATA" > "$AGENTS/com.roombot.postgres.plist"
  plist com.roombot.server server /usr/bin/caffeinate -i -s "$ROOT/.venv/bin/python" -m roombot.main > "$AGENTS/com.roombot.server.plist"
  plist com.roombot.tunnel tunnel "$ROOT/.venv/bin/python" "$ROOT/scripts/tunnel.py" > "$AGENTS/com.roombot.tunnel.plist"
  for l in "${LABELS[@]}"; do
    plutil -lint "$AGENTS/$l.plist" >/dev/null
    launchctl bootout "$DOMAIN/$l" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$AGENTS/$l.plist"
  done
  echo "자동 실행 등록 완료: ${LABELS[*]}"
}

uninstall() {
  for l in "${LABELS[@]}"; do
    launchctl bootout "$DOMAIN/$l" 2>/dev/null || true
    rm -f "$AGENTS/$l.plist"
  done
  echo "자동 실행 해제 완료 (PostgreSQL도 멈췄어요. 필요하면 Postgres.app으로 켜세요)"
}

status() {
  for l in "${LABELS[@]}"; do
    if out=$(launchctl print "$DOMAIN/$l" 2>/dev/null); then
      state=$(print -r -- "$out" | awk -F'= ' '/^\tstate = /{print $2; exit}')
      pid=$(print -r -- "$out" | awk -F'= ' '/^\tpid = /{print $2; exit}')
      runs=$(print -r -- "$out" | awk -F'= ' '/^\truns = /{print $2; exit}')
      echo "$l: ${state:-?} (pid ${pid:--}, 실행 횟수 ${runs:-?})"
    else
      echo "$l: 등록 안 됨"
    fi
  done
  [[ -f "$LOGS/public-url.txt" ]] && echo "공개 주소: $(cat "$LOGS/public-url.txt")"
  pmset -g assertions | grep -q "caffeinate" && echo "잠자기 방지: 켜짐 (caffeinate)" || echo "잠자기 방지: 꺼짐"
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  restart) launchctl kickstart -k "$DOMAIN/com.roombot.server" && echo "서버 다시 시작함" ;;
  *) echo "사용법: $0 install | uninstall | status | restart"; exit 1 ;;
esac
