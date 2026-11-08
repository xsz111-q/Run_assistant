# -*- coding: utf-8 -*-
"""ReportEngine（Iter 3 简化版）

职责：
  1) 模板选择：用 qwen-max-3 从 ReportEngine/report_template 下的 21 套中文模板中
     选出最贴合用户问题的一套（输出选择理由）
  2) HTML 生成：把三份引擎报告 + 论坛发言 + HOST 仲裁结论渲染成单文件 HTML
  3) 落盘：final_reports/{task_id}.html + 同名 .json 元数据

实现上保持单文件简化结构，不搬原项目的 nodes/state/prompts 分层。
"""

import html as html_lib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from openai import OpenAI

SELECT_PROMPT = (
    "你是跑步训练报告的编辑。下面是系统内置的 {n} 套中文报告模板名称。"
    "请根据用户问题和三方专家的分析内容，选出最能覆盖该问题的一套模板。"
    "只输出 JSON，不要输出其他文字："
    '{{"template_name": "模板文件名（不含 .md）", "reason": "一句话选择理由"}}'
)

HTML_PROMPT = (
    "你是专业的报告排版设计师。请把给定的分析材料整理成一份完整、可直接打开的单文件 HTML 报告。"
    "要求：\n"
    "1. 只输出 HTML 源码，从 <!DOCTYPE html> 开始，到 </html> 结束，不要输出任何解释或代码块标记；\n"
    "2. 所有 CSS 内联在 <style> 中，不引用任何外部资源（不要 CDN、不要外部字体/图片）；\n"
    "3. 结构参考给定模板的章节顺序，但内容必须全部来自给定材料，不得编造数据；\n"
    "4. 视觉要求：浅色背景、卡片式分区、清晰的标题层级、适合手机阅读（响应式）；\n"
    "5. 必含区块：报告标题、用户问题、生成时间、用户画像（紧跟开头，单独一个卡片）、三方专家要点、主持人仲裁（共识/分歧/盲区）、"
    "可执行建议清单；\n"
    "6. 个性化要求：建议清单中的每条建议需结合用户画像书写，引用画像中的具体字段"
    "（例如「根据您的半马破2目标……」「考虑到您是中年跑者……」「结合您工作日晚7-9点的可训练时间……」）；\n"
    "7. 中文排版，字号不小于 14px，行高 1.7。"
)

ENGINE_LABELS = {
    "insight": "运动科学家",
    "query": "理论专家",
    "media": "实用情报专家",
}


