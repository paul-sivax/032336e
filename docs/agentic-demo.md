# SIVAX Agent demo runbook

The app has an English mobile interface, an orchestrator/A2A service and one internal saved-quote/order merchant. It has no standalone pricing service. Product results and fills are simulated. API fees and receipt delivery can run on XRPL Testnet; investment principal never moves.

## Local start

Requires Python 3.12, Node 22+ and Linux/macOS or WSL.

```bash
bash scripts/setup-demo.sh
.venv/bin/python scripts/run_demo.py
```

Open <http://localhost:5173> with access code `local-demo-only`. Mock mode needs no wallets or external service. The orchestrator runs on port 8010 and the internal merchant on 8012. For Docker, copy `.env.demo.example` to `.env.demo` and run `docker compose -f compose.demo.yml up --build`. Compose publishes only the web/orchestrator on loopback. Its shared demo login is not production identity.

## User flow

Choose an exact investment term, amount with RLUSD/XRP currency, and Principal protection or High Yield. Principal protection selects simulated CMA products; High Yield selects FCNs. Terms start at 1 business day. NYSE holidays, weekends and market closes determine eligible dates. Saturday October 24, 2026 with 1 BD starts October 26 and matures October 27.

SIVAX is the sole seller. Its catalog has 24 equities and 36 CMA variants, excluding BTC, ETH and V. Matching results are randomly drawn from saved JSON: 30 FCNs from the default collection, or 12 CMAs. The complete draw is saved before billing. Standard and Turbo return the same quantity; Turbo has twice the per-quote fee and halves simulated delivery pacing. It does not speed up ledger finality.

The report shows three cards. View all opens pages of three and unlocks another pool. A fresh pool or saved-plan refresh requires a new fee grant. Selecting a card and pressing Confirm approves that exact order. The receipt document becomes readable only after completed delivery and authenticated retrieval.

Back to start offers holdings, a new plan or saved preferences. Reset returns this session's receipts and removes its holder authorizations before clearing the workspace. Audit records and ledger history remain. The animated character is illustrative; progress follows backend phases, not an invented ledger completion percentage.

## Testnet setup

Use dedicated faucet-funded accounts only:

```bash
.venv/bin/python scripts/setup_testnet.py
DEMO_ENV_FILE=.env.testnet.local .venv/bin/python scripts/run_demo.py
```

For an existing profile, `scripts/setup_mpt_testnet.py` adds missing receipt configuration. Keep generated environment files and keys private, backed up and out of Git. Use the generated access code and a fresh browser session when changing payment mode. Never repurpose populated mock data volumes as Testnet state. Mainnet is rejected.

Receipt delivery uses sponsored demo custody. The authenticated owner can view the readable document; the encryption/issuance implementation and its tests are public, while document contents and keys remain protected. See the [receipt lifecycle guide](mpt-receipts.md).

## A2A client

Discovery is `/.well-known/agent-card.json`; JSON-RPC is `/a2a`, using A2A protocol 1.0 and a scoped bearer credential. The official SDK is pinned in the lockfile. Browser and A2A adapters share the same orchestration core.

Use Menu → Connect an agent, then pass the resulting short-lived token privately through `SIVAX_AGENT_TOKEN`. Run the independent example:

```bash
SIVAX_URL=http://localhost:8010 .venv/bin/python examples/personal-assistant-agent/agent.py --scripted --order-amount 100 --currency XRP
```

For LLM mode omit `--scripted`, configure `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`, and supply `--goal` with explicit preferences. The agent may search and summarize; only the human can approve fees or an order. Reuse its checkpoint and credential when recovering an interrupted operation. Scripted tests do not claim an LLM invocation.

A2A skills are `find_fcn_quotes`, `prepare_demo_order` and `execute_demo_order`. Request schema is `sivax.agent-request.v4`, available at `/api/schema`. Stable operation IDs deduplicate requests per authenticated session/caller. Changed input requires a new operation ID. Supported task operations include send, streaming send, get, list, cancel and subscribe. Push notifications are not advertised.

## Payment and recovery boundaries

The shipped XRPL x402 SDK handles v2 exact native-XRP payment requirements, signing and settlement. Native XRP needs no issued-token trust line. Optional t54 Trustline is off by default; live Trustline/VI and directory registration are not implemented. The configured hosted facilitator remains an external dependency.

Standard discovery costs 1,000 drops per returned quote; Turbo costs 2,000. One settlement covers a whole collection. An approved order costs 5,000 drops separately. Fees stay XRP for RLUSD-denominated simulated investments. Standard rounds cap API spending at 100,000 drops; explicit Turbo authorization raises that round to 200,000. Bounded network fees are reserved too. Receipt fees and reserves are sponsored separately.

The payer validates the network, payee, body binding, quantity and total before signing, saves the proof before sending, and reuses that proof after an unknown response. The merchant persists settlement and the selected results. Retrying an operation cannot redraw results or create a replacement payment. Unknown outcomes retain their allowance and block the wallet lane until reconciled.

Cancellation can stop unsubmitted discovery; an in-flight batch may finish and be charged. Paid delivery failures remain recoverable with the original receipt. Automatic refunds and automated replacement of expired attempts are not implemented. Do not clear state or change IDs to bypass reconciliation.

SQLite and process locks support one orchestrator and one merchant per data directory, with dedicated signing accounts per deployment. Do not scale this profile across hosts.

## Verification

```bash
npm run build --prefix demo/web
.venv/bin/python -m pytest -q
cd demo/web
npx playwright install chromium
npm run test:e2e
```

The included receipt tests cover encryption and recovery. The opt-in live harness is `tests/agentic/test_testnet.py`; run it separately with dedicated generated Testnet funds only. See [validation](validation.md) and the [public API](api-reference.md).
