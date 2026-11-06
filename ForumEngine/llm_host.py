# -*- coding: utf-8 -*-
"""论坛主持人 HOST —— 汇总三个引擎的发言

三个引擎产出报告后在论坛发言；全部完成后由 HOST 用 qwen-plus-latest
做一次汇总发言（共识 / 分歧 / 下一步建议），并写入论坛与 logs/host_speech.md。
"""

import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
import config

from openai import OpenAI
from ForumEngine.monitor import append_post, read_posts


SYSTEM_PROMPT = (
    "你是跑步训练多智能体系统中的总教练（HOST）。系统里有三位专家："
    "运动科学家（基于跑者的真实训练数据）、理论专家（基于运动科学文献）、"
    "实用情报专家（赛事/装备/路线/天气等落地信息）。"
    "请阅读他们的发言，输出一段 250~350 字的中文主持词，结构为："
    "①三方共识 ②存在的分歧或数据盲区 ③给跑者的下一步行动建议（3 条，用短句）。"
    "不要复述用户问题，不要编造数据，不要输出标题层级，只写正文。"
)


def _posts_for_task(posts, task_id: str) -> list:
    items = [p for p in posts if p.get("kind") == "report"]
    if task_id:
        scoped = [p for p in items if p.get("task_id") == task_id]
        if scoped:
            return scoped
    return items[-3:]


def _fallback_speech(items) -> str:
    names = "、".join(p.get("agent_name", p.get("agent", "")) for p in items) or "各引擎"
    return (f"{names}已完成本轮分析，相关内容已写入各自报告。"
            "建议先阅读三份报告中的数据结论，再结合自身训练安排取舍；"
            "若数据量不足，请先补充训练记录后再次提问。")


def generate_host_speech(posts=None, query: str = "", task_id: str = "") -> dict:
    """生成并记录 HOST 发言，返回 {content, path, post}"""
    posts = posts if posts is not None else read_posts()
    items = _posts_for_task(posts, task_id)
    if not items:
        return {"content": "", "path": "", "post": None}

    digest = "\n\n".join(
        f"【{p.get('agent_name', p.get('agent'))}】\n{(p.get('content') or '')[:600]}"
        for p in items
    )

    try:
        client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)
        resp = client.chat.completions.create(
            model=config.DEFAULT_MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content":
                    (f"用户问题：{query}\n\n" if query else "") + f"各引擎发言：\n\n{digest}"},
            ],
            timeout=90,
        )
        content = resp.choices[0].message.content.strip()
    except Exception as e:
        content = _fallback_speech(items) + f"\n\n（HOST 调用失败：{type(e).__name__}）"

    out = _ROOT / config.HOST_SPEECH_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                f"{' · ' + task_id if task_id else ''}\n\n{content}\n")

    post = append_post(
        agent="host",
        content=content,
        task_id=task_id,
        kind="host_speech",
        round_no=1,
        report_path=str(out),
    )
    return {"content": content, "path": str(out), "post": post}


if __name__ == "__main__":
    print(generate_host_speech()["content"])
