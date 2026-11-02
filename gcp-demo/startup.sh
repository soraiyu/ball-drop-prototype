#!/bin/bash
# GCP demo VM setup (Ubuntu 22.04): ffmpeg + Python venv with pymunk.
# Run once on the VM as a normal user with sudo.
set -euxo pipefail

sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv python3-pip fonts-noto-cjk

sudo mkdir -p /opt/ballstream
sudo chown "$USER" /opt/ballstream
python3 -m venv /opt/ballstream/.venv
/opt/ballstream/.venv/bin/pip install --upgrade pip
/opt/ballstream/.venv/bin/pip install pymunk pillow numpy \
  google-api-python-client google-auth-oauthlib grpcio

echo "setup done: copy stream_live.py, chat_triggers.py, subscriber_watch.py, stream_list_pb2.py, stream_list_pb2_grpc.py (and yt_token.json for chat/subs modes) to /opt/ballstream/ and run with /opt/ballstream/.venv/bin/python"
