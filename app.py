# -*- coding: utf-8 -*-
"""Run_assistant Flask 主应用（Iter 3）

主页：统一提问
  → Orchestrator 调度：第 1 轮三引擎并行 → HOST 仲裁 → 第 2 轮定向复跑
  → ReportEngine：选模板 + 生成最终 HTML 报告
  → Flask 通过 Socket.IO 实时推送论坛消息 / 轮次 / 仲裁 / 最终报告

其他页面：
  /training/  训练数据管理（本地 Excel 导入 + 增删改查）
  /setup/     系统配置（LLM / 数据库 / 搜索 API / 调度参数 + 健康检查）

启动：
  conda activate synapse_run
  python app.py
"""

import logging
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

# 首次运行：没有 config.py 就从示例配置复制一份（含课程专用 API Key）
_cfg_file = _ROOT / "config.py"
_cfg_example = _ROOT / "config.example.py"
if not _cfg_file.exists() and _cfg_example.exists():
    import shutil
    shutil.copyfile(_cfg_example, _cfg_file)

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

import config
from utils.process_manager import ENGINES, EngineProcessManager
from utils.engine_runtime import read_status
from utils.persona import load_persona
from ForumEngine.monitor import ensure_forum, read_posts, read_from_offset
from orchestrator.scheduler import OrchestratorScheduler, read_orchestrator_state
from ReportEngine.agent import ReportAgent

# ---------- Flask + Socket.IO ----------
app = Flask(
    __name__,
    template_folder=str(_ROOT / "templates"),
    static_folder=str(_ROOT / "static"),
)
app.secret_key = "run_assistant_iter3"

from routes.auth import auth_bp, PUBLIC_ENDPOINTS
from routes.training_data import training_data_bp
from routes.setup import setup_bp
app.register_blueprint(auth_bp)
app.register_blueprint(training_data_bp)
app.register_blueprint(setup_bp)

# ---------- 数据库初始化（不阻断启动） ----------
# 未配置 MySQL 时也能启动：页面会被引导到 /setup/，保存配置后自动建库建表并生效
from utils import db_bootstrap

db_bootstrap.refresh()


# ---------- 登录守卫 ----------
# 免登录白名单：登录/注册页、登录态查询（前端导航用）、静态资源、引擎状态查询（供轮询）
_OPEN_PREFIXES = ('/auth/login', '/auth/register', '/auth/api/me', '/static')


@app.before_request
def _require_login():
    # 数据库未就绪：配置页与静态资源放行，其余请求一律引导到配置页
    if not db_bootstrap.is_ready():
        if request.path.startswith('/setup') or request.path.startswith('/static'):
            return None
        if request.path.startswith('/api/') or request.is_json:
            return jsonify({'success': False, 'need_setup': True,
                            'message': '数据库未连接，请先在配置页完成设置',
                            'error': db_bootstrap.last_error()}), 503
        return redirect(url_for('setup.page'))

    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if any(request.path.startswith(p) for p in _OPEN_PREFIXES):
        return None
    if 'user_id' in session:
        return None
    # API 请求返回 401 JSON；页面请求重定向到登录页
    if request.path.startswith('/api/') or request.is_json:
        return jsonify({'success': False, 'message': '未登录'}), 401
    return redirect(url_for('auth.login', next=request.path))


try:
    from flask_socketio import SocketIO
    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*")
except Exception:      # 没装 flask-socketio 时降级为纯 HTTP 轮询
    socketio = None


# ---------- 任务状态 ----------
_tasks = {}
_tasks_lock = threading.Lock()
_schedulers = {}
_active_task_id = None


def _emit(event: str, payload: dict):
    if socketio:
        try:
            socketio.emit(event, payload)
        except Exception as e:
            print(f"[Flask] 推送 {event} 失败: {e}")


def _ensure_dirs():
    for d in (config.LOGS_DIR, config.RUNTIME_DIR, config.EXCEL_DATA_DIR,
              config.ORCH_STATE_DIR, config.FINAL_REPORTS_DIR,
              config.INSIGHT_REPORTS_DIR, config.QUERY_REPORTS_DIR,
              config.MEDIA_REPORTS_DIR):
        (_ROOT / d).mkdir(parents=True, exist_ok=True)
    ensure_forum()


def _collect_reports(task_id: str) -> dict:
    reports = {}
    for key, info in ENGINES.items():
        st = read_status(key, task_id)
        path = st.get("report_path")
        if not path or not Path(path).exists():
            continue
        reports[key] = {
            "name": info["name"],
            "path": path,
            "round": st.get("round", 1),
            "content": Path(path).read_text(encoding="utf-8"),
        }
    return reports


