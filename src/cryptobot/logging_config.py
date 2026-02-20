"""结构化日志配置"""

import json
import logging
import logging.handlers
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """JSON 格式日志"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        # 额外字段
        for key in ("symbol", "node", "duration"):
            if hasattr(record, key):
                log_data[key] = getattr(record, key)
        return json.dumps(log_data, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: str | None = None,
) -> None:
    """配置全局日志

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        json_format: 是否使用 JSON 格式 (False 则用标准文本格式)
        log_file: 可选的日志文件路径 (启用 RotatingFileHandler)
    """
    root = logging.getLogger()

    # 清理已有 handler，避免重复
    root.handlers.clear()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 选择 formatter
    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    # 控制台 handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件 handler (可选)
    if log_file:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # 降低噪音日志
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
