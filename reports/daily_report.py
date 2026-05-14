"""
Generates the daily HTML + JSON report.
Uses Jinja2 for HTML and calls Claude Sonnet 4.6 for the top-10 narrative.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

import anthropic
from jinja2 import Environment, FileSystemLoader

import config

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _generate_narrative(top_stocks: list[dict], macro_context: str = "") -> str:
    """
    Call Claude Sonnet 4.6 to write a 2–3 paragraph investment narrative
    for the top-10 stocks of the day.
    """
    summary = [
        {
            "rank": i + 1,
            "ticker": s["ticker"],
            "composite_score": s["composite_score"],
            "rationale": s.get("investment_rationale", ""),
            "risk_flags": s.get("risk_flags", []),
        }
        for i, s in enumerate(top_stocks[:10])
    ]
    macro_section = f"\nMarket macro context:\n{macro_context}\n" if macro_context else ""
    prompt = (
        "You are a senior equity analyst writing a morning briefing for a retail investor "
        "with a 5–30 day holding horizon. Based on today's AI-scored watchlist and the "
        "current macro environment, write a concise 2–3 paragraph narrative highlighting "
        "the top themes, standout picks, and key risks. Be direct, factual, and avoid "
        "generic disclaimers.\n"
        f"{macro_section}\n"
        f"Today's top picks:\n{json.dumps(summary, indent=2)}"
    )
    try:
        resp = _get_client().messages.create(
            model=config.REPORT_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.error("Narrative generation failed: %s", e)
        return ""


def save_scores_json(scored_stocks: list[dict], output_dir: Path) -> Path:
    """Write scores.json to the output directory."""
    path = output_dir / "scores.json"
    with open(path, "w") as f:
        json.dump(scored_stocks, f, indent=2, ensure_ascii=False)
    logger.info("Saved scores.json → %s", path)
    return path


def generate_html_report(
    top_stocks: list[dict],
    report_date: date,
    total_screened: int,
    total_scored: int,
    backtest_summary: Optional[dict] = None,
    generate_narrative: bool = True,
    macro_context: str = "",
) -> str:
    """
    Render the Jinja2 HTML template and return the HTML string.
    Also generates the Sonnet narrative unless generate_narrative=False.
    """
    from datetime import datetime

    narrative = _generate_narrative(top_stocks, macro_context) if generate_narrative else ""

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("daily_report.html")
    html = template.render(
        report_date=report_date.strftime("%Y-%m-%d"),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        total_screened=total_screened,
        total_scored=total_scored,
        stocks=top_stocks,
        narrative=narrative,
        backtest=backtest_summary,
    )
    return html


def write_report(
    top_stocks: list[dict],
    all_scores: list[dict],
    report_date: Optional[date] = None,
    total_screened: int = 0,
    backtest_summary: Optional[dict] = None,
    generate_narrative: bool = True,
    macro_context: str = "",
) -> Path:
    """
    Full report writer:
    1. Creates output/YYYY-MM-DD/ directory
    2. Writes scores.json
    3. Generates and writes report.html
    Returns path to the HTML report.
    """
    target_date = report_date or date.today()
    date_str = target_date.strftime("%Y-%m-%d")
    output_dir = Path(config.OUTPUT_DIR) / date_str
    output_dir.mkdir(parents=True, exist_ok=True)

    save_scores_json(all_scores, output_dir)

    html = generate_html_report(
        top_stocks=top_stocks,
        report_date=target_date,
        total_screened=total_screened,
        total_scored=len(all_scores),
        backtest_summary=backtest_summary,
        generate_narrative=generate_narrative,
        macro_context=macro_context,
    )

    report_path = output_dir / "report.html"
    report_path.write_text(html, encoding="utf-8")
    logger.info("Daily report saved → %s", report_path)
    return report_path
