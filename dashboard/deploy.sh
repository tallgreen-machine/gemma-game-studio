#!/bin/bash
echo "Installing Epiphany Dashboard Dependencies..."
# Ensure Python 3 and pip are installed (Ubuntu/Debian)
if ! command -v pip3 &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3-pip python3-venv
fi

# Create virtual environment and install aiohttp
python3 -m venv venv
source venv/bin/activate
pip install aiohttp

echo "Dependencies installed."
echo "To start the dashboard, run:"
echo "source venv/bin/activate && python server.py"
