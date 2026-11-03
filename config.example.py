# -*- coding: utf-8 -*-
"""
Run_assistant 配置文件（Iter 1 原型）
"""

# ============================== 数据库配置 ==============================
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = "xsz050108"
DB_NAME = "run"
DB_CHARSET = "utf8mb4"
TRAINING_DATA_SOURCE = "keep"   # 目前 Iter 1 只做 keep，garmin 留到后面

# ============================== LLM 配置 ==============================
# DashScope 兼容接口
LLM_API_KEY = ""
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

DEFAULT_MODEL_NAME = "qwen-plus-latest"   # 三个子引擎通用
REPORT_MODEL_NAME  = "qwen-max-3"         # 报告生成专用（Iter 2 用）

# ============================== 搜索 API ==============================
TAVILY_API_KEY    = ""
BOCHA_WEB_SEARCH_API_KEY = ""

# ============================== 端口分配 ==============================
FLASK_PORT        = 5000
STREAMLIT_INSIGHT = 8501
STREAMLIT_QUERY   = 8502
STREAMLIT_MEDIA   = 8503

# ============================== 裁剪后的引擎参数 ==============================
# 每个引擎的段落数（Iter 1 砍到 2）
PARAGRAPHS_PER_ENGINE = 2
# 反思轮数（Iter 1 砍掉，直接 0）
MAX_REFLECTIONS = 0

# ============================== 输出目录 ==============================
REPORTS_DIR = "reports"
LOGS_DIR    = "logs"
