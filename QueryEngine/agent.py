# -*- coding: utf-8 -*-
"""QueryEngine 极简版（Iter 1 裁剪后）

理论专家 — Tavily 学术搜索，骨架同 InsightEngine，数据源换成 Tavily
LLM 调用量同上：6 次
"""

import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from openai import OpenAI
from tavily import TavilyClient


class TheoryExpertAgent:
    ENGINE_NAME = "QueryEngine"
    OUTPUT_FILE = "query_report.md"

    def __init__(self):
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.tavily = TavilyClient(api_key=config.TAVILY_API_KEY)

    def run(self, query: str, output_dir: str = None) -> str:
        out_dir = Path(output_dir) if output_dir else _ROOT / config.REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / self.OUTPUT_FILE

        print(f"[{self.ENGINE_NAME}] 开始理论研究: {query}")

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
                {"role": "system", "content": "你是运动科学理论专家。把用户问题拆成恰好 2 个学术研究子标题，每条一行，不要编号，不要其他解释。"},
                {"role": "user", "content": f"用户问题：{query}"},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:config.PARAGRAPHS_PER_ENGINE]

    def _analyze_one_paragraph(self, query: str, para_title: str) -> str:
        # LLM 生成搜索词（给 Tavily 用）
        q_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是专业搜索查询生成器。请给下面的研究主题生成 1~3 条英文学术搜索查询，每条一行，不要编号，不要解释。"},
                {"role": "user", "content": f"研究主题：{para_title}；用户原始问题：{query}"},
            ],
        )
        search_queries = [l.strip() for l in q_resp.choices[0].message.content.splitlines() if l.strip()]

        # Tavily 搜索（用第一条最关键的）
        results_text = ""
        if search_queries:
            try:
                result = self.tavily.search(
                    query=search_queries[0],
                    search_depth="advanced",
                    max_results=5,
                    include_domains=[
                        "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
                        "trainingpeaks.com", "runnersworld.com",
                        "98pao.com", "strava.com", "outsideonline.com",
                    ],
                )
                results_text = "\n".join(
                    f"- [{r.get('title','')}]({r.get('url','')})：{r.get('content','')[:200]}"
                    for r in result.get("results", [])
                )
            except Exception as e:
                results_text = f"(Tavily 搜索失败: {e})"

        # LLM 总结本段
        s_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学专家。基于搜索结果，用中文输出 150~250 字的学术性分析，标明引用来源编号，只写正文。"},
                {"role": "user", "content":
                    f"用户原始问题：{query}\n"
                    f"本段主题：{para_title}\n"
                    f"搜索结果：\n{results_text}"},
            ],
        )
        return s_resp.choices[0].message.content.strip()

    def _final_report(self, query: str, summaries: list) -> str:
        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是理论研究报告撰写者。把多段学术分析合并成一份中文 Markdown 报告，开头加问题和日期。"},
                {"role": "user", "content":
                    f"用户问题：{query}\n\n各段分析：\n\n" + "\n\n".join(summaries)},
            ],
        )
        return (
            f"# QueryEngine 理论分析报告\n\n"
            f"**问题**: {query}\n\n"
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + resp.choices[0].message.content.strip() + "\n"
        )


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "如何提升跑步耐力"
    TheoryExpertAgent().run(q)
