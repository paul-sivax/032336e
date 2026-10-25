# Validation record

This public package restores application revision `032336e9e9f0c15bffbe8ee893cd459bac484e4e`, including the final document-presentation frame. Application modules, frontend source, quote data and runtime assets match that revision. Packaging, documentation and test configuration were restored separately after the previous download expired.

## Recovery validation

- Frontend type check and production build: passed.
- Python regression: 35 passed, 1 opt-in live Testnet test skipped. Four upstream pandas deprecation warnings remain.
- Python lint (Ruff E4/E7/E9/F/I): passed.
- Browser regression: 16 passed, covering desktop/mobile journeys and alignment of all 21 mascot frames.

## Recorded XRPL Testnet run

The [catalog Testnet run](testnet-catalog-validation.json) used 30 Turbo quotes, one 0.06 XRP batch payment and a 0.005 XRP order fee. It verified encrypted MPT issuance, holder authorization, delivery, authenticated term-sheet decryption, return and unauthorization with Trustline off. Investment principal remained simulated. The outcome summary excludes wallet addresses, keys, private terms and detailed ledger identifiers.

No new Testnet transactions were sent during download recovery. Live LLM-provider mode and Docker execution were not exercised. The ordinary regression suite uses mock credits.
