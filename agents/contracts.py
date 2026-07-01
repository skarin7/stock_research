"""Typed message contracts (Pydantic) exchanged between agents.

Each model is the *seam* between the typed agent layer and the existing
dict-based modules. The key models expose:
  - ``from_legacy(d)``      build the model from the plain dict the existing code produces
  - ``to_legacy_dict()``    serialise back to the exact dict shape the existing code expects

Nodes never reach into each other's internals — they pass these contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_native(v: Any) -> Any:
    """Recursively coerce numpy / pandas scalars to plain Python types.

    yfinance/pandas leak ``numpy.float64``/``int64`` and ``pandas.Timestamp``
    into the ``Any``-typed contract fields (``technicals``, ``extra``,
    ``news_map`` …). LangGraph's msgpack checkpoint serde has an ndarray branch
    but **no numpy-scalar branch**, so a stray ``np.float64`` makes the whole
    ``EnrichmentResult`` fail to serialize (``TypeError: Type is not msgpack
    serializable: EnrichmentResult`` — it names the top object, not the deep
    offender). Sanitising at construction keeps checkpointed state (and JSON /
    Telegram output) to native types only.
    """
    mod = type(v).__module__
    if mod == "numpy":
        return v.tolist() if hasattr(v, "tolist") else v.item()
    if mod.startswith("pandas") and hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _to_native(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_to_native(x) for x in v]
    return v


# ── Research → Analyst ────────────────────────────────────────────────────────

class UniverseResult(BaseModel):
    source: str
    total_screened: int = 0
    symbols: list[str] = Field(default_factory=list)


# Fields of the accumulated stock dict that we promote to typed attributes.
# Anything else is preserved in ``extra`` so the round-trip is lossless.
_PROMOTED_KEYS = {
    "symbol", "company", "sector", "pe_ratio", "sector_pe", "forward_pe",
    "market_cap_cr", "debt_equity", "delivery_pct", "volume_ratio",
    "ohlc_5d", "ohlc_10d", "ltp", "groww_volume", "bulk_deals",
    "next_earnings_date", "last_earnings_date", "days_to_earnings",
    "no_data", "technicals",
}
# Legacy keys that are not valid Python identifiers (handled explicitly).
_LEGACY_ALIASES = {"week52_high": "52w_high", "week52_low": "52w_low"}


class EnrichedStock(BaseModel):
    """Typed mirror of the stock dict that accumulates through Stages 1-4."""

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _coerce_native(cls, data: Any) -> Any:
        return _to_native(data) if isinstance(data, dict) else data

    symbol: str
    company: str = ""
    sector: Optional[str] = None
    pe_ratio: Optional[float] = None
    sector_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    market_cap_cr: Optional[float] = None
    debt_equity: Optional[float] = None
    delivery_pct: Optional[float] = None
    volume_ratio: Optional[float] = None
    ohlc_5d: list[list] = Field(default_factory=list)
    ohlc_10d: list[list] = Field(default_factory=list)
    ltp: Optional[float] = None
    groww_volume: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    bulk_deals: list[dict] = Field(default_factory=list)
    next_earnings_date: Optional[str] = None
    last_earnings_date: Optional[str] = None
    days_to_earnings: Optional[int] = None
    no_data: bool = False
    technicals: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_legacy(cls, d: dict) -> "EnrichedStock":
        data: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in d.items():
            if k == "52w_high":
                data["week52_high"] = v
            elif k == "52w_low":
                data["week52_low"] = v
            elif k in _PROMOTED_KEYS:
                data[k] = v
            else:
                extra[k] = v
        data["extra"] = extra
        return cls(**data)

    def to_legacy_dict(self) -> dict:
        d = self.model_dump(exclude={"extra", "week52_high", "week52_low"})
        if self.week52_high is not None:
            d["52w_high"] = self.week52_high
        if self.week52_low is not None:
            d["52w_low"] = self.week52_low
        d.update(self.extra)
        return d


class EnrichmentResult(BaseModel):
    stocks: list[EnrichedStock] = Field(default_factory=list)
    news_map: dict[str, dict] = Field(default_factory=dict)
    macro_context: str = ""
    sector_macro_map: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_native(cls, data: Any) -> Any:
        # Sanitise news_map / sector_macro_map (Any-typed); EnrichedStock
        # instances carry their own validator, so they pass through untouched.
        return _to_native(data) if isinstance(data, dict) else data

    def legacy_stocks(self) -> list[dict]:
        return [s.to_legacy_dict() for s in self.stocks]


# ── Analyst → Debate ──────────────────────────────────────────────────────────

class SignalScore(BaseModel):
    score: Optional[int] = None
    reason: str = ""


class Scorecard(BaseModel):
    """Round-trips to the exact dict claude_scorer emits and ranker consumes."""

    ticker: str
    composite_score: float = 0.0
    signals: dict[str, SignalScore] = Field(default_factory=dict)
    earnings_proximity: bool = False
    investment_rationale: str = ""
    risk_flags: list[str] = Field(default_factory=list)

    @classmethod
    def from_legacy(cls, d: dict) -> "Scorecard":
        signals = {
            k: SignalScore(score=v.get("score"), reason=v.get("reason", ""))
            for k, v in (d.get("signals") or {}).items()
        }
        return cls(
            ticker=d.get("ticker", ""),
            composite_score=float(d.get("composite_score") or 0.0),
            signals=signals,
            earnings_proximity=bool(d.get("earnings_proximity", False)),
            investment_rationale=d.get("investment_rationale", ""),
            risk_flags=list(d.get("risk_flags") or []),
        )

    def to_legacy_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "composite_score": self.composite_score,
            "signals": {
                k: {"score": v.score, "reason": v.reason} for k, v in self.signals.items()
            },
            "earnings_proximity": self.earnings_proximity,
            "investment_rationale": self.investment_rationale,
            "risk_flags": self.risk_flags,
        }


class RankingResult(BaseModel):
    top: list[Scorecard] = Field(default_factory=list)
    all_scores: list[Scorecard] = Field(default_factory=list)

    def legacy_top(self) -> list[dict]:
        return [s.to_legacy_dict() for s in self.top]

    def legacy_all(self) -> list[dict]:
        return [s.to_legacy_dict() for s in self.all_scores]


# ── Debate → Trading ──────────────────────────────────────────────────────────

class DebateTurn(BaseModel):
    side: str            # "bull" | "bear"
    argument: str


class ConvictionView(BaseModel):
    ticker: str
    direction: str = "neutral"     # "long" | "short" | "neutral"
    conviction: float = 0.0        # 0.0 - 1.0
    bull_case: str = ""
    bear_case: str = ""
    transcript: list[DebateTurn] = Field(default_factory=list)
    composite_score: float = 0.0


# ── Trading / Risk / Portfolio ────────────────────────────────────────────────

class ProposalStatus(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PLACED = "placed"
    FILLED = "filled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    BLOCKED = "blocked"
    ERROR = "error"
    HALTED = "halted"


class RiskCheck(BaseModel):
    rule: str            # position_limit | sector_cap | earnings_block | stop_loss | max_open
    passed: bool
    detail: str = ""


class TradeProposal(BaseModel):
    proposal_id: str
    run_id: str = ""
    ticker: str
    side: str                       # BUY | SELL
    qty: int = 0
    order_type: str = "MARKET"      # MARKET | LIMIT
    limit_price: Optional[float] = None
    rationale: str = ""
    conviction: float = 0.0
    status: ProposalStatus = ProposalStatus.PROPOSED
    risk_checks: list[RiskCheck] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utcnow_iso)
    approved_at: Optional[str] = None
    expires_at: Optional[str] = None
    broker_order_id: Optional[str] = None


class Position(BaseModel):
    ticker: str
    qty: int
    avg_price: float
    stop_price: Optional[float] = None
    sector: Optional[str] = None
    opened_at: str = Field(default_factory=_utcnow_iso)


class PortfolioState(BaseModel):
    cash: float = 0.0
    positions: list[Position] = Field(default_factory=list)
    total_exposure: float = 0.0
    sector_exposure: dict[str, float] = Field(default_factory=dict)


class Alert(BaseModel):
    ticker: str
    kind: str            # stop_triggered | earnings_soon | anomaly | gap
    severity: str = "info"   # info | warn | critical
    message: str = ""
