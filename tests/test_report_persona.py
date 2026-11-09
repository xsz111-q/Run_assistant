# -*- coding: utf-8 -*-
"""Issue #6 验收标准的自动化检查：画像进入报告材料与兜底 HTML。

不调用 LLM：只验证确定性拼装部分（材料头与兜底渲染）。
"""

from ReportEngine.agent import ReportAgent

PERSONA = "【用户画像】用户群体：青年跑者；目标：半马破 2"

ENGINE_REPORTS = {
    "insight": {"name": "运动科学家", "content": "历史跑量稳定上升"},
    "query": {"name": "理论专家", "content": "文献支持渐进超负荷"},
}


def make_agent():
    """跳过 __init__（不建 OpenAI 客户端），只测拼装逻辑。"""
    return ReportAgent.__new__(ReportAgent)


def test_material_contains_persona_section():
    """标准 2（材料侧）：画像作为独立区块进入报告材料。"""
    material = make_agent()._build_material(
        "如何提升 10 公里成绩", ENGINE_REPORTS, [], {}, persona=PERSONA)
    assert "===== 用户画像 =====" in material
    assert "半马破 2" in material


def test_material_without_persona_has_no_section():
    """无画像时不插入空区块。"""
    material = make_agent()._build_material(
        "如何提升 10 公里成绩", ENGINE_REPORTS, [], {})
    assert "===== 用户画像 =====" not in material


def test_material_includes_engine_reports_and_arbitration():
    """材料聚合：三份专家报告与 HOST 仲裁都在。"""
    arbitration = {"consensus": "先堆有氧", "conflicts": ["强度分歧"],
                   "blind_spots": ["恢复"], "host_speech": "主持词"}
    material = make_agent()._build_material(
        "如何提升 10 公里成绩", ENGINE_REPORTS, [], arbitration, persona=PERSONA)
    assert "历史跑量稳定上升" in material
    assert "文献支持渐进超负荷" in material
    assert "先堆有氧" in material
    assert "强度分歧" in material


def test_fallback_renders_persona_card():
    """标准 1（兜底路径）：LLM 失败时 HTML 仍有用户画像卡片。"""
    html = make_agent()._render_fallback(
        "如何提升 10 公里成绩", ENGINE_REPORTS, {}, "通用模板", "task001",
        persona=PERSONA)
    assert 'class="card persona"' in html
    assert "用户画像" in html
    assert "半马破 2" in html


def test_fallback_without_persona_has_no_persona_card():
    html = make_agent()._render_fallback(
        "如何提升 10 公里成绩", ENGINE_REPORTS, {}, "通用模板", "task001")
    assert 'class="card persona"' not in html
