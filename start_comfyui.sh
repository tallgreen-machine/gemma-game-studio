#!/bin/bash
source /Users/max/comfy310/bin/activate
cd /Users/max/ComfyUI
exec python main.py --listen 127.0.0.1 --port 8188
