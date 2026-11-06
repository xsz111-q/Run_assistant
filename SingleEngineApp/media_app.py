# -*- coding: utf-8 -*-
"""MediaEngine 独立 Streamlit 页面（8503）"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from SingleEngineApp.common import render_page
from MediaEngine.agent import LogisticsIntelligenceAgent

render_page(
    agent_cls=LogisticsIntelligenceAgent,
    engine_key="media",
    title="🧭 MediaEngine · 实用情报专家",
    caption="基于博查多模态搜索：赛事 · 天气 · 装备 · 路线",
    default_query="无锡马拉松有什么注意事项？",
    port=config.STREAMLIT_MEDIA,
)
