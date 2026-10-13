# -*- coding: utf-8 -*-
"""'반가공 현황' 페이지 — 반가공센터(과자공장) 업무 현황판.

화면 본체는 banagong_board/index.html (HTML·JS 컴포넌트)이고, 이 파일은
  1) etl_banagong 이 만든 JSON(app_cache\\banagong\\*.json)을 읽어 컴포넌트에 넘기고
  2) '지금 다시 읽기' 버튼(복사 → 다시 읽기)을 그린다.
자료는 refresh_all.bat(매일 09:00·15:00)가 copy_banagong.bat → etl_cli(반가공 단계) 순서로 갱신한다.
"""
import os, glob, json, time, subprocess
import streamlit as st
import streamlit.components.v1 as components

from . import config as C, etl_banagong

_HERE = os.path.dirname(os.path.abspath(__file__))
_BOARD = components.declare_component("banagong_board", path=os.path.join(_HERE, "banagong_board"))
DOC_KEYS = {"meta": "meta__info", "prod": "prod__summary", "daily": "prod__daily", "sales": "sales__all",
            "purchase": "purchase__all", "ledgers": "stock__ledgers", "labor": "labor__all",
            "boiler": "equip__boiler", "reports": "reports__all"}


def _sig():
    """캐시 JSON 들의 수정시각 → 다시 읽을지 판단하는 키."""
    fs = glob.glob(os.path.join(C.BANAGONG_CACHE, "*.json"))
    return tuple(sorted((os.path.basename(f), int(os.path.getmtime(f))) for f in fs))


@st.cache_data(max_entries=2, show_spinner=False)
def _load(sig):
    d = {}
    for key, name in DOC_KEYS.items():
        p = os.path.join(C.BANAGONG_CACHE, name + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d[key] = json.load(f)
    return json.dumps(d, ensure_ascii=False)


def _copy_now():
    """copy_banagong.bat 로 업무자료 → 사본 폴더 복사. (성공 여부, 안내)"""
    bat = C.BANAGONG_COPY_BAT
    if not os.path.exists(bat):
        return False, f"복사 배치 파일이 없습니다: {bat}"
    try:
        r = subprocess.run(["cmd", "/c", bat, "/auto"], capture_output=True, timeout=600)
    except Exception as e:
        return False, f"복사 실패: {e} — 전에 복사해 둔 사본으로 다시 읽었습니다"
    code = r.returncode
    if code < 8:
        return True, "업무자료 복사 완료"
    why = "업무자료 폴더를 찾지 못함" if code == 9 else "일부 파일 복사 실패"
    return False, (f"복사 문제(종료코드 {code}, {why}) — 전에 복사해 둔 사본으로 다시 읽었습니다. "
                   "기록: `Downloads\\반가공자료\\_copy_log.txt`")


def render(me=None):
    admin = (me or {}).get("role", "관리자") == "관리자"     # 직원도 화면은 똑같이 본다(금액 포함, v1.21.3)
    sig = _sig()
    data = _load(sig) if sig else ""
    _BOARD(data=data, stamp="|".join(f"{n}:{t}" for n, t in sig), admin=admin, key="banagong_board", default=None)
    meta = json.loads(data).get("meta", {}) if data else {}

    if not admin:   # '지금 다시 읽기'(복사·다시 읽기)는 관리자만 — 데이터 관리 페이지와 같은 기준
        st.caption(f"마지막 갱신 {meta.get('updatedAt', '없음')} · 자동 갱신 매일 09:00·15:00")
        return

    with st.expander("자료 갱신 · 지금 다시 읽기", expanded=not sig):
        st.caption(
            f"마지막 갱신 {meta.get('updatedAt', '없음')} · 자동 갱신 매일 09:00·15:00 (refresh_all.bat) · "
            f"사본 폴더 {etl_banagong.source_dir() or '없음'}")
        c1, c2 = st.columns([1, 3])
        do_copy = c2.checkbox("업무자료에서 먼저 복사하기 (Y: → 다운로드\\반가공자료)", value=True, key="bng_copy")
        if c1.button("지금 다시 읽기", key="bng_reload"):
            msgs = []
            with st.spinner("반가공 자료를 읽는 중… (1분쯤)"):
                t0 = time.time()
                if do_copy:
                    ok, msg = _copy_now()
                    msgs.append(msg)
                n = etl_banagong.run(progress=lambda m: msgs.append(m))
                msgs.append(f"{time.time() - t0:.0f}초")
            _load.clear()
            st.session_state["bng_msgs"] = msgs
            st.rerun()
        for m in st.session_state.get("bng_msgs", []):
            st.caption(m)
