"""
Claude Haiku 4.5 Batch API scorer.
Submits stocks in batches of SCORING_BATCH_SIZE, polls for completion,
and returns a list of parsed score dicts.
"""

import base64
import json
import logging
import time
from typing import Optional

import anthropic

import config
from scoring.prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _build_batch_requests(stocks: list[dict], news_map: dict[str, dict], macro_context: str = "") -> list[dict]:
    """Build Batch API request objects for a list of stocks."""
    requests_list = []
    for stock in stocks:
        sym = stock["symbol"]
        news = news_map.get(sym, {})
        headlines = news.get("headlines", [])
        user_content = build_user_prompt(stock, headlines, macro_context)
        safe_id = base64.urlsafe_b64encode(sym.encode()).decode().rstrip("=")[:64]
        requests_list.append({
            "custom_id": safe_id,
            "params": {
                "model": config.SCORING_MODEL,
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


def score_stocks(stocks: list[dict], news_map: dict[str, dict], macro_context: str = "") -> list[dict]:
    """
    Score all stocks using Claude Haiku Batch API.
    Submits in batches of SCORING_BATCH_SIZE, polls each batch, returns parsed scorecards.
    Stocks that fail parsing are omitted from results.
    """
    batch_size = config.SCORING_BATCH_SIZE
    all_scores: list[dict] = []
    batches = [stocks[i:i + batch_size] for i in range(0, len(stocks), batch_size)]

    logger.info("Scoring %d stocks in %d batches (batch size=%d)", len(stocks), len(batches), batch_size)

    batch_ids = []
    for i, chunk in enumerate(batches):
        logger.info("Submitting batch %d/%d (%d stocks)", i + 1, len(batches), len(chunk))
        req_list = _build_batch_requests(chunk, news_map, macro_context)
        bid = _submit_batch(req_list)
        batch_ids.append(bid)

    # Poll all batches (sequentially to avoid hammering the API)
    for bid in batch_ids:
        results = _poll_batch(bid)
        for result in results:
            scorecard = _parse_result(result)
            if scorecard:
                all_scores.append(scorecard)

    logger.info("Scoring complete: %d/%d stocks successfully scored", len(all_scores), len(stocks))
    return all_scores
