#!/bin/bash
# Start the live stream on the demo VM.
# Type ONLY the stream key from YouTube Studio at the prompt
# (hidden input; never written to disk, a file, or a log).
# If you paste a full RTMP URL instead, that is used as-is.
set -euo pipefail
cd /opt/ballstream

read -rsp 'Stream key (hidden): ' INPUT
echo
if [[ "$INPUT" == rtmp://* ]]; then
  RTMP_URL="$INPUT"
else
  RTMP_URL="rtmp://a.rtmp.youtube.com/live2/$INPUT"
fi
export RTMP_URL
export DURATION_SEC=0   # run until Ctrl-C

exec /opt/ballstream/.venv/bin/python stream_live.py
