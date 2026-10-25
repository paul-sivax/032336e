import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.getenv("PAYMENT_MODE", "mock"))
    risk_mode: str = field(default_factory=lambda: os.getenv("RISK_MODE", "off"))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", ".demo-data"))
    )
    access_key: str = field(
        default_factory=lambda: os.getenv("DEMO_ACCESS_KEY", "local-demo-only")
    )
    service_key: str = field(
        default_factory=lambda: os.getenv("SERVICE_KEY", "local-service-only")
    )
    mock_secret: str = field(
        default_factory=lambda: os.getenv("MOCK_PAYMENT_SECRET", "local-mock-only")
    )
    api_url: str = field(
        default_factory=lambda: os.getenv("PUBLIC_API_URL", "http://localhost:8010")
    )
    web_url: str = field(
        default_factory=lambda: os.getenv("PUBLIC_WEB_URL", "http://localhost:5173")
    )
    orders_url: str = field(
        default_factory=lambda: os.getenv("ORDERS_URL", "http://127.0.0.1:8012")
    )
    network: str = field(default_factory=lambda: os.getenv("XRPL_NETWORK", "xrpl:1"))
    rpc_url: str = field(
        default_factory=lambda: os.getenv(
            "XRPL_RPC_URL", "https://s.altnet.rippletest.net:51234/"
        )
    )
    facilitator_url: str = field(
        default_factory=lambda: os.getenv(
            "XRPL_FACILITATOR_URL", "https://xrpl-facilitator-testnet.t54.ai"
        )
    )
    seed: str = field(
        default_factory=lambda: os.getenv("XRPL_PAYER_SEED", ""), repr=False
    )
    pay_to: str = field(default_factory=lambda: os.getenv("XRPL_PAY_TO", ""))
    mpt_issuer_seed: str = field(
        default_factory=lambda: os.getenv("XRPL_MPT_ISSUER_SEED", ""), repr=False
    )
    mpt_holder_seed: str = field(
        default_factory=lambda: os.getenv("XRPL_MPT_HOLDER_SEED", ""), repr=False
    )
    mpt_metadata_key: str = field(
        default_factory=lambda: os.getenv("MPT_METADATA_KEY", ""), repr=False
    )
    mpt_key_id: str = field(
        default_factory=lambda: os.getenv("MPT_METADATA_KEY_ID", "k1")
    )
    source_tag: int | None = field(
        default_factory=lambda: (
            int(os.environ["XRPL_SOURCE_TAG"]) if os.getenv("XRPL_SOURCE_TAG") else None
        )
    )
    rfq_fee: int = field(
        default_factory=lambda: int(os.getenv("RFQ_FEE_DROPS", "1000"))
    )
    order_fee: int = field(
        default_factory=lambda: int(os.getenv("ORDER_FEE_DROPS", "5000"))
    )

    network_fee_cap: int = 1000
    max_rfqs: int = 30
    session_cap: int = 100000
    quote_ttl: int = 600
    hold_ttl: int = 180
    execution_ttl: int = 180
    catalog_latency_ms: int = 800  # Demo delivery pacing; Turbo halves this.

    def validate(self, role: str = "orchestrator"):
        if self.mode not in {"mock", "xrpl_testnet"} or self.network != "xrpl:1":
            raise ValueError(
                "Only mock payments or XRPL Testnet (xrpl:1) are supported."
            )
        if self.risk_mode not in {"off", "mock"}:
            raise ValueError(
                "Live t54 enforcement is not configured; refusing to downgrade risk policy."
            )
        if min(self.rfq_fee, self.order_fee) <= 0 or self.max_rfqs > 36:
            raise ValueError("Invalid demo tariff or request limit.")
        if self.mode == "xrpl_testnet":
            if urlparse(self.rpc_url).hostname != "s.altnet.rippletest.net":
                raise ValueError(
                    "Signer RPC must use the allowlisted XRPL Testnet host."
                )
            if not self.pay_to or (role == "orchestrator" and not self.seed):
                raise ValueError(
                    "Configure a testnet payee and payer seed before enabling testnet payments."
                )
            if role == "orders":
                import base64
                import re

                from xrpl.wallet import Wallet

                if not self.mpt_issuer_seed or not self.mpt_holder_seed:
                    raise ValueError(
                        "Testnet orders require dedicated MPT issuer and demo holder seeds."
                    )
                addresses = [
                    Wallet.from_seed(v).address
                    for v in (self.mpt_issuer_seed, self.mpt_holder_seed)
                ]
                if self.seed:
                    addresses.append(Wallet.from_seed(self.seed).address)
                if len(addresses) != len(set(addresses)):
                    raise ValueError(
                        "MPT issuer, holder and x402 payer must use separate wallet lanes."
                    )
                try:
                    key = base64.b64decode(self.mpt_metadata_key, validate=True)
                except Exception:
                    key = b""
                if len(key) != 32 or not re.fullmatch(
                    r"[A-Za-z0-9_-]{1,16}", self.mpt_key_id
                ):
                    raise ValueError(
                        "Set a private base64-encoded 32-byte MPT_METADATA_KEY and bounded key ID."
                    )
            if (
                self.access_key == "local-demo-only"
                or self.service_key == "local-service-only"
            ):
                raise ValueError(
                    "Set private demo and service credentials for testnet payments."
                )
            host = urlparse(self.facilitator_url).hostname or ""
            # Hosted deployment's documented requirement, not a protocol-wide default.
            if host == "xrpl-facilitator-testnet.t54.ai" and self.source_tag is None:
                self.source_tag = 804681468
            if self.source_tag is not None and not 0 <= self.source_tag <= 0xFFFFFFFF:
                raise ValueError("SourceTag must be a uint32.")
            if host != "xrpl-facilitator-testnet.t54.ai" and host not in {
                "localhost",
                "127.0.0.1",
                "facilitator",
            }:
                raise ValueError(
                    "Use the testnet facilitator or an explicitly local testnet facilitator."
                )
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def public(self):
        from .domain import ASSET_NAMES, TERMS
        from .market_calendar import preview

        return {
            "payment_mode": self.mode,
            "risk_mode": self.risk_mode,
            "rfq_fee_drops": str(self.rfq_fee),
            "service_tiers": {
                "standard": {"fee_multiplier": 1, "delivery_speed": 1},
                "turbo": {"fee_multiplier": 2, "delivery_speed": 2},
            },
            "speed_scope": "simulated_catalog_delivery",
            "order_fee_drops": str(self.order_fee),
            "network_fee_cap_drops": str(self.network_fee_cap),
            "max_rfqs": self.max_rfqs,
            "session_cap_drops": str(self.session_cap),
            "api_url": self.api_url,
            "payer": "SIVAX sponsored demo wallet",
            "execution": "simulated",
            "conversation_mode": "guided",
            "request_schema": "sivax.agent-request.v4",
            "seller": "SIVAX",
            "investment_currencies": ["RLUSD", "XRP"],
            "catalog_sizes": {"protected": 12, "unprotected": 30},
            "catalog_assets": ASSET_NAMES,
            "catalog_total": {"fcn": 1104, "cma": 36},
            "quote_source": "precomputed_catalog",
            "mpt_mode": self.mode,
            "mpt_recipient": "SIVAX sponsored demo custody wallet",
            "mpt_network_fee_cap_drops": str(3 * self.network_fee_cap),
            "rfq_transport": "batch",
            "business_day_calendar": "XNYS",
            "calendar_preview": preview(TERMS),
            "budget_scope": "per_explicit_round",
            "network": self.network,
        }
