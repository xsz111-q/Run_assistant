# -*- coding: utf-8 -*-
"""系统配置页（Iter 3）

- 在线查看 / 修改 config.py 中的关键配置（LLM / 数据库 / 搜索 API / 调度参数）
- 保存后写回 config.py 并热重载 config 模块
- 健康检查：LLM、MySQL、Tavily、博查 四项连通性
"""

import importlib
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from flask import Blueprint, jsonify, render_template, request

setup_bp = Blueprint("setup", __name__, url_prefix="/setup")

# 分组：字段 -> 是否数值型
FIELDS = {
    "llm": {
        "LLM_API_KEY": False,
        "LLM_BASE_URL": False,
        "DEFAULT_MODEL_NAME": False,
        "REPORT_MODEL_NAME": False,
    },
    "db": {
        "DB_HOST": False,
        "DB_PORT": True,
        "DB_USER": False,
        "DB_PASSWORD": False,
        "DB_NAME": False,
        "DB_CHARSET": False,
    },
    "search": {
        "TAVILY_API_KEY": False,
        "BOCHA_WEB_SEARCH_API_KEY": False,
    },
    "scheduler": {
        "ORCH_MAX_ROUNDS": True,
        "ORCH_MAX_LLM_CALLS": True,
        "ORCH_TIMEOUT": True,
        "ORCH_ROUND_TIMEOUT": True,
        "PARAGRAPHS_PER_ENGINE": True,
    },
}

ALL_FIELDS = {k: v for group in FIELDS.values() for k, v in group.items()}


def _current_values() -> dict:
    return {k: getattr(config, k, "") for k in ALL_FIELDS}


def _cast(field: str, value):
    if ALL_FIELDS.get(field):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{field} 需要是数字")
    return str(value)


def _write_config(updates: dict):
    path = _ROOT / "config.py"
    text = path.read_text(encoding="utf-8")

    for key, value in updates.items():
        if key not in ALL_FIELDS:
            continue
        line = f"{key} = {repr(value)}"
        pattern = rf"^{key}\s*=\s*.*$"
        if re.search(pattern, text, flags=re.M):
            text = re.sub(pattern, line, text, count=1, flags=re.M)
        else:
            text = text.rstrip("\n") + "\n" + line + "\n"

    path.write_text(text, encoding="utf-8")
    importlib.reload(config)


@setup_bp.route("/")
def page():
    return render_template("setup.html")


@setup_bp.route("/api/config", methods=["GET"])
def get_config():
    return jsonify({"success": True, "groups": FIELDS, "values": _current_values()})


@setup_bp.route("/api/config", methods=["POST"])
def save_config():
    data = request.get_json(force=True) or {}
    updates = {}
    try:
        for key, value in data.items():
            if key in ALL_FIELDS:
                updates[key] = _cast(key, value)
        _write_config(updates)
    except Exception as e:
        return jsonify({"success": False, "message": f"保存失败：{e}"}), 400
    return jsonify({
        "success": True,
        "message": "已保存并热重载。端口/数据库连接等改动需重启 Flask 后生效。",
        "values": _current_values(),
    })


# ---------- 健康检查 ----------
def _timed(fn):
    start = time.time()
    try:
        detail = fn()
        return {"ok": True, "message": detail, "elapsed_ms": int((time.time() - start) * 1000)}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {e}",
                "elapsed_ms": int((time.time() - start) * 1000)}


def _check_llm():
    from openai import OpenAI
    client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
    resp = client.chat.completions.create(
        model=config.DEFAULT_MODEL_NAME,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=4, timeout=20,
    )
    return f"{config.DEFAULT_MODEL_NAME} 响应正常（{resp.choices[0].message.content.strip()[:20]}）"


def _check_mysql():
    from sqlalchemy import create_engine, text
    engine = create_engine(
        f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}"
        f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
        f"?charset={config.DB_CHARSET}", pool_pre_ping=True,
    )
    with engine.connect() as conn:
        n = conn.execute(text("SELECT 1")).scalar()
    engine.dispose()
    return f"{config.DB_NAME} 库连接正常（SELECT 1 → {n}）"


def _check_tavily():
    from tavily import TavilyClient
    client = TavilyClient(api_key=config.TAVILY_API_KEY)
    res = client.search(query="running training", max_results=1)
    n = len(res.get("results", []))
    return f"Tavily 返回 {n} 条结果"


def _check_bocha():
    import requests
    r = requests.post(
        "https://api.bocha.cn/v1/ai-search",
        headers={"Authorization": f"Bearer {config.BOCHA_WEB_SEARCH_API_KEY}",
                 "Content-Type": "application/json"},
        json={"query": "跑步训练", "count": 1},
        timeout=20,
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    return "博查搜索接口返回 200"


@setup_bp.route("/api/health")
def health():
    return jsonify({
        "success": True,
        "checks": {
            "llm": _timed(_check_llm),
            "mysql": _timed(_check_mysql),
            "tavily": _timed(_check_tavily),
            "bocha": _timed(_check_bocha),
        },
    })
