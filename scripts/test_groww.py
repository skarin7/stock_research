"""Quick Groww API sanity check — run locally to rule out credential issues.

Usage:
    python scripts/test_groww.py [SYMBOL]

Example:
    python scripts/test_groww.py RELIANCE
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from config import SETTINGS

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"

print("=== Groww credential check ===")
has_access_token = bool(getattr(SETTINGS, "GROWW_ACCESS_TOKEN", ""))
has_totp = bool(SETTINGS.GROWW_TOTP_TOKEN and SETTINGS.GROWW_TOTP_SECRET)
has_key_secret = bool(SETTINGS.GROWW_API_KEY and SETTINGS.GROWW_API_SECRET)
print(f"GROWW_ACCESS_TOKEN set: {has_access_token}  ← daily token from portal (simplest)")
print(f"GROWW_TOTP pair set:    {has_totp}")
print(f"GROWW_API key+secret:   {has_key_secret}")
print()

if not (has_access_token or has_totp or has_key_secret):
    print("ERROR: no usable Groww credentials in .env")
    print("  Option 1: GROWW_ACCESS_TOKEN  (daily token from Groww Cloud portal)")
    print("  Option 2: GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET  (auto OTP)")
    print("  Option 3: GROWW_API_KEY + GROWW_API_SECRET")
    print()
    print("Groww will fail — yfinance fallback will kick in instead.")
    sys.exit(1)

print("=== Testing Groww auth ===")
try:
    from enrichment.market_data.groww import default_client
    client = default_client()
    profile = client.get_user_profile()
    print(f"Auth OK. User profile: {profile}")
except Exception as e:
    print(f"FAIL — auth error: {e}")
    print("yfinance fallback will be used instead.")

print()
print(f"=== Testing live quote: {SYMBOL} ===")
try:
    from enrichment.market_data.groww import GrowwProvider
    provider = GrowwProvider()
    quote = provider.get_quote(SYMBOL)
    print(f"Quote: {quote}")
except Exception as e:
    print(f"FAIL — quote fetch error: {e}")

print()
print(f"=== Testing OHLCV (last 5 days): {SYMBOL} ===")
try:
    from enrichment.market_data.groww import GrowwProvider
    provider = GrowwProvider()
    candles = provider.get_ohlcv(SYMBOL, days=5)
    print(f"Candles returned: {len(candles)} rows")
    if candles:
        print(f"Latest: {candles[-1]}")
except Exception as e:
    print(f"FAIL — OHLCV fetch error: {e}")

print()
print(f"=== yfinance fallback test: {SYMBOL} ===")
try:
    from enrichment.market_data.yfinance_provider import YFinanceProvider
    provider = YFinanceProvider()
    quote = provider.get_quote(SYMBOL)
    print(f"yfinance quote: {quote}")
except Exception as e:
    print(f"FAIL — yfinance error: {e}")
