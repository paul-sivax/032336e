"""Add dedicated faucet-funded MPT wallets and a private metadata key.

Run after setup_testnet.py. Interrupted funding resumes from a private checkpoint.
Never prints seeds or the encryption key, and never replaces an existing key.
"""

import asyncio
import base64
import fcntl
import json
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.wallet import Wallet


async def configure_mpt(root):
    env = root / ".env.testnet.local"
    if not env.exists():
        raise SystemExit("Run scripts/setup_testnet.py first.")
    with open(root / ".demo-mpt-setup.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = dotenv_values(env)
        names = ("XRPL_MPT_ISSUER_SEED", "XRPL_MPT_HOLDER_SEED", "MPT_METADATA_KEY")
        if all(existing.get(name) for name in names):
            print("MPT Testnet configuration already exists; nothing changed.")
            return
        if any(existing.get(name) for name in names):
            raise SystemExit(
                "Partial MPT configuration found. Restore the original keys; refusing replacement."
            )
        if (
            existing.get("PAYMENT_MODE") != "xrpl_testnet"
            or existing.get("XRPL_NETWORK") != "xrpl:1"
        ):
            raise SystemExit("Only the explicit XRPL Testnet profile is supported.")
        path = root / ".env.mpt-testnet.keys.json"
        saved = json.loads(path.read_text()) if path.exists() else {}

        def checkpoint():
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(saved, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)

        client = AsyncJsonRpcClient("https://s.altnet.rippletest.net:51234/")
        for name in names[:2]:
            if name not in saved:
                wallet = await generate_faucet_wallet(
                    client, usage_context="Agentic SIVAX Testnet receipt token"
                )
                saved[name] = wallet.seed
                checkpoint()
            print(
                name.removesuffix("_SEED") + " funded:",
                Wallet.from_seed(saved[name]).address,
                flush=True,
            )
        if "MPT_METADATA_KEY" not in saved:
            saved["MPT_METADATA_KEY"] = base64.b64encode(
                secrets.token_bytes(32)
            ).decode()
            saved["MPT_METADATA_KEY_ID"] = "k1"
            checkpoint()
        fd = os.open(env, os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, "a") as f:
            f.write("\n" + "\n".join(f"{k}={v}" for k, v in saved.items()) + "\n")
        env.chmod(0o600)
        print("Private MPT keys saved. Back up the metadata key before issuing tokens.")


if __name__ == "__main__":
    asyncio.run(configure_mpt(Path(__file__).resolve().parents[1]))
