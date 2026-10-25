"""Create dedicated XRPL Testnet faucet accounts; never print private keys."""

import asyncio
import json
import os
import secrets
from pathlib import Path

from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.wallet import generate_faucet_wallet


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(text)


async def main():
    root = Path(__file__).resolve().parents[1]
    env = root / ".env.testnet.local"
    keys = root / ".env.testnet.keys.json"
    if env.exists() or keys.exists():
        raise SystemExit(
            "Testnet configuration already exists. Reuse it; keys were not overwritten."
        )
    client = AsyncJsonRpcClient("https://s.altnet.rippletest.net:51234/")
    payer = await generate_faucet_wallet(
        client, usage_context="Agentic SIVAX x402 testnet demo"
    )
    print("Dedicated testnet payer funded:", payer.address, flush=True)
    merchant = await generate_faucet_wallet(
        client, usage_context="Agentic SIVAX x402 testnet demo merchant"
    )
    print("Dedicated testnet merchant funded:", merchant.address, flush=True)
    private_write(
        keys,
        json.dumps(
            {
                "network": "xrpl:1",
                "payer_seed": payer.seed,
                "merchant_seed": merchant.seed,
            },
            indent=2,
        ),
    )
    private_write(
        env,
        "\n".join(
            [
                "PAYMENT_MODE=xrpl_testnet",
                "RISK_MODE=off",
                "XRPL_NETWORK=xrpl:1",
                "XRPL_RPC_URL=https://s.altnet.rippletest.net:51234/",
                "XRPL_FACILITATOR_URL=https://xrpl-facilitator-testnet.t54.ai",
                "XRPL_PAYER_SEED=" + payer.seed,
                "XRPL_PAY_TO=" + merchant.address,
                "DEMO_ACCESS_KEY=" + secrets.token_urlsafe(24),
                "SERVICE_KEY=" + secrets.token_urlsafe(32),
                "DATA_DIR=.demo-testnet-data",
                "PUBLIC_API_URL=http://localhost:8010",
                "PUBLIC_WEB_URL=http://localhost:5173",
                "ORDERS_URL=http://127.0.0.1:8012",
                "",
            ]
        ),
    )
    print(
        "Saved private configuration to .env.testnet.local and .env.testnet.keys.json. Neither belongs in Git.",
        flush=True,
    )
    from setup_mpt_testnet import configure_mpt

    await configure_mpt(root)


if __name__ == "__main__":
    asyncio.run(main())
