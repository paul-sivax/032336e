"""Launch the orchestrator, internal merchant and the Vue screen. Ctrl+C stops the group."""

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

root = Path(__file__).resolve().parents[1]
os.chdir(root)

load_dotenv(root / os.getenv("DEMO_ENV_FILE", ".env.demo"))
python = str(root / ".venv" / "bin" / "python")
commands = [
    (
        [
            python,
            "-m",
            "uvicorn",
            "agentic.api:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8010",
        ],
        {},
    ),
    (
        [
            python,
            "-m",
            "uvicorn",
            "agentic.merchant:app_factory",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            "8012",
        ],
        {},
    ),
    (["npm", "--prefix", "demo/web", "run", "dev"], {}),
]
children = []
# Orchestrator and web first; the merchant last so in-flight paid requests can finish.
stop_order = (0, 2, 1)


def signal_group(p, sig):
    # A finished group raises ProcessLookupError; on macOS one holding only zombies raises EPERM.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(p.pid, sig)


def force(*args):
    for p in children:
        signal_group(p, signal.SIGKILL)


def stop(*args):
    # A second Ctrl+C skips the graceful wait for in-flight requests.
    signal.signal(signal.SIGINT, force)
    signal.signal(signal.SIGTERM, force)
    print(
        "Stopping demo services (Ctrl+C again to force)...", file=sys.stderr, flush=True
    )
    deadline = time.monotonic() + 60
    for p in [children[i] for i in stop_order if i < len(children)]:
        signal_group(p, signal.SIGTERM)
        try:
            p.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            signal_group(p, signal.SIGKILL)
            p.wait()
    raise SystemExit(0)


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
try:
    for cmd, extra in commands:
        children.append(
            subprocess.Popen(cmd, env={**os.environ, **extra}, start_new_session=True)
        )
    print(
        "Agentic SIVAX: http://localhost:5173 | A2A: http://localhost:8010/.well-known/agent-card.json",
        flush=True,
    )
    while True:
        if any(p.poll() is not None for p in children):
            print(
                "A demo service exited; stopping the remaining services.",
                file=sys.stderr,
            )
            stop()
        time.sleep(0.5)
finally:
    for p in children:
        if p.poll() is None:
            signal_group(p, signal.SIGTERM)
