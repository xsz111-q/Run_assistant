# -*- coding: utf-8 -*-
"""MediaEngine 极简版（Iter 1 裁剪后）

实用情报 Agent — 博查多模态搜索（赛事 / 天气 / 装备 / 路线）
骨架同上：6 次 LLM 调用
"""

import sys
import requests
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from openai import OpenAI


class LogisticsIntelligenceAgent:
    ENGINE_NAME = "MediaEngine"
    OUTPUT_FILE = "media_report.md"

    BOCHA_URL = "https://api.bocha.cn/v1/ai-search"

    def __init__(self):
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

    def run(self, query: str, output_dir: str = None) -> str:
        out_dir = Path(output_dir) if output_dir else _ROOT / config.REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / self.OUTPUT_FILE

        print(f"[{self.ENGINE_NAME}] 开始实用情报搜索: {query}")

        paragraphs = self._plan_paragraphs(query)
        print(f"[{self.ENGINE_NAME}] 规划段落: {paragraphs}")

        summaries = []
        for i, para_title in enumerate(paragraphs, 1):
            print(f"[{self.ENGINE_NAME}] 段落 {i}/{len(paragraphs)}: {para_title}")
            summary = self._analyze_one_paragraph(query, para_title)
            summaries.append(f"## {para_title}\n\n{summary}")

        full = self._final_report(query, summaries)
        out_file.write_text(full, encoding="utf-8")
        print(f"[{self.ENGINE_NAME}] 报告已写入: {out_file}")
        return str(out_file)

    # ---------- 节点 ----------
    def _plan_paragraphs(self, query: str) -> list:
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是跑步实用情报专家。把用户问题拆成恰好 2 个实用信息子标题（如赛事报名 / 装备推荐 / 路线攻略 / 天气提示 等方向），每条一行，不要编号，不要其他解释。"},
                {"role": "user", "content": f"用户问题：{query}"},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:config.PARAGRAPHS_PER_ENGINE]

    def _analyze_one_paragraph(self, query: str, para_title: str) -> str:
        # LLM 生成 Bocha 搜索词
        q_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是搜索词生成器。为下面的实用信息主题生成 1 条精确中文搜索词，只要词本身，不要解释。"},
                {"role": "user", "content": f"主题：{para_title}；用户问题：{query}"},
            ],
        )
        search_q = q_resp.choices[0].message.content.strip()

        # Bocha 搜索
        results_text = ""
        try:
            resp = requests.post(
                self.BOCHA_URL,
                headers={
                    "Authorization": f"Bearer {config.BOCHA_WEB_SEARCH_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"query": search_q, "count": 5},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("data", {}).get("items", []) if isinstance(data, dict) else []
                results_text = "\n".join(
                    f"- [{it.get('title','')}]({it.get('url','')})：{it.get('summary','')[:200]}"
                    for it in items[:5]
                )
            else:
                results_text = f"(Bocha 返回 {resp.status_code}: {resp.text[:200]})"
        except Exception as e:
            results_text = f"(Bocha 搜索失败: {e})"

        # LLM 总结
        s_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是实用情报专家。基于搜索结果，用中文输出 150~250 字的实用建议，只写正文。"},
                {"role": "user", "content":
                    f"用户原始问题：{query}\n本段主题：{para_title}\n搜索结果：\n{results_text}"},
            ],
        )
        return s_resp.choices[0].message.content.strip()

    def _final_report(self, query: str, summaries: list) -> str:
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是实用情报报告撰写者。把多段建议合并成中文 Markdown 报告，开头加问题和日期。"},
                {"role": "user", "content":
                    f"用户问题：{query}\n\n各段建议：\n\n" + "\n\n".join(summaries)},
            ],
        )
        return (
            f"# MediaEngine 实用情报报告\n\n"
            f"**问题**: {query}\n\n"
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + resp.choices[0].message.content.strip() + "\n"
        )


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "如何提升跑步耐力"
    LogisticsIntelligenceAgent().run(q)
