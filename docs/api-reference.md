# SIVAX Agent public API — v4

This document describes the browser and A2A interface for the public demo. Quotes come from a shipped, simulated result catalog. There is no standalone pricing engine, upstream pricing credential or public pricing-input contract. The internal merchant combines saved-quote discovery, reservations and orders; its OpenAPI, Swagger and ReDoc endpoints are disabled.

## Public entry points

| Interface | Local URL | Contract |
| --- | --- | --- |
| Browser app | `http://localhost:5173` | English mobile UI |
| Orchestrator | `http://localhost:8010` | `/api/schema`, `/openapi.json`, `/docs` |
| Agent discovery | `http://localhost:8010/.well-known/agent-card.json` | Official A2A 1.0 Agent Card |
| Agent messages | `http://localhost:8010/a2a` | A2A JSON-RPC, scoped bearer credential |

The internal merchant is not a frontend route and is not published by Compose. External agents use the same guarded orchestration core as the browser; they cannot authorize spending or approve orders.

## Search request

Send this structured data part through the official A2A SDK, or as the JSON body of the authenticated browser operation route:

```json
{
  "schema_version": "sivax.agent-request.v4",
  "operation": "find_fcn_quotes",
  "operation_id": "unique-stable-operation-id",
  "service_tier": "standard",
  "preferences": {
    "investment_term": "1bd",
    "amount": "100.125",
    "currency": "RLUSD",
    "principal_protected": false
  }
}
```

The questionnaire has three choices: exact investment term; amount with currency; principal protection or High Yield. Terms are `1bd`, `2bd`, `3bd`, `5bd`, `10bd`, `1m`, `3m`, `6m`, `1y`; legacy `7d` is also accepted. Amount is a decimal string from 1 through 100,000 with at most six decimal places. Currency is `RLUSD` or `XRP`. Protection is a strict boolean: true selects CMA, false selects FCN.

An optional `asset_pool` selects 2–6 distinct catalog equities; `pool_round` rotates CMA collections. These do not add questionnaire steps. There is one seller, SIVAX. BTC, ETH and V are excluded. Default equities are TSLA, NVDA, AMZN, XRPN, MA and PLTR; the full allowlist is returned in `/api/config`.

## Saved discovery and results

The server ships 1,140 normalized result records: 1,104 FCNs and 36 CMAs, checked against 22,800 offline term/currency combinations. It filters by the exact protection choice and requested asset collection, randomly samples without replacement, then persists that entire selection before requesting payment. New searches draw anew; retries, timeouts and process restarts return the original draw and receipt. The JSON contains outputs, not the original pricing model or request specification.

Current NYSE business dates and the exact amount/currency are attached to each saved outcome. Annual yields and redemption scenarios remain the stored values. Coupon accrual scales the saved unit yield by actual days / 365 and notional, rounding down to six decimals. No runtime market pricing or FX conversion occurs. On Saturday 2026-10-24, 1 BD starts on October 26 and matures on October 27. Before or during the regular session today may start; at/after close the next session applies.

A completed search returns `sivax.quote-result.v4`, `quote_source: precomputed_catalog`, every ranked quote, the recommended quote ID, an initial hold and one aggregate payment receipt. Each quote carries its fixture ID, canonical terms hash, simulated flag, dates, exact amount/currency, stored annual rate, projected coupon and stored scenarios. Exact constraints are filtered before ranking by annual yield. Quotes last 600 seconds; holds last 180 seconds.

The UI requests 30 FCNs from a matching 60-item default collection, or all 12 CMAs in the current collection. It initially displays three cards. **View all** opens pages of three and unlocks **Try another pool**. A new pool retains the three preferences and needs fresh fee approval. Four disjoint default equity groups and three CMA groups cycle independently. A full CMA draw contains the same twelve members until the pool changes.

## x402 fees and recovery

| Tier | Per returned quote | Full FCN / CMA collection | Simulated delivery pacing |
| --- | --- | --- | --- |
| Standard | 1,000 drops | 30,000 / 12,000 drops | 800 ms |
| Turbo | 2,000 drops | 60,000 / 24,000 drops | 400 ms |

Both tiers return the same number of products. Turbo's 2× speed is demo delivery and animation pacing; ledger settlement time is unchanged. One x402 v2 `exact` native-XRP payment covers a whole collection. A separately approved order costs 5,000 drops. API fees remain XRP even for an RLUSD-denominated investment. Investment principal never moves. MPT fees/reserves are sponsored separately. Native XRP does not require an issued-token trust line; optional t54 Trustline is off by default.

The internal payer validates the allowlisted payee, `xrpl:1`, asset, body hash, quantity, unit price, aggregate amount and source tag before signing. It saves the signed SDK proof before sending it, and delivery follows validated settlement. The same logical operation never generates a replacement payment after an unknown response. Mock mode is explicitly labeled and uses demo credits, not ledger receipts.

