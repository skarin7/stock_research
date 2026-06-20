"""Local end-to-end smoke test for the chat agent — no Telegram.

Seeds a small synthetic daily snapshot (real NSE tickers) so screen_snapshot
works, then drives run_turn() with representative prompts. macro_search and
timing hit live APIs (Tavily / market-data provider). Prints each reply.

    python scripts/test_chat_e2e.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from config import SETTINGS  # noqa: E402  (after load_dotenv)
from persistence import store  # noqa: E402

# A handful of real NSE symbols so screen_snapshot / sector mapping have data.
_SNAPSHOT = [
    {"symbol": "TCS", "company": "Tata Consultancy Services", "sector": "IT",
     "pe_ratio": 28.0, "sector_pe": 30.0, "market_cap_cr": 1400000.0, "ltp": 3850.0,
     "week52_high": 4250.0, "week52_low": 3050.0, "composite_score": 7.2,
     "news": ["TCS wins large deal"], "rationale": "steady compounder",
     "risk_flags": [], "signals": {"value": {"score": 7, "reason": "fair"}}},
    {"symbol": "RELIANCE", "company": "Reliance Industries", "sector": "Energy",
     "pe_ratio": 24.0, "sector_pe": 18.0, "market_cap_cr": 1900000.0, "ltp": 2950.0,
     "week52_high": 3100.0, "week52_low": 2200.0, "composite_score": 6.8,
     "news": ["Reliance retail update"], "rationale": "oil + retail",
     "risk_flags": ["crude"], "signals": {"value": {"score": 6, "reason": "premium"}}},
    {"symbol": "ONGC", "company": "Oil and Natural Gas Corp", "sector": "Energy",
     "pe_ratio": 7.0, "sector_pe": 18.0, "market_cap_cr": 320000.0, "ltp": 255.0,
     "week52_high": 345.0, "week52_low": 200.0, "composite_score": 6.0,
     "news": ["Crude prices firm up"], "rationale": "cheap, crude-levered",
     "risk_flags": [], "signals": {"value": {"score": 9, "reason": "cheap"}}},
    {"symbol": "INDIGO", "company": "InterGlobe Aviation", "sector": "Aviation",
     "pe_ratio": 22.0, "sector_pe": 25.0, "market_cap_cr": 170000.0, "ltp": 4400.0,
     "week52_high": 5000.0, "week52_low": 3000.0, "composite_score": 5.5,
     "news": ["Air traffic strong"], "rationale": "fuel-cost sensitive",
     "risk_flags": ["crude", "fuel"], "signals": {"value": {"score": 5, "reason": "rich"}}},
]

_PROMPTS = [
    "What's the best time to buy TCS right now? Give me an entry zone and stop.",
    "What is the impact of the Iran war on the Indian stock market, and which "
    "stocks in your universe are most affected?",
    "Show me the cheapest energy stocks you know with a composite score above 5.",
    "What did you think of RELIANCE before?",
]


def main() -> int:
    print(f"provider={SETTINGS.LLM_PROVIDER}  chat_model="
          f"{getattr(SETTINGS, 'CHAT_MODEL', '') or SETTINGS.REPORT_MODEL}")
    print(f"tavily={'set' if getattr(SETTINGS, 'TAVILY_API_KEY', '') else 'MISSING'}  "
          f"db={'set' if getattr(SETTINGS, 'DATABASE_URL', '') else 'none (MemorySaver)'}")

    store.save_daily_snapshot(date.today().isoformat(), _SNAPSHOT)
    print(f"seeded snapshot: {[r['symbol'] for r in _SNAPSHOT]}\n")

    from agents.chat.agent import run_turn

    chat_id = "e2e-local"
    for i, prompt in enumerate(_PROMPTS, 1):
        print(f"{'=' * 70}\n[{i}] USER: {prompt}\n{'-' * 70}")
        reply = run_turn(chat_id, prompt)
        print(f"BOT:\n{reply}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
