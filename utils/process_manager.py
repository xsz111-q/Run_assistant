# -*- coding: utf-8 -*-
"""引擎进程管理（Iter 3）

三个 Streamlit 引擎各自独立进程运行：
  insight : 8501   query : 8502   media : 8503

每一轮调度只让本轮目标引擎真正执行研究，其余引擎启动后直接展示上一轮报告
（RUN_SKIP=1），从而避免重复跑、也避免端口被占用。
"""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
import requests
from utils.engine_runtime import write_status

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
        time.sleep(1.0)     # 给端口释放一点时间

    @classmethod
    def start_round(cls, query: str, task_id: str, round_no: int = 1,
                    targets=None, guidance: str = ""):
        """启动一轮：targets 内的引擎执行研究，其余引擎仅展示报告"""
        cls.stop_all()
        targets = set(targets) if targets else set(ENGINES)
        (_ROOT / config.LOGS_DIR).mkdir(parents=True, exist_ok=True)

        for key, info in ENGINES.items():
            active = key in targets
            if active:
                write_status(key, task_id, status="queued", progress=0,
                             stage=f"第 {round_no} 轮等待引擎启动",
                             query=query, report_path="", round=round_no)

            env = os.environ.copy()
            env.update({
                "RUN_QUERY": query,
                "RUN_TASK_ID": task_id,
                "RUN_ROUND": str(round_no),
                "RUN_GUIDANCE": guidance or "",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
                "PYTHONUNBUFFERED": "1",
                "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false",
            })
            if not active:
                env["RUN_SKIP"] = "1"

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
            print(f"[ProcessManager] 第 {round_no} 轮 {key} "
                  f"{'执行' if active else '仅展示'} pid={p.pid} port={info['port']}")

        # 等端口就绪，保证前端 iframe 能连上
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

        return {k: k not in pending for k in ENGINES}

    @classmethod
    def start_all(cls, query: str, task_id: str):
        """兼容旧调用：第 1 轮全部引擎执行"""
        return cls.start_round(query, task_id, round_no=1)


def engine_url(key: str) -> str:
    return f"http://localhost:{ENGINES[key]['port']}"
