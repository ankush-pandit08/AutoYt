#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
if [ ! -f .env ]; then cp .env.example .env; fi
.venv/bin/python -m autoyt doctor
echo 'See SETUP.md for FFmpeg, Ollama, local TTS and OAuth. No scheduler registered.'
