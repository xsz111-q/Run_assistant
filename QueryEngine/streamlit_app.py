# -*- coding: utf-8 -*-
"""QueryEngine Streamlit 入口（Iter 1 独立界面）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from QueryEngine.agent import TheoryExpertAgent

st.set_page_config(page_title="QueryEngine · 理论专家", page_icon="🎓", layout="wide")
st.title("🎓 QueryEngine · 运动科学理论专家")
st.caption("基于 Tavily 学术搜索做理论研究")

query = st.text_area("请输入研究问题", value="长距离跑步如何避免撞墙？", height=80)

if st.button("🚀 开始研究", type="primary"):
    agent = TheoryExpertAgent()
    with st.spinner("QueryEngine 正在进行学术搜索与分析...（约 30-60s）"):
        try:
            out_path = agent.run(query)
        except Exception as e:
            st.error(f"运行失败：{e}")
            st.stop()
    st.success(f"✅ 报告已生成：`{out_path}`")
    st.markdown("---")
    st.markdown(Path(out_path).read_text(encoding="utf-8"))
