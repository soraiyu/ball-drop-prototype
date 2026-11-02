#!/bin/bash
# Start the ball-drop stream on the VM (supervised or from boot).
# Expects /opt/ballstream/rtmp_url.txt to contain the full RTMP URL
# (persistent stream key). Runs detached in tmux session "live";
# logs to /opt/ballstream/live.log.
set -e
cd /opt/ballstream
if [ ! -s rtmp_url.txt ]; then
  echo "rtmp_url.txt missing or empty" >&2
  exit 1
fi
tmux kill-session -t live 2>/dev/null || true
tmux new-session -d -s live \
  "cd /opt/ballstream && CHAT_MODE=youtube SUBS_MODE=on THEME=light RTMP_URL=$(cat rtmp_url.txt) /opt/ballstream/.venv/bin/python stream_live.py >> live.log 2>&1"
echo "stream started in tmux session 'live'"
