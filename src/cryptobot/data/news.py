"""新闻数据获取

CoinGecko Demo API (免费 30 req/min，需设置环境变量 COINGECKO_DEMO_KEY)。
"""

import httpx

from cryptobot.cache import get_cache, set_cache
from cryptobot.config import get_coingecko_demo_key

COINGECKO_API = "https://api.coingecko.com/api/v3"
CACHE_TTL = 1800  # 30 分钟


def _cg_headers() -> dict:
    """CoinGecko Demo API 认证 header"""
    key = get_coingecko_demo_key()
    if key:
        return {"x-cg-demo-api-key": key}
    return {}

# CoinGecko ID 映射
SYMBOL_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "BNB": "binancecoin",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "SUI": "sui",
}


def get_market_overview() -> dict:
    """获取加密市场概览 (CoinGecko 全局数据)"""
    cache_key = "market_overview"
    cached = get_cache("news", cache_key, CACHE_TTL)
    if cached:
        return cached

    resp = httpx.get(f"{COINGECKO_API}/global", headers=_cg_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json().get("data", {})

    result = {
        "total_market_cap_usd": data.get("total_market_cap", {}).get("usd", 0),
        "total_volume_24h_usd": data.get("total_volume", {}).get("usd", 0),
        "btc_dominance": data.get("market_cap_percentage", {}).get("btc", 0),
        "eth_dominance": data.get("market_cap_percentage", {}).get("eth", 0),
        "market_cap_change_24h_pct": data.get("market_cap_change_percentage_24h_usd", 0),
        "active_cryptocurrencies": data.get("active_cryptocurrencies", 0),
    }
    set_cache("news", cache_key, result)
    return result


def get_coin_info(symbol: str = "BTC") -> dict:
    """获取单币种市场数据"""
    coin_id = SYMBOL_TO_ID.get(symbol.upper())
    if not coin_id:
        return {"error": f"未知币种: {symbol}"}

    cache_key = f"coin_{symbol.upper()}"
    cached = get_cache("news", cache_key, CACHE_TTL)
    if cached:
        return cached

    resp = httpx.get(
        f"{COINGECKO_API}/coins/{coin_id}",
        params={
            "localization": "false",
            "tickers": "false",
            "community_data": "true",
            "developer_data": "false",
        },
        headers=_cg_headers(),
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    market = data.get("market_data", {})
    result = {
        "symbol": symbol.upper(),
        "name": data.get("name", ""),
        "current_price": market.get("current_price", {}).get("usd", 0),
        "market_cap": market.get("market_cap", {}).get("usd", 0),
        "market_cap_rank": data.get("market_cap_rank", 0),
        "total_volume_24h": market.get("total_volume", {}).get("usd", 0),
        "price_change_24h_pct": market.get("price_change_percentage_24h", 0),
        "price_change_7d_pct": market.get("price_change_percentage_7d", 0),
        "price_change_30d_pct": market.get("price_change_percentage_30d", 0),
        "ath": market.get("ath", {}).get("usd", 0),
        "ath_change_pct": market.get("ath_change_percentage", {}).get("usd", 0),
        "atl": market.get("atl", {}).get("usd", 0),
        "circulating_supply": market.get("circulating_supply", 0),
        "total_supply": market.get("total_supply", 0),
        "sentiment_up_pct": data.get("sentiment_votes_up_percentage", 0),
        "sentiment_down_pct": data.get("sentiment_votes_down_percentage", 0),
    }
    set_cache("news", cache_key, result)
    return result


def get_trending() -> dict:
    """获取热门趋势币种"""
    cache_key = "trending"
    cached = get_cache("news", cache_key, CACHE_TTL)
    if cached:
        return cached

    resp = httpx.get(f"{COINGECKO_API}/search/trending", headers=_cg_headers(), timeout=10)
    resp.raise_for_status()
    data = resp.json()

    coins = []
    for item in data.get("coins", [])[:10]:
        coin = item.get("item", {})
        coins.append({
            "name": coin.get("name", ""),
            "symbol": coin.get("symbol", ""),
            "market_cap_rank": coin.get("market_cap_rank", 0),
            "score": coin.get("score", 0),
        })

    result = {"trending_coins": coins, "count": len(coins)}
    set_cache("news", cache_key, result)
    return result
