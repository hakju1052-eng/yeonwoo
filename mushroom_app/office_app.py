# -*- coding: utf-8 -*-
"""경영 자료 전용 앱 (v1.29.0) — 통합관리 시스템과 같은 PC 에서 다른 포트(기본 8502)로 따로 돈다.
경영 자료(3단 대조·팩스·입금 등)는 무거워서 본 앱과 나눠 두면 본 앱이 가벼워지고, 경영 자료를 오래 열어 두거나
다시 계산해도 현장 메뉴(개요·배치·살균 등)가 느려지지 않는다.
실행: streamlit run mushroom_app/office_app.py --server.port 8502   (serve_office.bat)
로그인은 본 앱과 같은 계정·같은 로그인 유지 쿠키를 쓴다(쿠키는 포트와 상관없이 같은 주소면 공유) — 관리자만.
"""
import os, sys, glob

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mushroom_app import config as C, auth
from mushroom_app import page_office as PO

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_eco_icon.png")
st.set_page_config(page_title=f"경영 자료 · {C.APP_NAME}", page_icon=ICON if os.path.exists(ICON) else "📊", layout="wide")


def _ver():
    fs = glob.glob(os.path.join(C.CACHE_DIR, "*.parquet"))
    return tuple(sorted((os.path.basename(f), int(os.path.getmtime(f))) for f in fs))


def main_url():
    """본 앱 주소 — 지금 들어온 주소에서 포트만 본 앱 포트로 바꾼다."""
    try:
        host = (st.context.headers or {}).get("host", "")
    except Exception:
        host = ""
    main_port = getattr(C, "MAIN_PORT", 8501)
    try:
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_port.txt")
        if os.path.exists(p):
            main_port = int(open(p).read().strip() or main_port)
    except Exception:
        pass
    name = host.rsplit(":", 1)[0] if host else "localhost"
    return f"http://{name}:{main_port}"


def main():
    C.ensure_dirs()
    me = auth.gate()
    with st.sidebar:
        if os.path.exists(ICON):
            c_ic, c_ti = st.columns([1, 3])
            c_ic.image(ICON, width=48)
            c_ti.markdown(f"### 경영 자료")
        else:
            st.markdown("### 📊 경영 자료")
        st.link_button("← 통합관리 시스템으로", main_url(), use_container_width=True)
        st.caption("경영 자료는 따로 떠 있는 화면입니다(본 앱이 느려지지 않게). 계정·로그인은 본 앱과 같습니다.")
        if not me.get("무인증"):
            st.divider(); auth.sidebar_box()
    if me.get("role") != "관리자" and not me.get("무인증"):
        st.error("관리자만 볼 수 있습니다."); return
    if not me.get("무인증") and not st.session_state.get("_office_logged"):
        st.session_state["_office_logged"] = True
        auth.log_access(me, "페이지", "경영 자료(따로 띄운 화면)")
    PO.render(_ver(), me)


main()
