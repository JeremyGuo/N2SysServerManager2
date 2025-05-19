import os
import logging
from datetime import datetime

# 1. 日志目录，放在项目根的 logs 文件夹下
_root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_log_dir = os.path.join(_root_dir, 'logs')
if not os.path.exists(_log_dir):
    os.makedirs(_log_dir)

# 2. 日志文件按日期命名
_log_file = os.path.join(_log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log")

# 3. 获取并配置 logger
logger = logging.getLogger('n2sys')
logger.setLevel(logging.INFO)

# 避免重复添加 Handler
if not logger.handlers:
    # 文件 Handler
    fh = logging.FileHandler(_log_file, encoding='utf-8')
    fh.setLevel(logging.INFO)
    # 控制台 Handler
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)

    # 统一格式
    fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    formatter = logging.Formatter(fmt)
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)