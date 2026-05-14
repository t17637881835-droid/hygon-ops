"""结构化日志配置"""
import os
import sys
from pathlib import Path
from loguru import logger

# 移除默认 handler
logger.remove()

# 添加控制台输出（带颜色）
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
)

# 添加文件输出（按天轮转）
# LOG_DIR 优先读取环境变量（Docker 挂载路径），回退到相对路径
_log_dir = Path(os.environ.get("LOG_DIR", os.path.join(os.path.dirname(__file__), "..", "logs")))
_log_dir.mkdir(parents=True, exist_ok=True)
logger.add(
    str(_log_dir / "app_{time:YYYY-MM-DD}.log"),
    rotation="00:00",
    retention="30 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="DEBUG"
)


def get_logger(name: str = None):
    """获取带模块名的 logger"""
    if name:
        return logger.bind(name=name)
    return logger
