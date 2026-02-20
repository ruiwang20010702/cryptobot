#!/usr/bin/env bash
# CryptoBot 部署前检查脚本
# 用法: bash deploy/preflight.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $1"; WARNINGS=$((WARNINGS+1)); }
fail() { echo -e "  ${RED}[FAIL]${NC} $1"; ERRORS=$((ERRORS+1)); }

echo "=== CryptoBot 部署前检查 ==="
echo ""

# 1. Python 版本
echo "--- Python ---"
PYVER=$(python3 --version 2>/dev/null | awk '{print $2}')
if [[ "$PYVER" == 3.12* ]]; then
    ok "Python $PYVER"
else
    fail "需要 Python 3.12.x, 当前: $PYVER"
fi

# 2. uv
if command -v uv &>/dev/null; then
    ok "uv $(uv --version 2>/dev/null | head -1)"
else
    fail "uv 未安装"
fi

# 3. 虚拟环境
if [ -d ".venv" ]; then
    ok "虚拟环境 .venv"
else
    fail "虚拟环境不存在，运行 uv sync"
fi

# 4. TA-Lib C 库
echo ""
echo "--- 依赖库 ---"
if python3 -c "import talib" 2>/dev/null; then
    ok "TA-Lib"
else
    fail "TA-Lib C 库未安装 (brew install ta-lib)"
fi

# 5. 环境变量
echo ""
echo "--- 环境变量 ---"
check_env() {
    local var=$1
    local required=$2
    if [ -n "${!var:-}" ]; then
        ok "$var (已设置)"
    elif [ "$required" = "required" ]; then
        fail "$var 未设置"
    else
        warn "$var 未设置 (可选)"
    fi
}

check_env BINANCE_API_KEY required
check_env BINANCE_API_SECRET required
check_env COINGLASS_API_KEY required
check_env CRYPTONEWS_API_KEY optional
check_env COINGECKO_DEMO_KEY optional
check_env TELEGRAM_BOT_TOKEN optional
check_env TELEGRAM_CHAT_ID optional

# 6. 配置文件
echo ""
echo "--- 配置文件 ---"
for f in config/settings.yaml config/pairs.yaml; do
    if [ -f "$f" ]; then
        ok "$f"
    else
        fail "$f 不存在"
    fi
done

# 7. 数据目录
echo ""
echo "--- 数据目录 ---"
for d in data/output/signals data/output/.cache data/output/journal logs; do
    if [ -d "$d" ]; then
        ok "$d"
    else
        mkdir -p "$d"
        ok "$d (已创建)"
    fi
done

# 8. Freqtrade 连接
echo ""
echo "--- Freqtrade ---"
FT_URL="http://127.0.0.1:8080"
if curl -s --max-time 3 "$FT_URL/api/v1/ping" >/dev/null 2>&1; then
    ok "Freqtrade API ($FT_URL)"
else
    warn "Freqtrade API 不可达 ($FT_URL)"
fi

# 9. 测试
echo ""
echo "--- 测试 ---"
if uv run pytest tests/ -q --tb=no 2>/dev/null; then
    ok "所有测试通过"
else
    fail "测试有失败"
fi

# 10. Lint
echo ""
echo "--- Lint ---"
if uv run ruff check src/ freqtrade_strategies/ --quiet 2>/dev/null; then
    ok "Lint 全通过"
else
    warn "Lint 有问题"
fi

# 结果
echo ""
echo "=========================="
if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}检查通过${NC} ($WARNINGS 警告)"
    echo ""
    echo "启动命令:"
    echo "  uv run cryptobot daemon start          # 前台运行"
    echo "  uv run cryptobot daemon start --run-now # 立即分析一次"
    echo ""
    echo "macOS 开机自启:"
    echo "  cp deploy/com.cryptobot.daemon.plist ~/Library/LaunchAgents/"
    echo "  launchctl load ~/Library/LaunchAgents/com.cryptobot.daemon.plist"
else
    echo -e "${RED}$ERRORS 项失败, $WARNINGS 项警告${NC}"
    echo "请修复 FAIL 项后重新运行"
    exit 1
fi
