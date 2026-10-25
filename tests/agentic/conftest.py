import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    directory = tmp_path_factory.mktemp("agentic-network-test")
    ports = {name: port() for name in ("api", "orders")}
    urls = {k: f"http://127.0.0.1:{v}" for k, v in ports.items()}
    env = {
        **os.environ,
        "DATA_DIR": str(directory),
        "PAYMENT_MODE": "mock",
        "RISK_MODE": "off",
        "DEMO_ACCESS_KEY": "test-access",
        "SERVICE_KEY": "test-service",
        "MOCK_PAYMENT_SECRET": "test-proof",
        "PUBLIC_API_URL": urls["api"],
        "PUBLIC_WEB_URL": "http://localhost:5173",
        "ORDERS_URL": urls["orders"],
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "XRPL_PAYER_SEED": "",
        "XRPL_PAY_TO": "",
    }
    if os.getenv("RUN_XRPL_TESTNET") == "1":
        from dotenv import dotenv_values

        private = dotenv_values(ROOT / ".env.testnet.local")
        for key in (
            "PAYMENT_MODE",
            "XRPL_NETWORK",
            "XRPL_RPC_URL",
            "XRPL_FACILITATOR_URL",
            "XRPL_PAYER_SEED",
            "XRPL_PAY_TO",
            "XRPL_MPT_ISSUER_SEED",
            "XRPL_MPT_HOLDER_SEED",
            "MPT_METADATA_KEY",
            "MPT_METADATA_KEY_ID",
        ):
            env[key] = private[key]
        assert env["PAYMENT_MODE"] == "xrpl_testnet" and env["XRPL_NETWORK"] == "xrpl:1"
    processes = {}
    handles = []

    def start(name):
        log = open(directory / f"{name}.log", "a")
        handles.append(log)
        target = (
            "agentic.api:create_app"
            if name == "api"
            else "agentic.merchant:app_factory"
        )
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                target,
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(ports[name]),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
        )
        processes[name] = proc
        for _ in range(120):
            if proc.poll() is not None:
                raise RuntimeError((directory / f"{name}.log").read_text())
            try:
                if (
                    httpx.get(
                        urls[name] + "/health", timeout=0.4, trust_env=False
                    ).status_code
                    == 200
                ):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError(f"{name} did not start")

    def restart(name):
        processes[name].terminate()
        processes[name].wait(5)
        start(name)

    try:
        for name in ("orders", "api"):
            start(name)
        yield {**urls, "directory": directory, "env": env, "restart": restart}
    finally:
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
        for proc in processes.values():
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for h in handles:
            h.close()


@pytest.fixture
async def browser(stack):
    async with httpx.AsyncClient(
        base_url=stack["api"], trust_env=False, timeout=30
    ) as c:
        r = await c.post("/api/session", json={"access_key": "test-access"})
        r.raise_for_status()
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        yield c
