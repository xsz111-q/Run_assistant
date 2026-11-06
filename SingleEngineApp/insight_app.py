# -*- coding: utf-8 -*-
"""InsightEngine 独立 Streamlit 页面（8501）"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import config
from SingleEngineApp.common import render_page
from InsightEngine.agent import SportsScientistAgent

render_page(
    agent_cls=SportsScientistAgent,
    engine_key="insight",
    title="📊 InsightEngine · 运动科学家",
    caption="基于本地训练数据（Excel 导入）做运动表现分析",
    default_query="如何提升 10 公里跑成绩？",
    port=config.STREAMLIT_INSIGHT,
)
