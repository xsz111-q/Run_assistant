# -*- coding: utf-8 -*-
"""QueryEngine 独立 Streamlit 页面（8502）"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from SingleEngineApp.common import render_page
from QueryEngine.agent import TheoryExpertAgent

render_page(
    agent_cls=TheoryExpertAgent,
    engine_key="query",
    title="🎓 QueryEngine · 理论专家",
    caption="基于 Tavily 学术搜索做运动科学研究",
    default_query="长距离跑步如何避免撞墙？",
    port=config.STREAMLIT_QUERY,
)
