# -*- coding: utf-8 -*-
"""调度器（Iter 3 简化版）

流程：
  Round 1：三引擎并行 fan-out（各自 Streamlit 进程）
    → 收论坛发言 → HOST 仲裁（共识 / 矛盾 / 盲区 + 复跑建议）
    → 有矛盾且预算允许 → Round 2 只复跑相关引擎（带 HOST 指引）
  停止条件：达到 max_rounds / LLM 调用估算上限 / 总超时 / 无矛盾 / 手动停止

状态写到 logs/orchestrator/{task_id}.json，供主页轮询展示。
"""

import json
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from openai import OpenAI
from ForumEngine.monitor import append_post, read_posts
from utils.engine_runtime import read_status, write_status
from utils.process_manager import ENGINES, EngineProcessManager

ARBITRATION_PROMPT = (
    "你是跑步训练多智能体系统的总教练（HOST），负责主持三方专家讨论。"
    "三位专家分别是：运动科学家（基于跑者真实训练数据）、理论专家（运动科学文献）、"
    "实用情报专家（赛事/装备/路线/天气）。"
    "请判断他们的发言是否足以回答用户问题，并只输出一个 JSON 对象，不要输出任何其他文字、"
    "不要使用代码块。JSON 字段：\n"
    '{"consensus": "三方一致的核心结论（80字内）",\n'
    ' "conflicts": ["需要澄清的分歧点，最多3条，没有则空数组"],\n'
    ' "blind_spots": ["尚未覆盖的信息盲区，最多3条，没有则空数组"],\n'
    ' "need_rerun": true 或 false（是否需要让部分专家补充研究）,\n'
    ' "rerun_engines": ["insight" / "query" / "media" 的子集，need_rerun 为 false 时用 []],\n'
    ' "guidance": "给复跑专家的具体补充指引，100字内，没有则空字符串",\n'
    ' "host_speech": "250~350字中文主持词，包含共识、分歧/盲区、给跑者的3条行动建议，不要标题"}'
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class OrchestratorScheduler:
    """轮次调度：fan-out → 仲裁 → 定向复跑"""

    def __init__(self, emit=None):
        self.emit = emit or (lambda event, payload: None)
        self.state = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ---------- 对外 ----------
    def start(self, query: str, task_id: str) -> threading.Thread:
        self.state = {
            "task_id": task_id,
            "query": query,
            "status": "running",
            "phase": "starting",
            "round": 0,
            "max_rounds": config.ORCH_MAX_ROUNDS,
            "llm_calls_estimate": 0,
            "rounds": [],
            "arbitration": {},
            "stop_reason": "",
            "started_at": _now(),
            "updated_at": _now(),
        }
        with self._lock:
            self._save()
        t = threading.Thread(target=self._run, args=(query, task_id), daemon=True)
        t.start()
        return t

    def stop(self, reason: str = "manual_stop"):
        self._stop.set()
        with self._lock:
            self.state["stop_reason"] = reason
            self._save()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, ensure_ascii=False))

    # ---------- 主循环 ----------
    def _run(self, query: str, task_id: str):
        deadline = time.time() + config.ORCH_TIMEOUT
        targets = set(ENGINES)
        guidance = ""

        try:
            for round_no in range(1, config.ORCH_MAX_ROUNDS + 1):
                if self._should_stop("before_round"):
                    break

                self._update(round=round_no, phase="starting_round",
                             current_targets=sorted(targets))
                self.emit("orchestrator_update", self.snapshot())

                engine_state = self._run_round(
                    query, task_id, round_no, targets, guidance, deadline
                )
                with self._lock:
                    self.state["llm_calls_estimate"] += \
                        len(targets) * config.ORCH_LLM_CALLS_PER_ENGINE

                if self._should_stop("after_round"):
                    break

                # 本轮论坛发言
                posts = [p for p in read_posts()
                         if p.get("task_id") == task_id and p.get("round") == round_no
                         and p.get("kind") == "report"]

                self._update(phase="arbitration")
                arbitration = self._arbitrate(query, posts, round_no, guidance)
                self._update(arbitration=arbitration,
                             arbitration_round=round_no)

                round_record = {
                    "round": round_no,
                    "targets": sorted(targets),
                    "engines": engine_state,
                    "arbitration": arbitration,
                    "finished_at": _now(),
                }
                with self._lock:
                    self.state.setdefault("rounds", []).append(round_record)
                    self._save()

                self.emit("arbitration", {
                    "task_id": task_id, "round": round_no,
                    **arbitration,
                })

                if not arbitration.get("need_rerun"):
                    self._finish("done", "no_conflict")
                    return

                if round_no >= config.ORCH_MAX_ROUNDS:
                    self._finish("done", "max_rounds_reached")
                    return

                rerun = [e for e in arbitration.get("rerun_engines") or []
                         if e in ENGINES]
                if not rerun:
                    self._finish("done", "no_engine_to_rerun")
                    return

                targets = set(rerun)
                guidance = arbitration.get("guidance", "")

                estimate = self.state["llm_calls_estimate"] + \
                    len(targets) * config.ORCH_LLM_CALLS_PER_ENGINE
                if estimate > config.ORCH_MAX_LLM_CALLS:
                    self._finish("done", "llm_budget_exceeded")
                    return

                if time.time() >= deadline:
                    self._finish("timeout", "timeout")
                    return

            self._finish("done", self.state.get("stop_reason") or "finished")
        except Exception as e:
            self._fail(f"{type(e).__name__}: {e}")

    # ---------- 单轮 ----------
    def _run_round(self, query, task_id, round_no, targets, guidance, deadline) -> dict:
        ready = EngineProcessManager.start_round(
            query, task_id, round_no=round_no,
            targets=targets, guidance=guidance,
        )
        self._update(phase=f"round{round_no}_running")
        self.emit("round_started", {
            "task_id": task_id, "round": round_no,
            "targets": sorted(targets),
            "engines": {k: {"port": ENGINES[k]["port"], "ready": ready.get(k, False),
                            "name": ENGINES[k]["name"]} for k in ENGINES},
        })

        round_deadline = min(deadline, time.time() + config.ORCH_ROUND_TIMEOUT)
        results = {}
        while True:
            results = {}
            all_terminal = True
            for key in targets:
                st = read_status(key, task_id)
                status = st.get("status", "pending")
                results[key] = {
                    "status": status,
                    "round": st.get("round", round_no),
                    "stage": st.get("stage", ""),
                    "report_path": st.get("report_path", ""),
                    "error": st.get("error", ""),
                }
                if status not in ("done", "error"):
                    all_terminal = False
                    if not EngineProcessManager.is_alive(key):
                        write_status(key, task_id, status="error",
                                     stage="引擎进程已退出",
                                     error=f"请查看 logs/{key}.log")
                        results[key]["status"] = "error"
                        results[key]["error"] = "引擎进程已退出"

            if all_terminal:
                return results

            if self._stop.is_set():
                return results
            if time.time() >= round_deadline:
                for key, info in results.items():
                    if info["status"] not in ("done", "error"):
                        write_status(key, task_id, status="error",
                                     stage=f"第 {round_no} 轮超时",
                                     error=f"超过 {config.ORCH_ROUND_TIMEOUT}s 未完成")
                return results

            time.sleep(config.STATUS_POLL_INTERVAL)

    # ---------- 仲裁 ----------
    def _arbitrate(self, query, posts, round_no, prev_guidance) -> dict:
        digest = "\n\n".join(
            f"【{p.get('agent_name', p.get('agent'))}】\n{(p.get('content') or '')[:600]}"
            for p in posts
        ) or "（本轮没有专家发言）"

        user = f"用户问题：{query}\n\n第 {round_no} 轮专家发言：\n\n{digest}"
        if prev_guidance:
            user += f"\n\n上一轮给专家的补充指引：\n{prev_guidance}"

        with self._lock:
            self.state["llm_calls_estimate"] += 1

        try:
            client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
            resp = client.chat.completions.create(
                model=config.DEFAULT_MODEL_NAME,
                messages=[
                    {"role": "system", "content": ARBITRATION_PROMPT},
                    {"role": "user", "content": user},
                ],
                timeout=90,
            )
            raw = resp.choices[0].message.content.strip()
            data = self._parse_json(raw)
        except Exception as e:
            data = self._fallback_arbitration(posts, e)

        data.setdefault("consensus", "")
        data.setdefault("conflicts", [])
        data.setdefault("blind_spots", [])
        data.setdefault("need_rerun", False)
        data.setdefault("rerun_engines", [])
        data.setdefault("guidance", "")
        data.setdefault("host_speech", data.get("consensus", ""))

        try:
            append_post(
                agent="host",
                content=data["host_speech"],
                task_id=self.state["task_id"],
                kind="arbitration",
                round_no=round_no,
            )
        except Exception as e:
            print(f"[Orchestrator] 仲裁发言写入失败: {e}")

        return data

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
        return json.loads(text)

    @staticmethod
    def _fallback_arbitration(posts, err) -> dict:
        names = "、".join(p.get("agent_name", "") for p in posts) or "各引擎"
        return {
            "consensus": f"{names} 已完成本轮分析，详情见各自报告。",
            "conflicts": [],
            "blind_spots": [],
            "need_rerun": False,
            "rerun_engines": [],
            "guidance": "",
            "host_speech": (f"{names} 已完成第 1 轮分析。建议先阅读三份报告中的数据结论，"
                            "再结合自身训练安排取舍；若数据不足，请先补充训练记录后再次提问。"
                            f"\n\n（HOST 仲裁调用失败：{type(err).__name__}）"),
        }

    # ---------- 状态 ----------
    def _should_stop(self, stage: str) -> bool:
        if self._stop.is_set():
            self._finish("stopped", self.state.get("stop_reason") or "manual_stop")
            return True
        if self.state.get("llm_calls_estimate", 0) >= config.ORCH_MAX_LLM_CALLS:
            self._finish("done", "llm_budget_exceeded")
            return True
        return False

    def _update(self, **fields):
        with self._lock:
            self.state.update(fields)
            self.state["updated_at"] = _now()
            self._save()

    def _finish(self, status: str, reason: str):
        with self._lock:
            self.state.update({
                "status": status,
                "phase": "finished",
                "stop_reason": reason,
                "finished_at": _now(),
                "updated_at": _now(),
            })
            self._save()
        self.emit("orchestrator_update", self.snapshot())

    def _fail(self, error: str):
        with self._lock:
            self.state.update({
                "status": "error",
                "phase": "finished",
                "error": error,
                "finished_at": _now(),
                "updated_at": _now(),
            })
            self._save()
        self.emit("orchestrator_update", self.snapshot())

    def _state_path(self) -> Path:
        p = _ROOT / config.ORCH_STATE_DIR / f"{self.state['task_id']}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def _save(self):
        """调用方需持有 _lock（start/_update/_finish 内部已加锁）"""
        try:
            p = self._state_path()
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            print(f"[Orchestrator] 状态落盘失败: {e}")


def read_orchestrator_state(task_id: str) -> dict:
    p = _ROOT / config.ORCH_STATE_DIR / f"{task_id}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
