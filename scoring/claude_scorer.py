"""
Claude Haiku 4.5 Batch API scorer.
Submits stocks in batches of SCORING_BATCH_SIZE, polls for completion,
and returns a list of parsed score dicts.
"""

import base64
import functools
import json
import logging
import time
from typing import Optional

import anthropic

from config import SETTINGS
import llm_router
from scoring.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=SETTINGS.ANTHROPIC_API_KEY)


def _build_batch_requests(stocks: list[dict], news_map: dict[str, dict], macro_context: str = "",
                          sector_map: Optional[dict] = None) -> list[dict]:
    """Build Batch API request objects for a list of stocks."""
    sector_map = sector_map or {}
    requests_list = []
    for stock in stocks:
        sym = stock["symbol"]
        news = news_map.get(sym, {})
        headlines = news.get("headlines", [])
        sector_macro = sector_map.get(stock.get("sector"))
        user_content = build_user_prompt(stock, headlines, macro_context, sector_macro)
        safe_id = base64.urlsafe_b64encode(sym.encode()).decode().rstrip("=")[:64]
        requests_list.append({
            "custom_id": safe_id,
            "params": {
                "model": SETTINGS.SCORING_MODEL,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
        })
    return requests_list


def _submit_batch(requests_list: list[dict]) -> str:
    """Submit a batch and return the batch ID."""
    client = _get_client()
    batch = client.messages.batches.create(requests=requests_list)
    logger.info("Submitted batch %s with %d requests", batch.id, len(requests_list))
    return batch.id


def _poll_batch(batch_id: str, poll_interval: int = 30, max_wait_minutes: int = 30) -> list:
    """Poll until batch is complete. Returns list of result objects."""
    client = _get_client()
    max_polls = (max_wait_minutes * 60) // poll_interval

    for poll in range(1, max_polls + 1):
        batch = client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        logger.info(
            "Batch %s status: %s (poll %d/%d) — succeeded: %d, errored: %d",
            batch_id, status, poll, max_polls,
            batch.request_counts.succeeded, batch.request_counts.errored,
        )
        if status == "ended":
            break
        time.sleep(poll_interval)
    else:
        raise TimeoutError(f"Batch {batch_id} did not complete within {max_wait_minutes} minutes")

    # Stream results
    results = []
    for result in client.messages.batches.results(batch_id):
        results.append(result)
    return results


def _parse_result(result) -> Optional[dict]:
    """Extract and parse the JSON scorecard from a single batch result."""
    safe_id = result.custom_id
    padding = "=" * (4 - len(safe_id) % 4) if len(safe_id) % 4 else ""
    sym = base64.urlsafe_b64decode(safe_id + padding).decode()
    if result.result.type == "error":
        logger.error("Batch error for %s: %s", sym, result.result.error)
        return None

    # result.result.message.content is a list of ContentBlock
    text = ""
    for block in result.result.message.content:
        if hasattr(block, "text"):
            text = block.text.strip()
            break

    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            scorecard = json.loads(text[start:end])
            scorecard["ticker"] = sym  # ensure ticker matches our key
            return scorecard
    except json.JSONDecodeError as e:
        logger.error("JSON parse error for %s: %s | raw: %.200s", sym, e, text)

    return None


def _extract_scorecard(text: str, sym: str) -> Optional[dict]:
    """Lenient JSON extraction shared by the sync + OpenRouter paths."""
    text = (text or "").strip()
    try:
        start, end = text.find("{"), text.rfind("}") + 1
        if start != -1 and end > start:
            scorecard = json.loads(text[start:end])
            scorecard["ticker"] = sym
            return scorecard
    except json.JSONDecodeError as e:
        logger.error("JSON parse error for %s: %s | raw: %.200s", sym, e, text)
    return None


def _score_openrouter(stocks: list[dict], news_map: dict[str, dict], macro_context: str,
                      sector_map: Optional[dict] = None) -> list[dict]:
    """Score via OpenRouter (OpenAI-compatible). One sync call per stock — no Batch API."""
    sector_map = sector_map or {}
    client = llm_router.openrouter_client()
    model = llm_router.scoring_model()
    all_scores = []
    for stock in stocks:
        sym = stock["symbol"]
        headlines = news_map.get(sym, {}).get("headlines", [])
        sector_macro = sector_map.get(stock.get("sector"))
        user_content = build_user_prompt(stock, headlines, macro_context, sector_macro)
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
            )
            text = resp.choices[0].message.content if resp.choices else ""
            scorecard = _extract_scorecard(text, sym)
            if scorecard:
                all_scores.append(scorecard)
        except Exception as e:
            logger.error("OpenRouter scoring failed for %s: %s", sym, e)
    return all_scores


