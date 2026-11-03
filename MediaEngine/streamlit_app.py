# -*- coding: utf-8 -*-
"""MediaEngine Streamlit 入口（Iter 1 独立界面）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from MediaEngine.agent import LogisticsIntelligenceAgent

st.set_page_config(page_title="MediaEngine · 实用情报", page_icon="🧭", layout="wide")
st.title("🧭 MediaEngine · 实用情报专家")
st.caption("基于博查多模态搜索：赛事 · 天气 · 装备 · 路线")

query = st.text_area("请输入实用情报问题", value="无锡马拉松有什么注意事项？", height=80)

if st.button("🚀 开始搜索", type="primary"):
    agent = LogisticsIntelligenceAgent()
    with st.spinner("MediaEngine 正在搜索实用情报...（约 30-60s）"):
        try:
            out_path = agent.run(query)
        except Exception as e:
            st.error(f"运行失败：{e}")
            st.stop()
    st.success(f"✅ 报告已生成：`{out_path}`")
    st.markdown("---")
    st.markdown(Path(out_path).read_text(encoding="utf-8"))
