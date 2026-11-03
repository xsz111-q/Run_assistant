# -*- coding: utf-8 -*-
"""InsightEngine Streamlit 入口（Iter 1 独立界面）

启动方式：streamlit run InsightEngine/streamlit_app.py --server.port 8501
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from InsightEngine.agent import SportsScientistAgent

st.set_page_config(page_title="InsightEngine · 运动数据分析", page_icon="📊", layout="wide")
st.title("📊 InsightEngine · 运动科学家")
st.caption("基于本地训练数据库做数据分析（MySQL · Keep）")

# 侧边栏：显示数据库状态
with st.sidebar:
    st.subheader("⚙️ 配置")
    if st.button("🔌 测试数据库连接"):
        try:
            agent = SportsScientistAgent()
            with agent.Session() as s:
                total = s.execute(
                    __import__("sqlalchemy").select(__import__("sqlalchemy").func.count()).
                    select_from(agent.__class__.__name__ and __import__("InsightEngine.db_models", fromlist=["TrainingRecordKeep"]).TrainingRecordKeep)
                ).scalar()
            st.success(f"✅ 连接正常，共 {total} 条 Keep 记录")
        except Exception as e:
            st.error(f"❌ 连接失败：{e}")

query = st.text_area("请输入分析问题", value="如何提升 10 公里跑成绩？", height=80)

if st.button("🚀 开始分析", type="primary"):
    agent = SportsScientistAgent()
    with st.spinner("InsightEngine 正在分析本地训练数据...（约 30-60s）"):
        try:
            out_path = agent.run(query)
        except Exception as e:
            st.error(f"运行失败：{e}")
            st.stop()
    st.success(f"✅ 报告已生成：`{out_path}`")
    st.markdown("---")
    st.markdown(Path(out_path).read_text(encoding="utf-8"))
