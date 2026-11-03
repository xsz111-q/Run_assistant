# -*- coding: utf-8 -*-
"""InsightEngine 极简版（Iter 1 裁剪后）

工作流（砍到只剩骨架，注释标出被裁掉的步骤）：
  step1  LLM → 生成 2 个分析段落（原项目 N=4~5，砍到 2）
  step2  对每个段落：
          LLM 生成 MySQL 查询参数 → SQLAlchemy 查库 → LLM 总结本段
          【裁掉】原项目的反思循环（max_reflections=3 轮，现置 0）
  step3  LLM → 汇总 2 段 → 完整 Markdown 报告
  step4  写文件到 reports/

LLM 调用量：1 + 2×(1+1) + 1 = 6 次
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path

# 项目根路径（方便直接运行 agent.py 调试）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from InsightEngine.db_models import Base, TrainingRecordKeep

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from openai import OpenAI


class SportsScientistAgent:
    """运动科学家 — 基于本地训练数据库做数据分析"""

    ENGINE_NAME = "InsightEngine"
    OUTPUT_FILE = "insight_report.md"

    def __init__(self):
        self.llm = OpenAI(
            api_key=config.LLM_API_KEY,
            base_url=config.LLM_BASE_URL,
        )
        self.engine = create_engine(
            f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}"
            f"@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
            f"?charset={config.DB_CHARSET}"
        )
        self.Session = sessionmaker(bind=self.engine)

    # ---------- 入口 ----------
    def run(self, query: str, output_dir: str = None) -> str:
        out_dir = Path(output_dir) if output_dir else _ROOT / config.REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / self.OUTPUT_FILE

        print(f"[{self.ENGINE_NAME}] 开始分析: {query}")

        # step1 生成段落
        paragraphs = self._plan_paragraphs(query)
        print(f"[{self.ENGINE_NAME}] 规划段落: {paragraphs}")

        # step2 逐段处理
        summaries = []
        for i, para_title in enumerate(paragraphs, 1):
            print(f"[{self.ENGINE_NAME}] 段落 {i}/{len(paragraphs)}: {para_title}")
            summary = self._analyze_one_paragraph(query, para_title)
            summaries.append(f"## {para_title}\n\n{summary}")

        # step3 汇总
        full = self._final_report(query, summaries)

        # step4 写文件
        out_file.write_text(full, encoding="utf-8")
        print(f"[{self.ENGINE_NAME}] 报告已写入: {out_file}")
        return str(out_file)

    # ---------- LLM 节点 ----------
    def _plan_paragraphs(self, query: str) -> list:
        """LLM 根据用户问题生成 2 个分析维度"""
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学教练。请把用户问题拆成恰好 2 个分析段落标题，每条一行，不要编号，不要其他解释。"},
                {"role": "user", "content": f"用户问题：{query}"},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:config.PARAGRAPHS_PER_ENGINE]

    def _analyze_one_paragraph(self, query: str, para_title: str) -> str:
        """单段：LLM 生成 MySQL 查询条件 → 查库 → LLM 总结"""
        db_summary = self._build_db_summary(query, para_title)
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学家，请基于下方的训练数据摘要，用中文输出 150~250 字的专业分析。只写分析正文，不要标题。"},
                {"role": "user", "content":
                    f"用户原始问题：{query}\n"
                    f"本段分析主题：{para_title}\n"
                    f"数据库摘要：\n{db_summary}"},
            ],
        )
        return resp.choices[0].message.content.strip()

    def _build_db_summary(self, query: str, para_title: str) -> str:
        """查库生成数据摘要字符串（用 Keep 表）"""
        session = self.Session()
        try:
            total = session.query(func.count(TrainingRecordKeep.id)).scalar() or 0
            # 最近 10 条 + 聚合指标
            recent = (session.query(TrainingRecordKeep)
                      .order_by(TrainingRecordKeep.start_time.desc())
                      .limit(10).all())
            summary_lines = [f"训练记录总数：{total}"]
            if total:
                # 聚合
                avg_hr = session.query(func.avg(TrainingRecordKeep.avg_heart_rate)).scalar()
                avg_dist = session.query(func.avg(TrainingRecordKeep.distance_meters)).scalar()
                summary_lines.append(f"平均心率：{avg_hr:.0f} bpm")
                summary_lines.append(f"平均单次距离：{avg_dist/1000:.2f} km")
                # 各运动类型次数
                types = session.query(
                    TrainingRecordKeep.exercise_type,
                    func.count(TrainingRecordKeep.id)
                ).group_by(TrainingRecordKeep.exercise_type).all()
                summary_lines.append("运动类型分布：" + ", ".join(f"{t}:{c}" for t, c in types))
                # 最近 5 条简要
                summary_lines.append("最近 5 条训练：")
                for r in recent[:5]:
                    summary_lines.append(
                        f"  - [{r.start_time.date()}] {r.exercise_type} "
                        f"{r.distance_meters/1000:.2f}km | {r.duration_seconds//60}min | HR {r.avg_heart_rate}"
                    )
            else:
                summary_lines.append("（数据库中暂无训练数据）")
            return "\n".join(summary_lines)
        finally:
            session.close()

    def _final_report(self, query: str, summaries: list) -> str:
        """LLM 把多段汇总成完整 Markdown 报告"""
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学报告撰写者。把多段分析合并成一份结构清晰的中文 Markdown 报告，开头加问题和日期。"},
                {"role": "user", "content":
                    f"用户问题：{query}\n\n"
                    f"各段分析：\n\n" + "\n\n".join(summaries)},
            ],
        )
        return (
            f"# InsightEngine 分析报告\n\n"
            f"**问题**: {query}\n\n"
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + resp.choices[0].message.content.strip() + "\n"
        )


# ---------- 直接运行入口（调试用） ----------
if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "如何提升跑步耐力"
    SportsScientistAgent().run(q)
