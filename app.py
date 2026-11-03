# -*- coding: utf-8 -*-
"""
Run_assistant Flask 主应用（Iter 1 原型）

主页：用户统一提问 → 后台顺序跑 Insight / Query / Media 三个引擎 → 实时返回三份报告
训练数据页：/training/ （搬自 original_project，支持增删改查 + CSV 导入）

启动：
  conda activate synapse_run
  python app.py
  浏览器打开 http://localhost:5000
"""

import os
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import config
from flask import Flask, render_template, jsonify, request, Response

# ---------- Flask 应用 ----------
app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)
app.secret_key = "run_assistant_iter1"

# ---------- 注册蓝图：训练数据管理 ----------
from routes.training_data import training_data_bp
app.register_blueprint(training_data_bp)

# ---------- 任务状态管理 ----------
# 因为每个引擎一次需要 30~60s，这里用后台线程顺序跑，前端轮询状态
_tasks: dict[str, dict] = {}  # task_id -> {status, query, engines:{insight,query,media}, error}
_tasks_lock = threading.Lock()


def _run_three_engines(task_id: str, query: str):
    """后台线程：顺序跑三个引擎（Iter 1 还没 Orchestrator 并行，先串行）"""
    with _tasks_lock:
        _tasks[task_id]["status"] = "running"

    # 三个引擎按 "Insight → Query → Media" 顺序跑
    engine_classes = [
        ("insight", "InsightEngine.agent", "SportsScientistAgent"),
        ("query",   "QueryEngine.agent",   "TheoryExpertAgent"),
        ("media",   "MediaEngine.agent",   "LogisticsIntelligenceAgent"),
    ]

    for key, mod_path, cls_name in engine_classes:
        # 检查是否被请求取消
        with _tasks_lock:
            if _tasks[task_id].get("cancelled"):
                _tasks[task_id]["status"] = "cancelled"
                return

        # 动态 import
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            agent = cls()
            with _tasks_lock:
                _tasks[task_id]["current_engine"] = key
                _tasks[task_id]["engines"][key] = {"status": "running", "start": datetime.now().isoformat()}
            out_path = agent.run(query)
            with _tasks_lock:
                _tasks[task_id]["engines"][key] = {"status": "done", "path": out_path, "end": datetime.now().isoformat()}
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            with _tasks_lock:
                _tasks[task_id]["engines"][key] = {"status": "error", "error": err_msg}
                _tasks[task_id]["error"] = err_msg
            # 继续跑下一个引擎，不阻断

    with _tasks_lock:
        _tasks[task_id]["status"] = "done"
        _tasks[task_id]["finished_at"] = datetime.now().isoformat()


# ---------- 路由 ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/training/")
def training_page():
    """训练数据管理页（已由 Blueprint 注册 /training 前缀，这里冗余保护）"""
    return render_template("training_data.html")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    """统一提问入口 —— 后台启动三引擎顺序分析"""
    data = request.get_json(force=True) if request.is_json else request.form
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "请输入问题"}), 400

    task_id = uuid.uuid4().hex[:10]
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "queued",
            "query": query,
            "created_at": datetime.now().isoformat(),
            "engines": {
                "insight": {"status": "pending"},
                "query":   {"status": "pending"},
                "media":   {"status": "pending"},
            },
        }

    threading.Thread(
        target=_run_three_engines, args=(task_id, query), daemon=True
    ).start()

    return jsonify({"task_id": task_id})


@app.route("/api/task/<task_id>")
def api_task_status(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return jsonify({"error": "task not found"}), 404
    # 为了避免报告内容一直被塞进内存，这里只返回状态+路径，内容单独拉
    brief = {
        "task_id": task_id,
        "status": task["status"],
        "query": task["query"],
        "engines": {},
    }
    for k, v in task["engines"].items():
        brief["engines"][k] = {"status": v.get("status", "pending")}
        if v.get("path"):
            brief["engines"][k]["path"] = v["path"]
        if v.get("error"):
            brief["engines"][k]["error"] = v["error"]
    return jsonify(brief)


@app.route("/api/report/<engine_key>")
def api_report(engine_key):
    """读取某个引擎最新生成的报告内容"""
    mapping = {
        "insight": "insight_report.md",
        "query":   "query_report.md",
        "media":   "media_report.md",
    }
    fname = mapping.get(engine_key)
    if not fname:
        return jsonify({"error": "unknown engine"}), 400
    p = _ROOT / config.REPORTS_DIR / fname
    if not p.exists():
        return jsonify({"error": "报告尚未生成"}), 404
    return jsonify({
        "engine": engine_key,
        "content": p.read_text(encoding="utf-8"),
        "path": str(p),
        "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
    })


# ---------- 启动钩子 ----------
@app.before_request
def _ensure_dirs():
    (_ROOT / config.REPORTS_DIR).mkdir(exist_ok=True)
    (_ROOT / config.LOGS_DIR).mkdir(exist_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print(" Run_assistant  — 跑步训练多智能体分析系统")
    print(f" 主页      : http://localhost:{config.FLASK_PORT}")
    print(f" 训练数据  : http://localhost:{config.FLASK_PORT}/training/")
    print("=" * 60)
    app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False)
