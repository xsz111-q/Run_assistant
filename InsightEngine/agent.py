# -*- coding: utf-8 -*-
"""InsightEngine（Iter 2）

运动科学家 —— 基于本地训练数据库（training_records_excel）做数据分析。

流程：
  step1  LLM → 生成 2 个分析段落
  step2  逐段：LLM 生成查询思路 → 查库 → LLM 总结
  step3  LLM → 汇总 2 段 → 完整 Markdown 报告
  step4  写报告 + 写运行态 + 论坛发言

Iter 2 新增：
  - research(query, guidance_from_host, task_id)：供 Streamlit 进程调用
  - 运行态写入 logs/runtime/insight/{task_id}.json
  - 完成后向论坛追加一条结构化发言
"""

import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from models.training_record import TrainingRecordExcel
from utils.engine_runtime import write_status, append_phase
from utils.persona import DEFAULT_PERSONA, persona_block
from ForumEngine.monitor import append_post

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from openai import OpenAI


class SportsScientistAgent:
    """运动科学家 — 本地训练数据分析"""

    ENGINE_KEY = "insight"
    ENGINE_NAME = "InsightEngine"
    OUTPUT_FILE = "insight_report.md"
    REPORT_DIR = config.INSIGHT_REPORTS_DIR

    def __init__(self):
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.engine = create_engine(
            f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}"
            f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
            f"?charset={config.DB_CHARSET}",
            pool_pre_ping=True,
        )
        self.Session = sessionmaker(bind=self.engine)

    # ---------- 对外入口 ----------
    def run(self, query: str, output_dir: str = None) -> str:
        """手动调试入口（不写运行态、不发论坛）"""
        return self._research(query, "", None, output_dir)

    def research(self, query: str, guidance_from_host: str = "",
                 task_id: str = None, round_no: int = 1,
                 output_dir: str = None, persona: str = "") -> str:
        """Streamlit 进程 / 主页流程入口（persona 为画像文本，Issue #5）"""
        return self._research(query, guidance_from_host, task_id, output_dir, round_no, persona)

    # ---------- 主流程 ----------
    def _research(self, query, guidance_from_host, task_id, output_dir, round_no=1,
                  persona="") -> str:
        out_dir = Path(output_dir) if output_dir else _ROOT / self.REPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"insight_report_{task_id}_r{round_no}.md" if task_id else self.OUTPUT_FILE
        out_file = out_dir / fname

        print(f"[{self.ENGINE_NAME}] 第 {round_no} 轮开始分析: {query}")
        self._task_id = task_id
        self._persona = persona or DEFAULT_PERSONA
        self._status(task_id, status="running", progress=5,
                     stage=f"第 {round_no} 轮：规划分析段落",
                     query=query, report_path="", round=round_no)
        self._phase(f"第 {round_no} 轮收到问题：{query}")
        self._phase(self._persona)
        if guidance_from_host:
            self._phase(f"HOST 补充指引：{guidance_from_host}")

        try:
            paragraphs = self._plan_paragraphs(query)
            self._status(task_id, progress=15, stage=f"已规划 {len(paragraphs)} 个分析段落")
            self._phase("分析段落规划完成：" + "；".join(paragraphs))
            print(f"[{self.ENGINE_NAME}] 规划段落: {paragraphs}")

            summaries = []
            total = max(len(paragraphs), 1)
            for i, para_title in enumerate(paragraphs, 1):
                self._status(task_id, progress=15 + int(65 * i / total),
                             stage=f"分析段落 {i}/{total}：{para_title}")
                print(f"[{self.ENGINE_NAME}] 段落 {i}/{total}: {para_title}")
                summary = self._analyze_one_paragraph(query, para_title)
                summaries.append(f"## {para_title}\n\n{summary}")

            self._status(task_id, progress=85, stage="汇总生成报告")
            self._phase("开始汇总生成完整报告…")
            full = self._final_report(query, summaries, guidance_from_host)
            out_file.write_text(full, encoding="utf-8")
            print(f"[{self.ENGINE_NAME}] 报告已写入: {out_file}")

            self._status(task_id, status="done", progress=100,
                         stage=f"第 {round_no} 轮分析完成",
                         report_path=str(out_file), round=round_no)
            self._post_to_forum(query, summaries, out_file, task_id, round_no)
            return str(out_file)
        except Exception as e:
            self._status(task_id, status="error", stage="分析失败",
                         error=f"{type(e).__name__}: {e}")
            raise

    # ---------- 辅助 ----------
    def _status(self, task_id, **fields):
        if not task_id:
            return
        try:
            write_status(self.ENGINE_KEY, task_id, **fields)
        except Exception as e:
            print(f"[{self.ENGINE_NAME}] 写运行态失败: {e}")

    def _phase(self, text: str):
        """阶段性输出：写运行态，供子引擎页面实时展示"""
        tid = getattr(self, "_task_id", None)
        if not tid:
            return
        try:
            append_phase(self.ENGINE_KEY, tid, text)
        except Exception as e:
            print(f"[{self.ENGINE_NAME}] 写阶段日志失败: {e}")

    def _post_to_forum(self, query, summaries, out_file, task_id, round_no=1):
        if not task_id:
            return
        digest = "；".join(
            s.split("\n\n", 1)[-1].replace("\n", " ")[:150] for s in summaries
        )
        try:
            append_post(
                agent=self.ENGINE_KEY,
                content=f"第 {round_no} 轮针对「{query}」的数据分析已完成：{digest}",
                task_id=task_id,
                kind="report",
                round_no=round_no,
                report_path=str(out_file),
            )
        except Exception as e:
            print(f"[{self.ENGINE_NAME}] 论坛发言失败: {e}")

    # ---------- LLM 节点 ----------
    def _plan_paragraphs(self, query: str) -> list:
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学教练。请把用户问题拆成恰好 2 个分析段落标题，每条一行，不要编号，不要其他解释。段落规划需结合用户画像。"},
                {"role": "user", "content": f"用户问题：{query}\n{self._persona}"},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:config.PARAGRAPHS_PER_ENGINE]

    def _analyze_one_paragraph(self, query: str, para_title: str) -> str:
        db_summary = self._build_db_summary(query, para_title)
        self._phase(f"「{para_title}」数据库摘要：\n{db_summary}")
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学家，请基于下方的训练数据摘要和用户画像，用中文输出 150~250 字的个性化专业分析。只写分析正文，不要标题。"},
                {"role": "user", "content":
                    f"用户原始问题：{query}\n"
                    f"{self._persona}\n"
                    f"本段分析主题：{para_title}\n"
                    f"数据库摘要：\n{db_summary}"},
            ],
        )
        summary = resp.choices[0].message.content.strip()
        self._phase(f"「{para_title}」分析完成：{summary[:120]}…")
        return summary

    def _build_db_summary(self, query: str, para_title: str) -> str:
        """查库生成数据摘要（Excel 导入的本地训练记录）"""
        session = self.Session()
        try:
            total = session.query(func.count(TrainingRecordExcel.id)).scalar() or 0
            lines = [f"训练记录总数：{total}"]
            if not total:
                lines.append("（数据库中暂无训练数据，请在「训练数据管理」页导入 Excel）")
                return "\n".join(lines)

            avg_hr = session.query(func.avg(TrainingRecordExcel.avg_heart_rate)).scalar()
            avg_dist = session.query(func.avg(TrainingRecordExcel.distance_meters)).scalar()
            avg_dur = session.query(func.avg(TrainingRecordExcel.duration_seconds)).scalar()

            latest = session.query(func.max(TrainingRecordExcel.start_time)).scalar()
            earliest = session.query(func.min(TrainingRecordExcel.start_time)).scalar()

            if latest and earliest:
                lines.append(f"数据区间：{earliest.date()} ~ {latest.date()}")
            if avg_dur:
                lines.append(f"平均单次时长：{int(avg_dur // 60)} 分钟")
            if avg_dist:
                lines.append(f"平均单次距离：{float(avg_dist) / 1000:.2f} km")
            if avg_hr:
                lines.append(f"平均心率：{float(avg_hr):.0f} bpm")

            types = (session.query(TrainingRecordExcel.exercise_type,
                                   func.count(TrainingRecordExcel.id))
                     .group_by(TrainingRecordExcel.exercise_type).all())
            lines.append("运动类型分布：" + "，".join(f"{t}:{c} 次" for t, c in types))

            recent = (session.query(TrainingRecordExcel)
                      .order_by(TrainingRecordExcel.start_time.desc())
                      .limit(5).all())
            lines.append("最近 5 条训练：")
            for r in recent:
                dist = f"{float(r.distance_meters) / 1000:.2f}km" if r.distance_meters else "-"
                lines.append(
                    f"  - [{r.start_time.date()}] {r.exercise_type} {dist} | "
                    f"{r.duration_seconds // 60}min | HR {r.avg_heart_rate or '-'}"
                )
            return "\n".join(lines)
        finally:
            session.close()

    def _final_report(self, query: str, summaries: list, guidance_from_host: str = "") -> str:
        user_content = f"用户问题：{query}\n\n{self._persona}\n\n各段分析：\n\n" + "\n\n".join(summaries)
        if guidance_from_host:
            user_content += f"\n\n主持人（HOST）给出的补充指引：\n{guidance_from_host}"

        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学报告撰写者。把多段分析合并成一份结构清晰的中文 Markdown 报告，开头加问题和日期，建议需与用户画像匹配。"},
                {"role": "user", "content": user_content},
            ],
        )
        return (
            f"# InsightEngine 分析报告\n\n"
            f"**问题**: {query}\n\n"
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + persona_block(self._persona)
            + resp.choices[0].message.content.strip() + "\n"
        )


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "如何提升跑步耐力"
    SportsScientistAgent().run(q)
