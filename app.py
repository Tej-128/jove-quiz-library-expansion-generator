"""Hosted entry point for JoVE Quiz Library Expansion Generator.

Production generation intentionally runs through local_app.py so completed lesson
files can be written directly to the user's Windows output folder.
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="JoVE Quiz Library Expansion Generator",
    page_icon="JQ",
    layout="wide",
)

st.markdown(
    """
    <div style="max-width:900px;margin:8vh auto;padding:28px;border:1px solid #e4e7eb;border-left:6px solid #d71920;border-radius:10px;">
      <div style="font-size:2rem;font-weight:900;color:#d71920;">JoVE</div>
      <div style="font-size:1.3rem;font-weight:800;margin-top:8px;">Quiz Library Expansion Generator</div>
      <div style="margin-top:10px;color:#67707a;">
        Production batch generation now runs locally so every completed lesson workbook is saved directly to your Windows output folder and unfinished lessons can resume without redoing completed work.
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.info(
    "Use the local production build: download or clone the GitHub repository, "
    "then double-click RUN_LOCAL_WINDOWS.bat. The local Streamlit UI opens on "
    "http://127.0.0.1:8501."
)
st.code("RUN_LOCAL_WINDOWS.bat")
