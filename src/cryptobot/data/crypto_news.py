"""CryptoNews-API 新闻数据 (Premium Plan)

实时新闻 + 内置情绪分析 + Top Mentions + Trending Headlines。
需设置环境变量 CRYPTONEWS_API_KEY。
"""

import httpx

from cryptobot.cache import get_cache, set_cache
from cryptobot.config import get_cryptonews_api_key

CRYPTONEWS_API = "https://cryptonews-api.com/api/v1"
CACHE_TTL = 1800  # 30 分钟


def _api_params(extra: dict | None = None) -> dict:
    """构建带 token 的请求参数"""
    params = {"token": get_cryptonews_api_key()}
    if extra:
        params.update(extra)
    return params


def get_crypto_news(currencies: list[str] | None = None) -> dict:
    """获取全局加密新闻 + 情绪聚合

    Args:
        currencies: 货币代码列表 (如 ["BTC", "ETH"])。None 表示通用新闻。
    """
    api_key = get_cryptonews_api_key()
    if not api_key:
        return {"error": "CRYPTONEWS_API_KEY 未设置", "articles": [], "sentiment_score": 0}

    cache_key = "global_news"
    cached = get_cache("crypto_news", cache_key, CACHE_TTL)
    if cached:
        return cached

    # 全局新闻: 有 tickers 则用 tickers，否则用 general
    if currencies:
        params = _api_params({"tickers": ",".join(currencies), "items": 20, "page": 1})
        url = CRYPTONEWS_API
    else:
        params = _api_params({"section": "general", "items": 20, "page": 1})
        url = f"{CRYPTONEWS_API}/category"

    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e), "articles": [], "sentiment_score": 0}

    articles, positive, negative, neutral = _parse_articles(data.get("data", []), limit=20)

    total = positive + negative + neutral
    sentiment_score = (positive - negative) / total if total > 0 else 0

    result = {
        "articles": articles,
        "positive_count": positive,
        "negative_count": negative,
        "neutral_count": neutral,
        "total_count": total,
        "sentiment_score": round(sentiment_score, 3),
    }
    set_cache("crypto_news", cache_key, result)
    return result


def get_coin_specific_news(symbol: str) -> dict:
    """获取单币种新闻 + 情绪评分"""
    base = symbol.replace("USDT", "").upper()

    api_key = get_cryptonews_api_key()
    if not api_key:
        return {"symbol": symbol, "error": "CRYPTONEWS_API_KEY 未设置", "articles": [], "sentiment_score": 0}

    cache_key = f"coin_news_{base}"
    cached = get_cache("crypto_news", cache_key, CACHE_TTL)
    if cached:
        return cached

    params = _api_params({"tickers": base, "items": 10, "page": 1})

    try:
        resp = httpx.get(CRYPTONEWS_API, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "articles": [], "sentiment_score": 0}

    articles, positive, negative, neutral = _parse_articles(data.get("data", []), limit=10)

    total = positive + negative + neutral
    sentiment_score = (positive - negative) / total if total > 0 else 0

    # 最近的重要新闻 (取前 3 条有明确情绪的)
    important = [a for a in articles if a["sentiment"] != "Neutral"][:3]

    result = {
        "symbol": symbol,
        "ticker": base,
        "articles": articles,
        "positive_count": positive,
        "negative_count": negative,
        "total_count": len(articles),
        "sentiment_score": round(sentiment_score, 3),
        "important_news": important,
    }
    set_cache("crypto_news", cache_key, result)
    return result


def get_top_mentions() -> dict:
    """获取 Top 50 最多被提及的币种 (Premium 功能)"""
    api_key = get_cryptonews_api_key()
    if not api_key:
        return {"error": "CRYPTONEWS_API_KEY 未设置"}

    cache_key = "top_mentions"
    cached = get_cache("crypto_news", cache_key, CACHE_TTL)
    if cached:
        return cached

    params = _api_params({"date": "last7days"})

    try:
        resp = httpx.get(f"{CRYPTONEWS_API}/top-mention", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e)}

    result = {"mentions": data.get("data", []), "period": "last7days"}
    set_cache("crypto_news", cache_key, result)
    return result


def get_trending_headlines() -> dict:
    """获取热门头条新闻 (Premium 功能)"""
    api_key = get_cryptonews_api_key()
    if not api_key:
        return {"error": "CRYPTONEWS_API_KEY 未设置", "articles": []}

    cache_key = "trending_headlines"
    cached = get_cache("crypto_news", cache_key, CACHE_TTL)
    if cached:
        return cached

    params = _api_params({"page": 1})

    try:
        resp = httpx.get(f"{CRYPTONEWS_API}/trending-headlines", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"error": str(e), "articles": []}

    articles, positive, negative, neutral = _parse_articles(data.get("data", []), limit=10)

    result = {
        "articles": articles,
        "positive_count": positive,
        "negative_count": negative,
        "total_count": len(articles),
    }
    set_cache("crypto_news", cache_key, result)
    return result


def get_coin_sentiment(symbol: str) -> dict:
    """获取币种情绪统计 (Premium 功能，sentiment score -1.5 ~ +1.5)"""
    base = symbol.replace("USDT", "").upper()

    api_key = get_cryptonews_api_key()
    if not api_key:
        return {"symbol": symbol, "error": "CRYPTONEWS_API_KEY 未设置"}

    cache_key = f"sentiment_{base}"
    cached = get_cache("crypto_news", cache_key, CACHE_TTL)
    if cached:
        return cached

    params = _api_params({"tickers": base, "date": "last7days", "page": 1})

    try:
        resp = httpx.get(f"{CRYPTONEWS_API}/stat", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

    result = {"symbol": symbol, "ticker": base, "sentiment_data": data.get("data", [])}
    set_cache("crypto_news", cache_key, result)
    return result


# ─── 内部解析 ────────────────────────────────────────────────────────────

def _parse_articles(items: list, limit: int = 20) -> tuple[list, int, int, int]:
    """解析 CryptoNews-API 的文章列表，返回 (articles, positive, negative, neutral)"""
    articles = []
    positive = 0
    negative = 0
    neutral = 0

    for item in items[:limit]:
        sentiment = item.get("sentiment", "Neutral")

        if sentiment == "Positive":
            positive += 1
        elif sentiment == "Negative":
            negative += 1
        else:
            neutral += 1

        articles.append({
            "title": item.get("title", ""),
            "source": item.get("source_name", ""),
            "published_at": item.get("date", ""),
            "sentiment": sentiment,
            "tickers": item.get("tickers", []),
        })

    return articles, positive, negative, neutral
