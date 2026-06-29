"""run_intraday.py — RETIRED. Use: python run_agents.py --mode intraday"""
import subprocess
import sys
import warnings

warnings.warn(
    "run_intraday.py is retired. Use: python run_agents.py --mode intraday",
    DeprecationWarning,
    stacklevel=1,
)
sys.exit(subprocess.call([sys.executable, "run_agents.py", "--mode", "intraday"] + sys.argv[1:]))