def _snapshot(task_id: str) -> dict:
    sched = _schedulers.get(task_id)
    orch = sched.snapshot() if sched else read_orchestrator_state(task_id)

    engines = {}
    for key, info in ENGINES.items():
        st = read_status(key, task_id)
        status = st.get("status") or "pending"
        engines[key] = {
            "name": info["name"],
            "port": info["port"],
            "status": status,
            "progress": st.get("progress", 0),
            "stage": st.get("stage", ""),
            "round": st.get("round", 1),
            "report_path": st.get("report_path", ""),
            "error": st.get("error", ""),
            "process_alive": EngineProcessManager.is_alive(key),
        }

    meta = _tasks.get(task_id, {})
    rounds = []
    for r in orch.get("rounds", []):
        rounds.append({
            "round": r.get("round"),
            "targets": r.get("targets", []),
            "engines": {k: v.get("status") for k, v in (r.get("engines") or {}).items()},
            "need_rerun": (r.get("arbitration") or {}).get("need_rerun", False),
        })

    return {
        "task_id": task_id,
        "query": meta.get("query") or orch.get("query", ""),
        "status": meta.get("status") or orch.get("status", "running"),
        "phase": orch.get("phase", ""),
        "round": orch.get("round", 1),
        "max_rounds": orch.get("max_rounds", config.ORCH_MAX_ROUNDS),
        "llm_calls_estimate": orch.get("llm_calls_estimate", 0),
        "stop_reason": orch.get("stop_reason", "") or meta.get("stop_reason", ""),
        "arbitration": orch.get("arbitration", {}),
        "arbitration_round": orch.get("arbitration_round", 0),
        "rounds": rounds,
        "engines": engines,
        "final_report": meta.get("final_report", ""),
        "template_used": meta.get("template_used", ""),
        "error": meta.get("error", "") or orch.get("error", ""),
    }


# ---------- 主流程（后台线程） ----------
def _pipeline(task_id: str, query: str, user_id=None):
    """调度 → 最终报告（user_id 用于生成用户画像并注入引擎，Issue #5）"""
    global _active_task_id
    sched = _schedulers[task_id]

    try:
        persona = load_persona(user_id)
        sched.start(query, task_id, persona=persona).join()
        state = sched.snapshot()

        reports = _collect_reports(task_id)
        posts = [p for p in read_posts() if p.get("task_id") == task_id]
        arbitration = state.get("arbitration") or {}

        meta = {}
        if reports:
            with _tasks_lock:
                _tasks[task_id]["status"] = "reporting"
            _emit("reporting", {"task_id": task_id})
            try:
                meta = ReportAgent().generate(
                    query, task_id, reports, posts, arbitration,
                    persona=persona)
            except Exception as e:
                print(f"[Flask] 最终报告生成失败: {e}")
                meta = {"error": f"{type(e).__name__}: {e}"}
        else:
            meta = {"error": "三个引擎都没有产出报告，无法生成最终报告"}

        with _tasks_lock:
            _tasks.setdefault(task_id, {})
            _tasks[task_id].update({
                "status": state.get("status", "done"),
                "stop_reason": state.get("stop_reason", ""),
                "final_report": meta.get("report_path", ""),
                "template_used": meta.get("template_used", ""),
                "error": meta.get("error", ""),
            })
        _active_task_id = None

        _emit("task_done", {
            "task_id": task_id,
            "status": state.get("status", "done"),
            "stop_reason": state.get("stop_reason", ""),
            "final_report": meta.get("report_path", ""),
            "template_used": meta.get("template_used", ""),
            "error": meta.get("error", ""),
        })
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[Flask] 流程异常: {err}")
        with _tasks_lock:
            _tasks.setdefault(task_id, {})
            _tasks[task_id].update({"status": "error", "error": err})
        _active_task_id = None
        _emit("task_done", {"task_id": task_id, "status": "error", "error": err})


# ---------- 论坛实时推送 ----------
def _forum_tail_loop():
    offset = ensure_forum().stat().st_size      # 只推新消息
    while True:
        try:
            posts, offset = read_from_offset(offset)
            for post in posts:
                _emit("forum_message", post)
        except Exception as e:
            print(f"[Flask] 论坛监听异常: {e}")
        time.sleep(config.FORUM_TAIL_INTERVAL)


