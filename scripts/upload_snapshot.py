"""Upload a local snapshot.json to the DB daily_snapshot table.

Usage:
    python scripts/upload_snapshot.py [YYYY-MM-DD]

Defaults to today's date. Reads output/<date>/snapshot.json and upserts
into the daily_snapshot Postgres table so the chat agent can use it
without re-running the full pipeline.
"""
import sys
import json
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from config import SETTINGS

run_date = sys.argv[1] if len(sys.argv) > 1 else str(date.today())
snap_path = Path(SETTINGS.OUTPUT_DIR) / run_date / "snapshot.json"

if not snap_path.exists():
    print(f"ERROR: {snap_path} not found")
    sys.exit(1)

data = json.loads(snap_path.read_text())
rows = data.get("stocks", [])
print(f"Loaded {len(rows)} stocks from {snap_path}")

if not getattr(SETTINGS, "DATABASE_URL", ""):
    print("ERROR: DATABASE_URL not set — nothing to upload")
    sys.exit(1)

from persistence.store import save_daily_snapshot
save_daily_snapshot(run_date, rows)
print(f"Uploaded {len(rows)} stocks to DB for {run_date}")