_SYNC_THRESHOLD = 20  # use synchronous API below this count; Batch API above


def _score_sync(stocks: list[dict], news_map: dict[str, dict], macro_context: str,
                sector_map: Optional[dict] = None) -> list[dict]:
    """Score stocks one-by-one using synchronous Messages API (fast, for small sets)."""
    sector_map = sector_map or {}
    client = _get_client()
    all_scores = []
    for stock in stocks:
        sym = stock["symbol"]
        headlines = news_map.get(sym, {}).get("headlines", [])
        sector_macro = sector_map.get(stock.get("sector"))
        user_content = build_user_prompt(stock, headlines, macro_context, sector_macro)
        try:
            resp = client.messages.create(
                model=SETTINGS.SCORING_MODEL,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_content}],
            )
            text = resp.content[0].text if resp.content else ""
            scorecard = _extract_scorecard(text, sym)
            if scorecard:
                all_scores.append(scorecard)
        except Exception as e:
            logger.error("Sync scoring failed for %s: %s", sym, e)
    return all_scores


def score_stocks(stocks: list[dict], news_map: dict[str, dict], macro_context: str = "",
                 sector_map: Optional[dict] = None) -> list[dict]:
    """
    Score stocks using Claude Haiku.
    Uses synchronous API for small sets (< _SYNC_THRESHOLD) for speed,
    and Batch API for large sets to save cost (50% cheaper).

    When SETTINGS.LLM_PROVIDER == "openrouter", all scoring is routed through
    OpenRouter (one sync call per stock — Batch API is Anthropic-only).
    """
    if llm_router.is_openrouter():
        logger.info("Scoring %d stocks via OpenRouter (%s)", len(stocks), llm_router.scoring_model())
        all_scores = _score_openrouter(stocks, news_map, macro_context, sector_map)
        logger.info("Scoring complete: %d/%d stocks successfully scored", len(all_scores), len(stocks))
        return all_scores

    if len(stocks) < _SYNC_THRESHOLD:
        logger.info("Scoring %d stocks via synchronous API (faster for small sets)", len(stocks))
        all_scores = _score_sync(stocks, news_map, macro_context, sector_map)
        logger.info("Scoring complete: %d/%d stocks successfully scored", len(all_scores), len(stocks))
        return all_scores

    batch_size = SETTINGS.SCORING_BATCH_SIZE
    all_scores: list[dict] = []
    batches = [stocks[i:i + batch_size] for i in range(0, len(stocks), batch_size)]
    logger.info("Scoring %d stocks via Batch API in %d batches", len(stocks), len(batches))

    batch_ids = []
    for i, chunk in enumerate(batches):
        logger.info("Submitting batch %d/%d (%d stocks)", i + 1, len(batches), len(chunk))
        req_list = _build_batch_requests(chunk, news_map, macro_context, sector_map)
        batch_ids.append(_submit_batch(req_list))

    for bid in batch_ids:
        for result in _poll_batch(bid):
            scorecard = _parse_result(result)
            if scorecard:
                all_scores.append(scorecard)

    logger.info("Scoring complete: %d/%d stocks successfully scored", len(all_scores), len(stocks))
    return all_scores
