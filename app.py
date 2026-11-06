# -*- coding: utf-8 -*-
"""
Run_assistant Flask 主应用（Iter 2）

主页：统一提问 → 以子进程启动三个 Streamlit 引擎（8501/8502/8503）
      → 页面用隐藏 iframe 建立 Streamlit 会话，引擎在自己的进程里跑出报告
      → 引擎写运行态 / 论坛发言，Flask 通过 Socket.IO 实时推送论坛消息
      → 三个引擎全部完成后，HOST（总教练）汇总发言

训练数据页：/training/ （数据层只有本地 Excel 导入）

启动：
  conda activate synapse_run
  python app.py
"""

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

import requests
from flask import Flask, render_template, jsonify, request

import config
from utils.engine_runtime import read_status, write_status
from ForumEngine.monitor import ensure_forum, read_posts, read_from_offset
from ForumEngine.llm_host import generate_host_speech

# ---------- Flask + Socket.IO ----------
app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)
app.secret_key = "run_assistant_iter2"

# 训练数据管理蓝图（Excel 导入 + 增删改查）
from routes.training_data import training_data_bp
app.register_blueprint(training_data_bp)

try:
    from flask_socketio import SocketIO
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
except Exception:  # 没装 flask-socketio 时降级为纯 HTTP 轮询
    socketio = None


# ---------- 引擎进程管理 ----------
ENGINES = {
    "insight": {"name": "InsightEngine · 运动科学家",
                "script": "SingleEngineApp/insight_app.py",
                "port": config.STREAMLIT_INSIGHT},
    "query": {"name": "QueryEngine · 理论专家",
              "script": "SingleEngineApp/query_app.py",
              "port": config.STREAMLIT_QUERY},
    "media": {"name": "MediaEngine · 实用情报专家",
              "script": "SingleEngineApp/media_app.py",
              "port": config.STREAMLIT_MEDIA},
}

_processes = {}          # key -> Popen
_process_logs = {}       # key -> 文件句柄
_proc_lock = threading.Lock()


