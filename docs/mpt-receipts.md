# SIVAX demo receipt lifecycle

After the human confirms an exact saved quote, the order service simulates the fill and delivers one receipt token on XRPL Testnet. The investment principal is not transferred. The default local profile uses a labeled mock receipt.

## Receipt access

The receipt is delivered to a dedicated sponsored demo custody wallet. The public token name is opaque. The application stores its friendly product name in the owner's private records. The authenticated owner can open the term sheet in the UI; the service verifies the ledger record and its relationship to the approved order before returning the readable document.

The MPT encryption and issuance implementation and tests are public. Actual term-sheet contents are encrypted on-chain and readable through the authenticated owner interface. Signing seeds and decryption keys are never returned to the browser or written into public token data. Public ledger evidence remains visible even when document contents are protected.

## Setup and recovery

Use `scripts/setup_testnet.py` to create dedicated faucet-funded demo accounts. Existing test profiles can add receipt support with `scripts/setup_mpt_testnet.py`. Keep the generated environment file private and retain a secure backup of its keys. Losing the document key can make old receipts unreadable. Do not replace keys or delete runtime records to resolve an interrupted operation.

The service persists signed attempts before submission and resumes those attempts after interruption. Unknown outcomes retain their wallet-lane lock until reconciled. A completed order requires verified issuance and delivery; the UI does not treat an animation as proof of completion.

## Reset

Reset operates only on the authenticated session's receipts. It returns each token to its issuer and removes the holder authorization after the return validates. The UI clears holdings and saved preferences only after cleanup succeeds. Reset does not erase ledger history, issuance objects or retained application audit records. Resume the same reset ID if cleanup is interrupted.

## Fees and limits

Discovery has one aggregate x402 fee per collection, based on its returned quote count and selected tier. The approved order has a separate 5,000-drop fee. These fees remain native XRP even when the simulated investment is denominated in RLUSD. Receipt transactions and reserves are sponsored separately. This is a single-process, Testnet-only custody demonstration, not live investment execution.

See the [runbook](agentic-demo.md) for setup and payment recovery, the [public API](api-reference.md) for the user/agent workflow, and the [validation record](validation.md) for completed tests. The implementation is in `agentic/mpt.py` and `agentic/mpt_lifecycle.py`; tests cover encryption, delivery and recovery. Production encryption and custody choices can change independently of this Testnet demonstration.