def _start_background():
    _ensure_dirs()
    threading.Thread(target=_forum_tail_loop, daemon=True).start()


if socketio:
    @socketio.on("connect")
    def _on_connect():
        posts = []
        if _active_task_id:
            posts = [p for p in read_posts()
                     if p.get("task_id") == _active_task_id][-30:]
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
    user_id = session.get("user_id")
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "running",
            "query": query,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
        }
    _active_task_id = task_id

    # 新一轮提问前清空论坛日志，历史发言不保留
    try:
        ensure_forum().write_text("", encoding="utf-8")
    except Exception as e:
        print(f"[Flask] 清空论坛日志失败: {e}")

    sched = OrchestratorScheduler(emit=_emit)
    _schedulers[task_id] = sched

    threading.Thread(target=_pipeline, args=(task_id, query, user_id), daemon=True).start()

    return jsonify({
        "task_id": task_id,
        "query": query,
        "max_rounds": config.ORCH_MAX_ROUNDS,
        "engines": {
            key: {"port": info["port"], "name": info["name"]}
            for key, info in ENGINES.items()
        },
    })


@app.route("/api/task/<task_id>")
def api_task_status(task_id):
    if task_id not in _schedulers and not read_orchestrator_state(task_id):
        return jsonify({"error": "task not found"}), 404
    return jsonify(_snapshot(task_id))


@app.route("/api/task/<task_id>/stop", methods=["POST"])
def api_task_stop(task_id):
    sched = _schedulers.get(task_id)
    if not sched:
        return jsonify({"error": "task not found"}), 404
    sched.stop("manual_stop")
    return jsonify({"success": True, "message": "已请求停止"})


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
        "round": st.get("round", 1),
        "mtime": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
    })


@app.route("/report/view/<task_id>")
def final_report_view(task_id):
    """最终 HTML 报告（用于主页 iframe 与下载）"""
    p = _ROOT / config.FINAL_REPORTS_DIR / f"{task_id}.html"
    if not p.exists():
        return Response("<h3 style='font-family:sans-serif'>最终报告尚未生成</h3>",
                        mimetype="text/html")
    return Response(p.read_text(encoding="utf-8"), mimetype="text/html")


@app.route("/report/download/<task_id>")
def final_report_download(task_id):
    p = _ROOT / config.FINAL_REPORTS_DIR / f"{task_id}.html"
    if not p.exists():
        return jsonify({"error": "最终报告尚未生成"}), 404
    return Response(
        p.read_text(encoding="utf-8"),
        mimetype="text/html",
        headers={"Content-Disposition":
                 f'attachment; filename="run_report_{task_id}.html"'},
    )


@app.route("/api/report/final/<task_id>")
def api_final_report(task_id):
    p = _ROOT / config.FINAL_REPORTS_DIR / f"{task_id}.html"
    if not p.exists():
        return jsonify({"error": "最终报告尚未生成"}), 404
    meta_path = _ROOT / config.FINAL_REPORTS_DIR / f"{task_id}.json"
    meta = {}
    if meta_path.exists():
        import json
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    return jsonify({
        "task_id": task_id,
        "path": str(p),
        "url": f"/report/view/{task_id}",
        **meta,
    })


@app.route("/api/forum")
def api_forum():
    limit = int(request.args.get("limit", 50))
    task_id = request.args.get("task_id")
    posts = read_posts()
    if task_id:
        posts = [p for p in posts if p.get("task_id") == task_id]
    return jsonify({"posts": posts[-limit:]})


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
    print("=" * 64)
    print(" Run_assistant  —  跑步训练多智能体分析系统（Iter 3）")
    print(f" 主页      : http://localhost:{config.FLASK_PORT}")
    print(f" 训练数据  : http://localhost:{config.FLASK_PORT}/training/")
    print(f" 系统配置  : http://localhost:{config.FLASK_PORT}/setup/")
    print(f" 调度参数  : {config.ORCH_MAX_ROUNDS} 轮 / "
          f"{config.ORCH_MAX_LLM_CALLS} 次 LLM / {config.ORCH_TIMEOUT}s")
    print("=" * 64)
    try:
        if socketio:
            socketio.run(app, host="0.0.0.0", port=config.FLASK_PORT,
                         debug=False, allow_unsafe_werkzeug=True)
        else:
            app.run(host="0.0.0.0", port=config.FLASK_PORT, debug=False)
    finally:
        EngineProcessManager.stop_all()
