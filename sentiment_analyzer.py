"""
Use Claude (Anthropic API) to rate the market sentiment for a crypto coin
based on recent news headlines.

Claude returns a score from 1 (very bearish) to 10 (very bullish).
We buy when the score is >= SENTIMENT_BUY_THRESHOLD (default 7).
"""
import re
import logging
import anthropic
from config import ANTHROPIC_API_KEY
from cost_tracker import cost_tracker

log = logging.getLogger(__name__)

# Re-use one client for all requests (more efficient)
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set in your .env file. "
                "Get your key at https://console.anthropic.com"
            )
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """\
You are a cryptocurrency market sentiment analyst.
You read recent news headlines and summaries, then rate the sentiment
for a specific coin on a scale of 1–10:

  1–3  : Very bearish (bad news, crashes, regulation crackdowns, FUD)
  4–6  : Neutral or mixed (no clear direction)
  7–9  : Bullish (positive news, adoption, partnerships, price momentum)
  10   : Extremely bullish (rare – only major positive catalysts)

Respond in EXACTLY this format (two lines, nothing else):
SCORE: <number>
REASONING: <one or two sentences explaining your rating>
"""


def analyze_sentiment(coin_symbol: str, news_text: str) -> dict:
    """
    Ask Claude to rate the sentiment of recent news for a coin.

    Args:
        coin_symbol: e.g. "BTC", "ETH"
        news_text:   Formatted news articles from news_client.format_articles_for_prompt()

    Returns:
        {"score": float, "reasoning": str}
        Defaults to neutral (5.0) if anything goes wrong.
    """
    try:
        client = _get_client()
    except ValueError as e:
        log.warning(str(e))
        return {"score": 5.0, "reasoning": "No Anthropic API key - defaulting to neutral"}

    prompt = (
        f"Analyze the market sentiment for {coin_symbol} cryptocurrency "
        f"based on these recent news articles:\n\n{news_text}\n\n"
        f"Rate the sentiment from 1–10."
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Fast + cheap for quick analysis
            max_tokens=150,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        
        # Track cost for this API call
        input_tokens = message.usage.input_tokens if hasattr(message, 'usage') else len(prompt.split()) * 1.3  # rough estimate
        output_tokens = message.usage.output_tokens if hasattr(message, 'usage') else len(message.content[0].text.split()) * 1.3
        cost_tracker.track_claude_usage(int(input_tokens), int(output_tokens))
        
        raw = message.content[0].text.strip()
        log.debug(f"Claude raw response for {coin_symbol}:\n{raw}")
        return _parse_response(raw)

    except anthropic.APIError as e:
        log.error(f"Anthropic API error for {coin_symbol}: {e}")
        return {"score": 5.0, "reasoning": f"API error: {e}"}
    except Exception as e:
        log.error(f"Unexpected error during sentiment analysis for {coin_symbol}: {e}")
        return {"score": 5.0, "reasoning": f"Unexpected error: {e}"}


def _parse_response(text: str) -> dict:
    """
    Parse Claude's two-line SCORE / REASONING response.
    Falls back to neutral defaults if parsing fails.
    """
    score = 5.0
    reasoning = "Could not parse Claude response"

    score_match = re.search(r"SCORE:\s*(\d+(?:\.\d+)?)", text)
    if score_match:
        score = float(score_match.group(1))
        score = max(1.0, min(10.0, score))  # Clamp to valid range

    reasoning_match = re.search(r"REASONING:\s*(.+)", text, re.DOTALL)
    if reasoning_match:
        reasoning = reasoning_match.group(1).strip()

    return {"score": score, "reasoning": reasoning}
