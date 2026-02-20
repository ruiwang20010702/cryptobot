FROM python:3.12-slim AS base

# TA-Lib C 库
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential wget curl && \
    wget -q http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz && \
    tar xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib && ./configure --prefix=/usr && make && make install && \
    cd .. && rm -rf ta-lib ta-lib-0.4.0-src.tar.gz && \
    apt-get purge -y build-essential wget && apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
COPY freqtrade_strategies/ freqtrade_strategies/

# 安装 uv + 依赖
RUN pip install uv && uv sync --no-dev

ENV PATH="/app/.venv/bin:$PATH"
