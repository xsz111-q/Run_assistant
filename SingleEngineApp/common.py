# -*- coding: utf-8 -*-
"""三个引擎 Streamlit 页面共用的渲染逻辑

两种运行方式：
  1) 主页触发：Flask 以子进程启动 streamlit，并注入环境变量
     RUN_QUERY / RUN_TASK_ID → 页面加载后在后台线程执行研究，
     页面轮询运行态文件，实时展示阶段性输出
  2) 手动调试：没有环境变量时，页面显示输入框，手动点击运行
"""

import os
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import streamlit as st

from utils.engine_runtime import read_status

POLL_INTERVAL = 1.5


def _query_param(name: str) -> str:
    """兼容不同 Streamlit 版本的 URL 参数读取"""
    try:
        v = st.query_params.get(name, "")
        if isinstance(v, list):
            return v[0] if v else ""
        return v or ""
    except Exception:
        q = st.experimental_get_query_params()
        v = q.get(name, [""])
        return v[0] if v else ""


def _resolve_inputs(default_query: str):
    """解析本轮任务参数（环境变量优先，其次 URL 参数）"""
    query = os.environ.get("RUN_QUERY") or _query_param("query")
    task_id = os.environ.get("RUN_TASK_ID") or _query_param("task_id") or None
    try:
        round_no = int(os.environ.get("RUN_ROUND") or _query_param("round") or 1)
    except ValueError:
        round_no = 1
    guidance = os.environ.get("RUN_GUIDANCE") or _query_param("guidance") or ""
    skip = os.environ.get("RUN_SKIP") == "1" or _query_param("skip") == "1"
    auto = bool(query) and (task_id is not None
                            or os.environ.get("RUN_AUTO") == "1"
                            or _query_param("auto_search") == "true")
    return query.strip(), task_id, auto, round_no, guidance, skip


def _show_report(report_path: str, label: str):
    p = Path(report_path)
    if not p.exists():
        st.warning(f"报告文件不存在：{report_path}")
        return
    content = p.read_text(encoding="utf-8")
    st.success(f"✅ {label}报告已生成：`{report_path}`")
    st.markdown("---")
    st.markdown(content, unsafe_allow_html=False)


def _claim_task(engine_key: str, task_id: str, round_no: int = 1) -> bool:
    """跨会话原子任务锁：同一 (engine, task, 轮次) 只允许一个会话真正执行"""
    lock = _ROOT / "logs" / "runtime" / engine_key / f"{task_id}_r{round_no}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return True   # 文件系统不支持时退化为允许执行


def _render_phases(status: dict, expanded: bool = True):
    phases = status.get("phases") or []
    if not phases:
        st.caption("等待引擎输出…")
        return
    st.markdown("#### 🔎 分析过程")
    for ph in phases:
        ts = (ph.get("ts") or "")[11:19]
        text = ph.get("text", "")
        st.markdown(f"`{ts}` {text}")


def _render_progress(status: dict):
    s = status.get("status", "queued")
    progress = int(status.get("progress", 0) or 0)
    stage = status.get("stage", "")
    if s == "done":
        st.progress(100, text="分析完成")
    elif s == "error":
        st.error(f"运行失败：{status.get('error', '未知错误')}")
    else:
        st.progress(progress, text=f"{stage}（{progress}%）")


def render_page(agent_cls, engine_key: str, title: str, caption: str,
                default_query: str, port: int):
    st.set_page_config(page_title=title, page_icon="🏃", layout="wide")
    st.title(title)
    st.caption(caption)

    query, task_id, auto, round_no, guidance, skip = _resolve_inputs(default_query)
    status = read_status(engine_key, task_id) if task_id else {}

    with st.sidebar:
        st.markdown(f"**引擎**：`{engine_key}`")
        st.markdown(f"**端口**：`{port}`")
        if task_id:
            st.markdown(f"**任务**：`{task_id}`")
            st.markdown(f"**轮次**：第 `{round_no}` 轮")
        st.markdown(f"**运行态**：`{status.get('status', '无')}`")
        st.markdown("[← 返回主页](http://localhost:5000)")

    # ---- 非本轮目标引擎：只展示上一轮报告，不重复执行 ----
    if auto and skip:
        st.info(f"本轮由其他引擎复跑，本引擎展示第 {status.get('round', round_no - 1)} 轮报告")
        if status.get("report_path"):
            _show_report(status["report_path"], agent_cls.ENGINE_NAME)
        else:
            st.caption("本引擎暂无可展示的报告")
        return

    # ---- 主页触发（环境变量 / URL 参数）----
    if auto:
        st.markdown(f"**收到统一提问（第 {round_no} 轮）**：{query}")
        if guidance:
            st.markdown(f"**HOST 补充指引**：{guidance}")

        # 已完成 / 已失败：直接展示结果 + 过程
        if status.get("status") == "done" and status.get("report_path"):
            _show_report(status["report_path"], agent_cls.ENGINE_NAME)
            with st.expander("查看分析过程", expanded=False):
                _render_phases(status)
            return
        if status.get("status") == "error":
            _render_progress(status)
            with st.expander("查看分析过程", expanded=True):
                _render_phases(status)
            return

        # 需要执行：抢到任务锁的会话启动后台线程跑 agent
        job_key = f"job_started_{task_id}_r{round_no}"
        if status.get("status") != "running" and not st.session_state.get(job_key):
            if _claim_task(engine_key, task_id, round_no):
                st.session_state[job_key] = True

                def _job():
                    try:
                        agent_cls().research(query, guidance_from_host=guidance,
                                             task_id=task_id, round_no=round_no)
                    except Exception as e:
                        print(f"[{engine_key}] 任务执行失败: {e}")

                threading.Thread(target=_job, daemon=True).start()
            else:
                st.info("该任务正在本引擎进程中运行，已接入当前进度。")
        elif status.get("status") == "running" and not st.session_state.get(job_key):
            st.info("该任务正在本引擎进程中运行，已接入当前进度。")

        # 轮询运行态并实时展示
        _render_progress(status)
        _render_phases(status)
        time.sleep(POLL_INTERVAL)
        st.rerun()

    # ---- 手动调试模式 ----
    manual_query = st.text_area("请输入问题", value=default_query, height=80)
    if st.button("🚀 开始运行", type="primary"):
        try:
            with st.spinner("正在运行，请稍候…"):
                out_path = agent_cls().run(manual_query)
        except Exception as e:
            st.error(f"运行失败：{type(e).__name__}: {e}")
            return
        _show_report(out_path, agent_cls.ENGINE_NAME)