class EngineProcessManager:
    """用子进程拉起 / 终止三个 Streamlit 引擎"""

    @staticmethod
    def is_alive(key: str) -> bool:
        p = _processes.get(key)
        return bool(p) and p.poll() is None

    @classmethod
    def stop_all(cls):
        with _proc_lock:
            for key, p in list(_processes.items()):
                if p and p.poll() is None:
                    try:
                        p.terminate()
                        p.wait(timeout=5)
                    except Exception:
                        try:
                            p.kill()
                        except Exception:
                            pass
                _processes.pop(key, None)
                fh = _process_logs.pop(key, None)
                if fh:
                    try:
                        fh.close()
                    except Exception:
                        pass
        # 给端口一点释放时间
        time.sleep(1.0)

    @classmethod
    def start_all(cls, query: str, task_id: str):
        cls.stop_all()
        (_ROOT / config.LOGS_DIR).mkdir(parents=True, exist_ok=True)

        for key, info in ENGINES.items():
            write_status(key, task_id, status="queued", progress=0,
                         stage="等待引擎启动", query=query, report_path="")

            env = os.environ.copy()
            env.update({
                "RUN_QUERY": query,
                "RUN_TASK_ID": task_id,
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            })

            log_path = _ROOT / config.LOGS_DIR / f"{key}.log"
            log_fh = open(log_path, "a", encoding="utf-8")

            cmd = [
                sys.executable, "-m", "streamlit", "run",
                str(_ROOT / info["script"]),
                "--server.port", str(info["port"]),
                "--server.address", "127.0.0.1",
                "--server.headless", "true",
                "--browser.gatherUsageStats", "false",
                "--logger.level", "info",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            p = subprocess.Popen(
                cmd, cwd=str(_ROOT), env=env,
                stdout=log_fh, stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            with _proc_lock:
                _processes[key] = p
                _process_logs[key] = log_fh
            print(f"[Flask] 已启动 {key} 引擎进程 pid={p.pid} port={info['port']}")

        # 等端口就绪（最多 ENGINE_STARTUP_TIMEOUT 秒），保证前端 iframe 能连上
        deadline = time.time() + config.ENGINE_STARTUP_TIMEOUT
        pending = set(ENGINES)
        probe = requests.Session()
        probe.trust_env = False          # 忽略系统代理，访问本机端口
        while pending and time.time() < deadline:
            for key in list(pending):
                port = ENGINES[key]["port"]
                try:
                    r = probe.get(f"http://127.0.0.1:{port}/", timeout=1)
                    if r.status_code < 500:
                        pending.discard(key)
                except requests.RequestException:
                    pass
            if pending:
                time.sleep(0.5)

        ready = {k: k not in pending for k in ENGINES}
        return ready


# ---------- 任务状态 ----------
_tasks = {}
_tasks_lock = threading.Lock()
_active_task_id = None   # 当前正在运行的任务（论坛历史只对当前任务可见）


def _ensure_dirs():
    (_ROOT / config.LOGS_DIR).mkdir(exist_ok=True)
    (_ROOT / config.RUNTIME_DIR).mkdir(parents=True, exist_ok=True)
    (_ROOT / config.EXCEL_DATA_DIR).mkdir(exist_ok=True)
    (_ROOT / config.INSIGHT_REPORTS_DIR).mkdir(exist_ok=True)
    (_ROOT / config.QUERY_REPORTS_DIR).mkdir(exist_ok=True)
    (_ROOT / config.MEDIA_REPORTS_DIR).mkdir(exist_ok=True)
    ensure_forum()


def _snapshot(task_id: str) -> dict:
    engines = {}
    terminal_count = 0
    for key in ENGINES:
        st = read_status(key, task_id)
        status = st.get("status") or "pending"
        engines[key] = {
            "name": ENGINES[key]["name"],
            "port": ENGINES[key]["port"],
            "status": status,
            "progress": st.get("progress", 0),
            "stage": st.get("stage", ""),
            "report_path": st.get("report_path", ""),
            "error": st.get("error", ""),
            "process_alive": EngineProcessManager.is_alive(key),
        }
        if status in ("done", "error"):
            terminal_count += 1

    meta = _tasks.get(task_id, {})
    if terminal_count == len(ENGINES):
        overall = "done"
    elif terminal_count > 0:
        overall = "running"
    else:
        overall = meta.get("status", "running")

    return {
        "task_id": task_id,
        "query": meta.get("query", ""),
        "status": overall,
        "engines": engines,
        "host_speech": meta.get("host_speech", ""),
    }


def _orchestrate(task_id: str, query: str):
    """等三个引擎结束 → 触发 HOST 汇总发言"""
    global _active_task_id
    deadline = time.time() + config.ENGINE_TIMEOUT
    while time.time() < deadline:
        snap = _snapshot(task_id)
        all_terminal = all(v["status"] in ("done", "error") for v in snap["engines"].values())
        if all_terminal:
            break

        # 进程挂了但状态没落盘 → 标记失败，避免永远等待
        for key, info in snap["engines"].items():
            if not info["process_alive"] and info["status"] not in ("done", "error"):
                write_status(key, task_id, status="error",
                             stage="引擎进程已退出",
                             error=f"引擎进程未正常结束，请查看 logs/{key}.log")
        time.sleep(config.STATUS_POLL_INTERVAL)

    snap = _snapshot(task_id)
    for key, info in snap["engines"].items():
        if info["status"] not in ("done", "error"):
            write_status(key, task_id, status="error", stage="任务超时",
                         error=f"超过 {config.ENGINE_TIMEOUT}s 未完成")

    speech = ""
    try:
        result = generate_host_speech(read_posts(), query=query, task_id=task_id)
        speech = result.get("content", "")
    except Exception as e:
        print(f"[Flask] HOST 发言失败: {e}")

    with _tasks_lock:
        _tasks.setdefault(task_id, {})
        _tasks[task_id].update({"status": "done", "host_speech": speech})
    _active_task_id = None

    if socketio:
        socketio.emit("task_done", {"task_id": task_id, "host_speech": speech})


# ---------- 论坛实时推送 ----------
def _forum_tail_loop():
    offset = ensure_forum().stat().st_size   # 只推新消息
    while True:
        try:
            posts, offset = read_from_offset(offset)
            for post in posts:
                if socketio:
                    socketio.emit("forum_message", post)
        except Exception as e:
            print(f"[Flask] 论坛监听异常: {e}")
        time.sleep(config.FORUM_TAIL_INTERVAL)


def _start_background():
    _ensure_dirs()
    threading.Thread(target=_forum_tail_loop, daemon=True).start()


if socketio:
    @socketio.on("connect")
    def _on_connect():
        # 只回放当前正在运行的任务的发言；没有活跃任务时论坛保持空白
        posts = []
        if _active_task_id:
            posts = [p for p in read_posts()
                     if p.get("task_id") == _active_task_id][-20:]
        socketio.emit("forum_history", {"posts": posts})


# ---------- 路由 ----------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/training/")
def training_page():
    return render_template("training_data.html")


@app.route("/api/ask", methods=["POST"])
def api_ask():
    data = request.get_json(force=True) if request.is_json else request.form
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "请输入问题"}), 400

    global _active_task_id
    task_id = uuid.uuid4().hex[:10]
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "running",
            "query": query,
            "created_at": datetime.now().isoformat(),
            "host_speech": "",
        }
    _active_task_id = task_id

    # 新一轮提问前清空论坛日志，历史发言不保留
    try:
        ensure_forum().write_text("", encoding="utf-8")
    except Exception as e:
        print(f"[Flask] 清空论坛日志失败: {e}")

    ready = EngineProcessManager.start_all(query, task_id)
    if not any(ready.values()):
        return jsonify({"error": "三个引擎进程都没能启动，请检查 logs/ 下的日志"}), 500

    threading.Thread(target=_orchestrate, args=(task_id, query), daemon=True).start()

    return jsonify({
        "task_id": task_id,
        "query": query,
        "engines": {
            key: {"port": info["port"], "ready": ready.get(key, False),
                  "name": info["name"]}
            for key, info in ENGINES.items()
        },
    })


