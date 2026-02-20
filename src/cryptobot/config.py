"""全局配置加载"""

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_OUTPUT_DIR = PROJECT_ROOT / "data" / "output"
FREQTRADE_DATA_DIR = PROJECT_ROOT / "user_data" / "data" / "binance" / "futures"
FREQTRADE_DATA_DIR_ALT = PROJECT_ROOT / "user_data" / "data" / "futures"


def load_settings() -> dict:
    path = CONFIG_DIR / "settings.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text())


def load_pairs() -> dict:
    path = CONFIG_DIR / "pairs.yaml"
    if not path.exists():
        return {"pairs": []}
    return yaml.safe_load(path.read_text())


def get_pair_config(symbol: str) -> dict | None:
    pairs = load_pairs()
    for p in pairs.get("pairs", []):
        if p["symbol"] == symbol:
            return p
    return None


def get_all_symbols() -> list[str]:
    pairs = load_pairs()
    return [p["symbol"] for p in pairs.get("pairs", [])]


def get_coingecko_demo_key() -> str:
    return os.environ.get("COINGECKO_DEMO_KEY", "")


def get_cryptonews_api_key() -> str:
    return os.environ.get("CRYPTONEWS_API_KEY", "")