Standard rounds have a 100,000-drop API allowance; explicit Turbo authorization raises only that round to 200,000. Network-fee ceilings are reserved separately within the allowance. No available matching collection or an invalid profile fails before invoicing. Paid delivery failures retain the receipt and recovery state. Automatic refunds are not implemented. See the [recovery runbook](agentic-demo.md#payment-and-recovery-boundaries).

## Browser orchestration and human approval

Browser writes use an HttpOnly SameSite Strict session cookie, `X-CSRF-Token` from the session, and the configured origin. `POST /api/session` takes `{"access_key":"<demo access code>"}`. Agent tokens cannot call these approval routes.

| Route | Purpose |
| --- | --- |
| `GET /api/config`, `GET /api/schema` | Public demo configuration and v4 domain request schema |
| `GET /api/state` | Own session, `workspace`, saved `profiles`, active `holdings`, reset progress, jobs/intents, `order_progress`, current-round allowance and `round_cap_drops` |
| `POST /api/grants` | `{max_rfqs, authorization_id, caller?, job_id?, service_tier?}`; repeated authorization ID returns the same grant, changed policy conflicts |
| `POST /api/operations` | Start/retrieve a logical domain operation with a stable `operation_id` |
| `POST /api/operations/{id}/cancel` | Stop an unsubmitted search; an in-flight batch may finish and be charged |
| `POST /api/operations/{id}/resume` | Reconcile an unknown original payment; no replacement signature |
| `POST /api/searches/{id}/selection` | `{operation_id, quote_id, amount, currency}` prepares a free immutable intent using the original search caller; browser owner + CSRF required |
| `POST /api/intents/{id}/confirm` | `{intent_hash}` approves and starts/resumes the one execution operation; repeated Confirm returns that same operation; browser owner + CSRF required |
| `POST /api/intents/{id}/approve` | `{intent_hash}` approves that exact held quote, amount, currency, API fee and sponsored MPT receipt |
| `POST /api/agent-credentials` | Create an expiring, caller-scoped A2A bearer token |
| `GET /api/events` | SSE with durable event IDs and `Last-Event-ID` replay |
| `POST /api/operations/{id}/review-all` | Records full-review action; required before another-pool action |
| `POST /api/workspace/start` | `{action_id, mode, profile_id?, search_id?}`; mode is `new`, `saved` or `another_pool`; idempotent new round |
| `POST /api/workspace/home` | Opens holdings / new plan / refresh saved hub |
| `GET /api/orders/{order_id}/termsheet` | Owner-authenticated ledger metadata decryption; never returns keys |
| `POST /api/reset` | `{reset_id}` begins/resumes MPT return + unauthorize, archives profiles, starts empty round |

Search operation: `find_fcn_quotes` + three `preferences` + optional `spend_authorization_id` and `service_tier`. Preparation: `prepare_demo_order` + `search_id` + optional `quote_id` (any returned live quote from that owner/caller search) + the same `amount` and `currency`. Omit `quote_id` for the recommendation. A fresh hold binds the selection. Changed market sessions require current dates before new approval/execution. Execution: `execute_demo_order` + `intent_id`; waits for exact human approval. One intent cannot be used for two execution operations.

A2A exposes these same operations through structured data parts, never a separate bypass. See the [A2A contract](agentic-demo.md#a2a-contract) and [independent client](../examples/personal-assistant-agent/agent.py).

## Errors and durable states

| Code | Typical condition | Action |
| --- | --- | --- |
| 400 | Missing/invalid idempotency or session header; malformed payment proof | Correct input; do not change an already-signed payment |
| 401 / 403 | Missing credentials, wrong owner/caller or human approval | Use the correct authenticated session/caller |
| 402 | Payment challenge or rejected payment proof | Validate the challenge; use the original SDK proof |
| 404 | Quote/order/task not found for this owner | Check the identifier and scope |
| 409 | Changed idempotent input, conflicting approval/amount, insufficient budget or consumed hold | Inspect original state before another operation |
| 410 | Expired invoice/quote/hold/lease | New search needs new authorization; preserve any paid recovery record |
| 422 | Invalid profile or unavailable saved collection | Fix the body; no invoice/payment was created for invalid schemas |
| 503 | Unknown settlement or paid result not delivered | Reconcile original operation, retaining its proof and budget |

Invoice states: `UNPAID`, `IN_FLIGHT`, `UNKNOWN`, `SETTLED`. Holds: `HELD`, `RELEASED`, `LEASED`, `CONSUMED` (timestamps remain authoritative). Domain jobs: `input_required`, `queued`, `running`, `payment_unknown`, `completed`, `cancelled`, `failed`. Browser events include `batch.planned`, `batch.submitted`, `batch.received`, `payment.pending`, `payment.settled`, `search.completed` and order events.


## Repeatable rounds and receipt access

The landing page starts a fresh questionnaire or opens the returning session's holdings/new/saved hub. A saved refresh updates dates, draws a new collection and requires fee approval. Old grants and approvals cannot spend in a new round. Active operations and unresolved payments block reset or a new round.

Reset is authenticated and session-scoped. It completes receipt return and holder cleanup before clearing preferences. Reuse the returned reset ID after interruption. Audit records and ledger history remain.

The owner-authenticated term-sheet route returns a readable document after ledger verification. Encryption and issuance code are public. Actual term-sheet contents remain encrypted on-chain and require authenticated owner access in the application. Keys are never returned to clients. Mock and Testnet sources are clearly distinguished. Progress is an owner-scoped phase signal, not proof of completion by itself.
