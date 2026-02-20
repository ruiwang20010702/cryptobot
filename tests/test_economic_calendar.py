"""宏观经济日历数据测试"""

from unittest.mock import patch, MagicMock

from cryptobot.data.economic_calendar import get_upcoming_events, _empty_result


def _mock_finnhub_response():
    """模拟 FinnHub API 响应"""
    return {
        "economicCalendar": [
            {
                "country": "US",
                "event": "FOMC Interest Rate Decision",
                "impact": "high",
                "time": "2026-02-21 19:00:00",
            },
            {
                "country": "US",
                "event": "CPI YoY",
                "impact": "high",
                "time": "2026-02-22 13:30:00",
            },
            {
                "country": "US",
                "event": "Initial Jobless Claims",
                "impact": "low",
                "time": "2026-02-21 13:30:00",
            },
            {
                "country": "EU",
                "event": "ECB Rate Decision",
                "impact": "high",
                "time": "2026-02-21 12:45:00",
            },
        ]
    }


@patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
@patch("cryptobot.data.economic_calendar.get_cache", return_value=None)
@patch("cryptobot.data.economic_calendar.set_cache")
@patch("cryptobot.data.economic_calendar.httpx.get")
def test_get_upcoming_events_normal(mock_get, mock_set_cache, mock_get_cache):
    """测试正常响应: 仅保留 US + high impact"""
    resp = MagicMock()
    resp.json.return_value = _mock_finnhub_response()
    mock_get.return_value = resp

    result = get_upcoming_events()

    assert result["has_high_impact"] is True
    assert result["event_count"] == 2  # FOMC + CPI, 不含 low impact 和 EU
    assert all(e["country"] == "US" for e in result["events"])
    assert all(e["impact"] == "high" for e in result["events"])
    mock_set_cache.assert_called_once()


@patch.dict("os.environ", {"FINNHUB_API_KEY": ""})
def test_no_api_key_returns_empty():
    """无 API key 时返回空结果"""
    result = get_upcoming_events()
    assert result == _empty_result()


@patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
@patch("cryptobot.data.economic_calendar.get_cache")
def test_cache_hit(mock_get_cache):
    """缓存命中"""
    cached = {"events": [], "has_high_impact": False, "event_count": 0, "_cached_at": 999}
    mock_get_cache.return_value = cached
    result = get_upcoming_events()
    assert result == cached


@patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
@patch("cryptobot.data.economic_calendar.get_cache", return_value=None)
@patch("cryptobot.data.economic_calendar.httpx.get", side_effect=Exception("network error"))
def test_api_error_returns_empty(mock_get, mock_get_cache):
    """API 错误返回空结果"""
    result = get_upcoming_events()
    assert result == _empty_result()


@patch.dict("os.environ", {"FINNHUB_API_KEY": "test_key"})
@patch("cryptobot.data.economic_calendar.get_cache", return_value=None)
@patch("cryptobot.data.economic_calendar.set_cache")
@patch("cryptobot.data.economic_calendar.httpx.get")
def test_no_high_impact_events(mock_get, mock_set_cache, mock_get_cache):
    """无高影响事件"""
    resp = MagicMock()
    resp.json.return_value = {
        "economicCalendar": [
            {"country": "US", "event": "Redbook", "impact": "low", "time": "2026-02-21 14:00:00"},
        ]
    }
    mock_get.return_value = resp

    result = get_upcoming_events()
    assert result["has_high_impact"] is False
    assert result["event_count"] == 0
    assert result["events"] == []


def test_empty_result_structure():
    result = _empty_result()
    assert result["events"] == []
    assert result["has_high_impact"] is False
    assert result["next_high_impact"] is None
    assert result["event_count"] == 0
