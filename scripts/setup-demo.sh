#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install -r requirements-demo.lock
npm ci --prefix demo/web
if [ ! -f .env.demo ]; then cp .env.demo.example .env.demo; chmod 600 .env.demo; fi
printf '%s\n' 'Run .venv/bin/python scripts/run_demo.py, then open http://localhost:5173'