class ReportAgent:
    ENGINE_NAME = "ReportEngine"

    def __init__(self):
        self.llm = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        self.template_dir = _ROOT / config.REPORT_TEMPLATE_DIR

    # ---------- 模板 ----------
    def list_templates(self) -> list:
        if not self.template_dir.exists():
            return []
        return sorted(self.template_dir.glob("*.md"), key=lambda p: p.name)

    def select_template(self, query: str, material: str, persona: str = "") -> dict:
        templates = self.list_templates()
        if not templates:
            return {"template_name": "内置通用模板", "template_content": "", "reason": "未找到模板目录"}
        names = [p.stem for p in templates]

        try:
            resp = self.llm.chat.completions.create(
                model=config.REPORT_MODEL_NAME,
                messages=[
                    {"role": "system", "content": SELECT_PROMPT.format(n=len(names))},
                    {"role": "user", "content":
                        f"可选模板：\n" + "\n".join(f"- {n}" for n in names) +
                        f"\n\n用户问题：{query}\n\n{persona}\n\n分析材料摘要：\n{material[:2500]}"},
                ],
                timeout=60,
            )
            data = _parse_json(resp.choices[0].message.content)
            picked = str(data.get("template_name", "")).strip()
            picked = picked.replace(".md", "")
            match = next((p for p in templates if p.stem == picked), None)
            if not match:
                match = next((p for p in templates if picked and picked in p.stem), None)
            if match:
                return {"template_name": match.stem,
                        "template_content": match.read_text(encoding="utf-8"),
                        "reason": str(data.get("reason", ""))}
        except Exception as e:
            print(f"[{self.ENGINE_NAME}] 模板选择失败，使用通用模板: {e}")

        generic = next((p for p in templates if "通用" in p.stem), templates[0])
        return {"template_name": generic.stem,
                "template_content": generic.read_text(encoding="utf-8"),
                "reason": "LLM 选择失败，回退到通用模板"}

    # ---------- 生成 ----------
    def generate(self, query: str, task_id: str, engine_reports: dict,
                 forum_posts: list, arbitration: dict, persona: str = "") -> dict:
        out_dir = _ROOT / config.FINAL_REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{task_id}.html"

        material = self._build_material(query, engine_reports, forum_posts,
                                        arbitration, persona)
        template = self.select_template(query, material, persona)
        print(f"[{self.ENGINE_NAME}] 选中模板: {template['template_name']}")

        html_text = ""
        try:
            resp = self.llm.chat.completions.create(
                model=config.REPORT_MODEL_NAME,
                messages=[
                    {"role": "system", "content": HTML_PROMPT},
                    {"role": "user", "content":
                        f"报告模板章节结构（仅供参考）：\n{template['template_content'][:1500]}\n\n"
                        f"{material}"},
                ],
                max_tokens=config.REPORT_HTML_MAX_TOKENS,
                timeout=180,
            )
            html_text = _strip_code_fence(resp.choices[0].message.content)
        except Exception as e:
            print(f"[{self.ENGINE_NAME}] HTML 生成失败，使用兜底模板: {e}")

        if not _looks_like_html(html_text):
            html_text = self._render_fallback(query, engine_reports, arbitration,
                                              template["template_name"], task_id,
                                              persona)

        out_file.write_text(html_text, encoding="utf-8")

        meta = {
            "task_id": task_id,
            "query": query,
            "persona": persona,
            "template_used": template["template_name"],
            "selection_reason": template["reason"],
            "report_path": str(out_file),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        (out_dir / f"{task_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"[{self.ENGINE_NAME}] 最终报告已写入: {out_file}")
        return meta

    def _build_material(self, query, engine_reports, forum_posts, arbitration,
                        persona: str = "") -> str:
        parts = [f"用户问题：{query}",
                 f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
        if persona:
            parts.append(f"\n===== 用户画像 =====\n{persona}")
        for key in ("insight", "query", "media"):
            info = engine_reports.get(key) or {}
            content = (info.get("content") or "").strip()
            if not content:
                continue
            label = ENGINE_LABELS.get(key, key)
            parts.append(f"\n===== 专家报告：{label} =====\n"
                         + content[:config.REPORT_MAX_CHARS_PER_ENGINE])

        if forum_posts:
            lines = [f"- [{p.get('agent_name')}] {(p.get('content') or '')[:160]}"
                     for p in forum_posts[-12:]]
            parts.append("\n===== 论坛发言摘要 =====\n" + "\n".join(lines))

        if arbitration:
            parts.append(
                "\n===== 主持人仲裁 =====\n"
                f"共识：{arbitration.get('consensus', '')}\n"
                f"分歧：{'；'.join(arbitration.get('conflicts') or []) or '无'}\n"
                f"盲区：{'；'.join(arbitration.get('blind_spots') or []) or '无'}\n"
                f"主持词：{arbitration.get('host_speech', '')}"
            )
        return "\n".join(parts)

    # ---------- 兜底 HTML ----------
    def _render_fallback(self, query, engine_reports, arbitration,
                         template_name, task_id, persona: str = "") -> str:
        def esc(t):
            return html_lib.escape(t or "")

        def md_block(text):
            body = esc(text)
            body = re.sub(r"^### (.+)$", r"<h3>\1</h3>", body, flags=re.M)
            body = re.sub(r"^## (.+)$", r"<h2>\1</h2>", body, flags=re.M)
            body = re.sub(r"^# (.+)$", r"<h1>\1</h1>", body, flags=re.M)
            body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
            body = body.replace("\n\n", "</p><p>").replace("\n", "<br>")
            return f"<p>{body}</p>"

        cards = []
        if persona:
            cards.append(
                '<section class="card persona"><h2>用户画像</h2>'
                f"<p>{esc(persona)}</p></section>"
            )
        for key in ("insight", "query", "media"):
            info = engine_reports.get(key) or {}
            content = info.get("content") or ""
            if not content:
                continue
            cards.append(
                f'<section class="card"><h2>{esc(ENGINE_LABELS.get(key, key))}</h2>'
                f"{md_block(content)}</section>"
            )

        arb = ""
        if arbitration:
            conflicts = "；".join(arbitration.get("conflicts") or []) or "无"
            spots = "；".join(arbitration.get("blind_spots") or []) or "无"
            arb = (
                '<section class="card arb"><h2>主持人仲裁</h2>'
                f"<p><strong>共识：</strong>{esc(arbitration.get('consensus', ''))}</p>"
                f"<p><strong>分歧：</strong>{esc(conflicts)}</p>"
                f"<p><strong>盲区：</strong>{esc(spots)}</p>"
                f"<p>{esc(arbitration.get('host_speech', ''))}</p></section>"
            )

        return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>跑步训练分析报告 · {esc(task_id)}</title>
<style>
body{{margin:0;padding:24px;background:#f4f6fa;color:#1f2937;
 font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.7}}
main{{max-width:900px;margin:0 auto}}
header{{background:#fff;border-radius:14px;padding:24px;margin-bottom:18px;
 box-shadow:0 2px 8px rgba(0,0,0,.06)}}
h1{{font-size:24px;margin:0 0 8px}}
.meta{{color:#6b7280;font-size:13px}}
.card{{background:#fff;border-radius:14px;padding:24px;margin-bottom:16px;
 box-shadow:0 2px 8px rgba(0,0,0,.06)}}
.card h2{{font-size:18px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid #eef2f7}}
.card.arb{{border-left:4px solid #f59e0b}}
.card.persona{{border-left:4px solid #10b981}}
h3{{font-size:16px;margin:14px 0 6px}}
p{{font-size:14px;margin:8px 0}}
</style></head><body><main>
<header><h1>跑步训练综合分析报告</h1>
<div class="meta">问题：{esc(query)}｜模板：{esc(template_name)}｜
生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div></header>
{''.join(cards)}
{arb}
</main></body></html>"""


# ---------- 工具 ----------
def _parse_json(raw: str) -> dict:
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def _strip_code_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _looks_like_html(text: str) -> bool:
    low = (text or "").lower()
    return "<html" in low and "</html>" in low and len(text) > 800
