# -*- coding: utf-8 -*-
"""QueryEngine（Iter 2）

理论专家 —— Tavily 学术搜索，骨架同 InsightEngine，数据源换成 Tavily。
Iter 2 新增：research() 签名、运行态写入、论坛发言。
"""

import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from utils.engine_runtime import write_status, append_phase
from utils.persona import DEFAULT_PERSONA, persona_block
from ForumEngine.monitor import append_post

from openai import OpenAI
from tavily import TavilyClient


class TheoryExpertAgent:
    ENGINE_KEY = "query"
    ENGINE_NAME = "QueryEngine"
    OUTPUT_FILE = "query_report.md"
    REPORT_DIR = config.QUERY_REPORTS_DIR

    def __init__(self):
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.tavily = TavilyClient(api_key=config.TAVILY_API_KEY)

    # ---------- 对外入口 ----------
    def run(self, query: str, output_dir: str = None) -> str:
        return self._research(query, "", None, output_dir)

    def research(self, query: str, guidance_from_host: str = "",
                 task_id: str = None, round_no: int = 1,
                 output_dir: str = None, persona: str = "") -> str:
        return self._research(query, guidance_from_host, task_id, output_dir, round_no, persona)

    # ---------- 主流程 ----------
    def _research(self, query, guidance_from_host, task_id, output_dir, round_no=1,
                  persona="") -> str:
        out_dir = Path(output_dir) if output_dir else _ROOT / self.REPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"query_report_{task_id}_r{round_no}.md" if task_id else self.OUTPUT_FILE
        out_file = out_dir / fname

        print(f"[{self.ENGINE_NAME}] 第 {round_no} 轮开始理论研究: {query}")
        self._task_id = task_id
        self._persona = persona or DEFAULT_PERSONA
        self._status(task_id, status="running", progress=5,
                     stage=f"第 {round_no} 轮：规划研究段落",
                     query=query, report_path="", round=round_no)
        self._phase(f"第 {round_no} 轮收到问题：{query}")
        self._phase(self._persona)
        if guidance_from_host:
            self._phase(f"HOST 补充指引：{guidance_from_host}")

        try:
            paragraphs = self._plan_paragraphs(query)
            self._status(task_id, progress=15, stage=f"已规划 {len(paragraphs)} 个研究段落")
            self._phase("研究段落规划完成：" + "；".join(paragraphs))

            summaries = []
            total = max(len(paragraphs), 1)
            for i, para_title in enumerate(paragraphs, 1):
                self._status(task_id, progress=15 + int(65 * i / total),
                             stage=f"检索文献 {i}/{total}：{para_title}")
                print(f"[{self.ENGINE_NAME}] 段落 {i}/{total}: {para_title}")
                summary = self._analyze_one_paragraph(query, para_title)
                summaries.append(f"## {para_title}\n\n{summary}")

            self._status(task_id, progress=85, stage="汇总生成报告")
            self._phase("开始汇总生成完整报告…")
            full = self._final_report(query, summaries, guidance_from_host)
            out_file.write_text(full, encoding="utf-8")
            print(f"[{self.ENGINE_NAME}] 报告已写入: {out_file}")

            self._status(task_id, status="done", progress=100,
                         stage=f"第 {round_no} 轮研究完成",
                         report_path=str(out_file), round=round_no)
            self._post_to_forum(query, summaries, out_file, task_id, round_no)
            return str(out_file)
        except Exception as e:
            self._status(task_id, status="error", stage="研究失败",
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
                content=f"第 {round_no} 轮针对「{query}」的文献研究已完成：{digest}",
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
                {"role": "system", "content": "你是运动科学理论专家。把用户问题拆成恰好 2 个学术研究子标题，每条一行，不要编号，不要其他解释。子标题需结合用户画像。"},
                {"role": "user", "content": f"用户问题：{query}\n{self._persona}"},
            ],
        )
        text = resp.choices[0].message.content.strip()
        return [line.strip() for line in text.splitlines() if line.strip()][:config.PARAGRAPHS_PER_ENGINE]

    def _analyze_one_paragraph(self, query: str, para_title: str) -> str:
        q_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是专业搜索查询生成器。请给下面的研究主题生成 1~3 条英文学术搜索查询，每条一行，不要编号，不要解释。"},
                {"role": "user", "content": f"研究主题：{para_title}；用户原始问题：{query}"},
            ],
        )
        search_queries = [l.strip() for l in q_resp.choices[0].message.content.splitlines() if l.strip()]
        self._phase(f"「{para_title}」生成检索词：" + "；".join(search_queries))

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
                hits = result.get("results", [])
                results_text = "\n".join(
                    f"- [{r.get('title', '')}]({r.get('url', '')})：{r.get('content', '')[:200]}"
                    for r in hits
                )
                if hits:
                    titles = "；".join(h.get("title", "")[:40] for h in hits[:3])
                    self._phase(f"「{para_title}」Tavily 返回 {len(hits)} 条：{titles}")
            except Exception as e:
                results_text = f"(Tavily 搜索失败: {e})"
                self._phase(f"「{para_title}」Tavily 搜索失败：{e}")

        s_resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是运动科学专家。基于搜索结果和用户画像，用中文输出 150~250 字的学术性分析，标明引用来源编号，只写正文。"},
                {"role": "user", "content":
                    f"用户原始问题：{query}\n"
                    f"{self._persona}\n"
                    f"本段主题：{para_title}\n"
                    f"搜索结果：\n{results_text}"},
            ],
        )
        summary = s_resp.choices[0].message.content.strip()
        self._phase(f"「{para_title}」研究完成：{summary[:120]}…")
        return summary

    def _final_report(self, query: str, summaries: list, guidance_from_host: str = "") -> str:
        user_content = f"用户问题：{query}\n\n{self._persona}\n\n各段分析：\n\n" + "\n\n".join(summaries)
        if guidance_from_host:
            user_content += f"\n\n主持人（HOST）给出的补充指引：\n{guidance_from_host}"

        resp = self.llm.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": "你是理论研究报告撰写者。把多段学术分析合并成一份中文 Markdown 报告，开头加问题和日期，内容需与用户画像匹配。"},
                {"role": "user", "content": user_content},
            ],
        )
        return (
            f"# QueryEngine 理论分析报告\n\n"
            f"**问题**: {query}\n\n"
            f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + persona_block(self._persona)
            + resp.choices[0].message.content.strip() + "\n"
        )


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "如何提升跑步耐力"
    TheoryExpertAgent().run(q)