@app.route("/api/task/<task_id>")
def api_task_status(task_id):
    meta = _tasks.get(task_id)
    if not meta:
        # 可能是 Flask 重启前的旧任务，仍尝试读运行态
        if not any(read_status(k, task_id) for k in ENGINES):
            return jsonify({"error": "task not found"}), 404
    return jsonify(_snapshot(task_id))


@app.route("/api/report/<engine_key>")
def api_report(engine_key):
    if engine_key not in ENGINES:
        return jsonify({"error": "unknown engine"}), 400
    task_id = request.args.get("task_id", "")
    if not task_id:
        return jsonify({"error": "缺少 task_id"}), 400

    st = read_status(engine_key, task_id)
    path = st.get("report_path")
    if not path or not Path(path).exists():
        return jsonify({"error": "报告尚未生成"}), 404

    p = Path(path)
    return jsonify({
        "engine": engine_key,
        "content": p.read_text(encoding="utf-8"),
        "path": str(p),
        "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
    })


@app.route("/api/forum")
def api_forum():
    limit = int(request.args.get("limit", 50))
    return jsonify({"posts": read_posts()[-limit:]})


@app.route("/api/engines")
def api_engines():
    return jsonify({
        key: {
            "name": info["name"], "port": info["port"],
            "alive": EngineProcessManager.is_alive(key),
            "url": f"http://localhost:{info['port']}",
        }
        for key, info in ENGINES.items()
    })


# ---------- 启动 ----------
_start_background()

def _mute_werkzeug_dev_warning():
    """屏蔽 “This is a development server...” 提示，保留请求日志"""

    class _Filter(logging.Filter):
        def filter(self, record):
            return "development server" not in record.getMessage()

    logging.getLogger("werkzeug").addFilter(_Filter())


if __name__ == "__main__":
    _mute_werkzeug_dev_warning()
    print("=" * 60)
    print(" Run_assistant  —  跑步训练多智能体分析系统（Iter 2）")
    print(f" 主页      : http://localhost:{config.FLASK_PORT}")
    print(f" 训练数据  : http://localhost:{config.FLASK_PORT}/training/")
    print(" 引擎端口  : 8501 / 8502 / 8503（由主页统一提问触发）")
    print("=" * 60)
    try:
        if socketio:
            socketio.run(app, host="0.0.0.0", port=config.FLASK_PORT,
                         debug=False, allow_unsafe_werkzeug=True)
        else:
            app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False)
    finally:
        EngineProcessManager.stop_all()
