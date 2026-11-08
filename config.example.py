# -*- coding: utf-8 -*-
"""
Run_assistant 配置文件示例（Iter 3）
复制为 config.py 后填入自己的 Key
"""

# ============================== 数据库配置 ==============================
DB_HOST = "localhost"
DB_PORT = 3306
DB_USER = "root"
DB_PASSWORD = ""
DB_NAME = "run"
DB_CHARSET = "utf8mb4"

# ============================== 数据层（仅本地 Excel） ==============================
TRAINING_TABLE = "training_records_excel"
EXCEL_DATA_DIR = "data"
EXCEL_ALLOWED_EXT = ".xlsx"

# ============================== LLM 配置 ==============================
LLM_API_KEY = ""
LLM_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

DEFAULT_MODEL_NAME = "qwen-plus-latest"
REPORT_MODEL_NAME  = "qwen-max"

# ============================== 搜索 API ==============================
TAVILY_API_KEY    = ""
BOCHA_WEB_SEARCH_API_KEY = ""

# ============================== 端口分配 ==============================
FLASK_PORT        = 5000
STREAMLIT_INSIGHT = 8501
STREAMLIT_QUERY   = 8502
STREAMLIT_MEDIA   = 8503

# ============================== 裁剪后的引擎参数 ==============================
PARAGRAPHS_PER_ENGINE = 2
MAX_REFLECTIONS = 0

# ============================== 输出目录 ==============================
INSIGHT_REPORTS_DIR = "insight_engine_streamlit_reports"
QUERY_REPORTS_DIR   = "query_engine_streamlit_reports"
MEDIA_REPORTS_DIR   = "media_engine_streamlit_reports"

REPORTS_DIR = "reports"
LOGS_DIR    = "logs"

# ============================== 论坛 / 运行态 ==============================
FORUM_LOG = "logs/forum.log"
HOST_SPEECH_FILE = "logs/host_speech.md"
RUNTIME_DIR = "logs/runtime"

# ============================== 进程与超时 ==============================
ENGINE_STARTUP_TIMEOUT = 60
ENGINE_TIMEOUT = 900
STATUS_POLL_INTERVAL = 1.0
FORUM_TAIL_INTERVAL = 0.5

# ============================== 调度器（Iter 3，简化参数） ==============================
ORCH_MAX_ROUNDS = 2
ORCH_MAX_LLM_CALLS = 60
ORCH_TIMEOUT = 600
ORCH_ROUND_TIMEOUT = 300
ORCH_STATE_DIR = "logs/orchestrator"
ORCH_LLM_CALLS_PER_ENGINE = 6

# ============================== 最终报告（Iter 3） ==============================
FINAL_REPORTS_DIR = "final_reports"
REPORT_TEMPLATE_DIR = "ReportEngine/report_template"
REPORT_TEMPLATE_COUNT = 21
REPORT_MAX_CHARS_PER_ENGINE = 1800
REPORT_HTML_MAX_TOKENS = 6000
