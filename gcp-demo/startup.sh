#!/bin/bash
# GCP demo VM setup (Ubuntu 22.04): ffmpeg + Python venv with pymunk.
# Run once on the VM as a normal user with sudo.
set -euxo pipefail

sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv python3-pip

sudo mkdir -p /opt/ballstream
sudo chown "$USER" /opt/ballstream
python3 -m venv /opt/ballstream/.venv
/opt/ballstream/.venv/bin/pip install --upgrade pip
/opt/ballstream/.venv/bin/pip install pymunk pillow numpy

echo "setup done: copy stream_live.py to /opt/ballstream/ and run it with /opt/ballstream/.venv/bin/python"
