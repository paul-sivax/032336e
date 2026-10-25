# SIVAX Agent

Your Investment Agent on XRPL. An English mobile demo discovers one product from the SIVAX collection using three choices: exact investment term, amount with XRP/RLUSD currency, and principal protection. Protection selects simulated CMA products; High Yield selects FCNs.

**Demo only:** quotes are precomputed and investment principal and fills are simulated. In Testnet mode, native XRP API fees and encrypted MPT receipts use real ledger transactions. The default mock profile requires no wallets, external accounts or LLM credentials.

## Run locally

Requires Python 3.12, Node 22+ and Linux/macOS or WSL.

```bash
bash scripts/setup-demo.sh
.venv/bin/python scripts/run_demo.py
```

Open <http://localhost:5173>. The mock profile uses access code `local-demo-only`.

### Demo access code

The server accepts the `DEMO_ACCESS_KEY` value from the environment file that `run_demo.py` loads: `.env.demo` by default, or the file named by `DEMO_ENV_FILE`. The landing page's **Start** button always tries `local-demo-only`. If you use any other code, enter it through the menu. This includes Testnet mode, where `scripts/setup_testnet.py` writes a random code to `.env.testnet.local`.

1. Read the code from the environment file, for example `grep DEMO_ACCESS_KEY .env.testnet.local`.
2. Before signing in, press the menu button (☰) at the top right to open **Demo access**.
3. Enter the code in **Access code** and press **Start**.

"Incorrect demo access code." means the entered code does not match the running server's `DEMO_ACCESS_KEY`. A `401` from `/api/state` before sign-in is expected: sessions are stored per data directory, so a cookie from an earlier run or another payment mode is rejected. Use a fresh browser session when you switch modes. See the [runbook](docs/agentic-demo.md#testnet-setup) for Testnet setup.

Choose a term from 1 BD, amount and currency, then protection or High Yield. Standard and Turbo search the same maximum collection; Turbo doubles the RFQ fee and halves simulated delivery pacing. Inspect the top three cards, view the full collection or try another pool, then Confirm an exact simulated order.

The character writes, stamps and holds up the completed document after delivery and authenticated retrieval. Its 21 animation frames share a fixed viewport. The term sheet then expands into a readable document. Holdings, new plans, saved preferences and reset make the flow repeatable.

## Agent and ledger integrations

- **A2A:** Agent Card discovery, scoped credentials, structured tasks, streaming and recovery let an independent agent delegate searches and order requests under human approvals.
- **x402:** Native XRP API payments settle once per RFQ batch, with a separate order fee and capped spending. Mock credits are used locally.
- **MPT:** One encrypted receipt per simulated order, with sponsored custody, holder authorization, delivery, authenticated term-sheet viewing, return and unauthorization.
- **t54 Trustline:** Optional and off by default. Live Trustline/VI and merchant-directory registration are not implemented. Native XRP does not require an issued-token trust line.

The catalog contains 1,140 saved outputs across 24 equities and 36 CMA variants. Paid searches persist a random compatible selection before billing; retries reuse it. The original pricing model, input contract and generation tooling are excluded.

See the [runbook](docs/agentic-demo.md), [public API](docs/api-reference.md), [Starter Kit integration](docs/starter-kit-integration.md), [MPT guide](docs/mpt-receipts.md) and [validation record](docs/validation.md). The independent A2A example is in `examples/personal-assistant-agent/`.

## Verify

```bash
npm run build --prefix demo/web
RUN_XRPL_TESTNET=0 .venv/bin/python -m pytest -q
cd demo/web
npx playwright install chromium
npm run test:e2e
```

The ordinary suite uses mock credits. Opt-in Testnet tests require dedicated faucet-funded accounts. The recorded [Testnet run](docs/testnet-catalog-validation.json) covers aggregate XRP fees, encrypted MPT delivery/decryption, return and unauthorization. It does not claim real principal investment or securities execution.
