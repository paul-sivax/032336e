# XRPL AI Starter Kit integration

The Starter Kit is an ecosystem entry point. The demo uses pinned Python packages rather than depending on a monolithic runtime.

| Capability | Used component | Application role |
| --- | --- | --- |
| Agent interoperability | Official A2A SDK 1.2.3, protocol 1.0 | Discovery, streamed tasks, authenticated independent callers |
| API payment | x402-xrpl 0.3.4 | v2 exact native-XRP fee challenges, signing, verification and settlement |
| Ledger interaction | xrpl-py 5.2.0 | Testnet integration behind the internal merchant |
| Saved discovery | Shipped JSON catalog | Random matching outcomes, one persisted draw per operation |

The runtime has two Python processes: the orchestrator/A2A adapter and one internal merchant. It never calls a standalone pricing service. The original pricing-input contract and private generation tooling are not included.

Native XRP API fees do not need an issued-token trust line. Optional t54 Trustline is off by default; live Trustline/VI and merchant-directory registration are not implemented. The hosted facilitator remains configurable. Its deployment source-tag requirement is checked before signing; it is not treated as a universal x402 constant.

Receipt delivery is implemented on Testnet with owner-authenticated document access. Its encryption/issuance implementation and tests are public; actual term-sheet contents and keys remain protected. The public validation record reports outcomes without wallet, receipt or transaction identifiers. Investment principal remains simulated.

References used during implementation:

- <https://ripple.com/insights/xrpl-ai-starter-kit/>
- <https://xrpl.org/docs/agents/getting-started-with-agentic-transactions>
- <https://xrpl-x402.t54.ai/docs/xrpl-scheme>
- <https://xrpl-x402.t54.ai/docs/merchant-guides/fastapi>
- <https://a2a-protocol.org/latest/specification/>
- <https://a2a-protocol.org/latest/topics/agent-discovery/>

See the [public API](api-reference.md), [runbook](agentic-demo.md) and [validation summary](testnet-catalog-validation.json).
