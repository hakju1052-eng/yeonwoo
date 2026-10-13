# -*- coding: utf-8 -*-
"""통합관리 시스템 (옛 이름: 스마트팜 분석 시스템) by p.h.j (Streamlit).
실행: streamlit run app.py --server.address 0.0.0.0 --server.port 8501
"""
import os, sys, glob, json, time, re
import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mushroom_app import config as C, etl_env, etl_logs, etl_retort, analysis as A, ai_chat, report as RP, auth, batch_report as BR
import streamlit.components.v1 as components
from mushroom_app import page_banagong as PB      # 반가공센터 현황 (v1.21.0)
# 경영 자료(page_office)는 v1.29.0 부터 따로 띄운 앱(office_app.py, 포트 8502)에서 연다 — 필요할 때만 여기서 불러온다

# Streamlit 1.47+ 는 use_container_width 대신 width="stretch" 를 쓴다 (구버전 호환)
try:
    from packaging.version import Version as _V
    _FULL = {"width": "stretch"} if _V(st.__version__) >= _V("1.47") else {"use_container_width": True}
except Exception:
    _FULL = {"use_container_width": True}

ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_eco_icon.png")      # eco 로고(포장 디자인에서 추출)
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets_eco_teal.png")
st.set_page_config(page_title=C.APP_NAME, page_icon=ICON if os.path.exists(ICON) else "🍄", layout="wide")
alt.data_transformers.disable_max_rows()
PAL = {"실측": "#2a78d6", "설정": "#8a8f84", "비교": "#eb6834", "보조": "#1baf7a"}


# ───────────────────────── 데이터 로딩 ─────────────────────────
def _ver():
    """캐시 파일들의 수정시각 → 캐시 키."""
    fs = glob.glob(os.path.join(C.CACHE_DIR, "*.parquet"))
    return tuple(sorted((os.path.basename(f), int(os.path.getmtime(f))) for f in fs))


@st.cache_data(show_spinner="환경 데이터 읽는 중…")
def load_env(ver):
    return A.env_daily()


@st.cache_data(show_spinner="팽이 배치 사슬 만드는 중…")
def load_paengi(ver):
    env = load_env(ver)
    K = A.paengi_chain()
    PE = A.paengi_batch_env(K, env) if not K.empty else pd.DataFrame()
    return K, PE


@st.cache_data(show_spinner="새송이 배치 만드는 중…")
def load_ss(ver):
    env = load_env(ver)
    B = A.ss_batches()
    SE = A.ss_batch_env(B, env) if not B.empty else pd.DataFrame()
    return B, SE


@st.cache_data(show_spinner=False)
def load_monthly(ver):
    B, _ = load_ss(ver)
    return A.paengi_monthly(), A.ss_monthly(B), A.cult_monthly()


@st.cache_data(show_spinner=False)
def load_retort_vs_scrape(ver):
    return A.retort_vs_scrape()


@st.cache_data(show_spinner=False)
def load_weather(ver):
    return A.weather_daily()


@st.cache_data(show_spinner=False)
def load_sales(ver):
    return A.sales_monthly()


@st.cache_data(show_spinner=False)
def load_loads(ver):
    return A.loads_with_cycles()


@st.cache_data(show_spinner="배치 전 공정 표 만드는 중…")
def load_stage_table(ver):
    env = load_env(ver); K, PE = load_paengi(ver)
    return A.paengi_stage_table(K, PE, env)


@st.cache_data(show_spinner="공정 경고 확인 중…")
def load_process_alerts(ver, d1, d2):
    return A.process_alerts(d1, d2)


@st.cache_data(show_spinner="솥 단위 기록 읽는 중…")
def load_loads_cycles(ver):
    return A.loads_with_cycles()


@st.cache_resource(max_entries=30, show_spinner="배치 자료 모으는 중…")
def load_batch_report(ver, bid, basis="같은 계절"):
    """배치 보고서에 쓸 자료 묶음. 최근 30개를 붙들어 둔다(화면 보기·PDF 둘 다 이걸 쓴다). basis = 이슈 판정 비교 기준"""
    K, PE = load_paengi(ver)
    return BR.gather(bid, K=K, ST=load_stage_table(ver), env=load_env(ver), PE=PE, loads=load_loads_cycles(ver), basis=basis)


def batch_report_box(ver, bid, preview=True, where="dossier", basis="같은 계절"):
    """PDF 만들기 → 내려받기 (+ 닫기). preview=True 면 쪽별 미리보기도 보인다. 화면에는 이미 같은 내용이 있으므로 보통 False."""
    key = f"_brep_{bid}_{basis}"
    c1, c2, c3 = st.columns([1.3, 1, 3])
    if c1.button("PDF 만들기 (인쇄용)", key=f"brep_btn_{where}_{bid}", type="primary",
                 help="원재료 → 입병 → 살균 → 냉각 → 접종 → 배양 → 균긁기 → 발이실 → 권지 → 생육실 → 수확 순서로 A4 PDF. 표·그래프가 쪽에 걸쳐 잘리지 않게 배치"):
        with st.spinner("PDF 만드는 중…"):
            st.session_state[key] = BR.build(bid, G=load_batch_report(ver, bid, basis))
    r = st.session_state.get(key)
    if r:
        c2.download_button("PDF 내려받기", data=r["pdf"], file_name=f"배치보고서_{bid}.pdf", mime="application/pdf", key=f"brep_dl_{where}_{bid}")
        if c3.button("닫기", key=f"brep_close_{where}_{bid}"):
            del st.session_state[key]; st.rerun()
        if preview:
            if r["issues"]:
                st.caption("이슈 " + " · ".join(f"{st_}: {len([1 for a, _ in r['issues'] if a == st_])}건" for st_ in dict.fromkeys(a for a, _ in r["issues"])))
            for png in r.get("pngs", []):
                st.image(png, use_container_width=True)
            st.caption(f"{r['pages']}쪽 · 화면은 미리보기이고, 인쇄나 보관은 'PDF 내려받기'로 받은 파일을 쓰세요.")
        else:
            st.caption(f"A4 {r['pages']}쪽으로 만들었습니다. 'PDF 내려받기'로 받아서 인쇄하거나 보관하세요.")


@st.cache_data(show_spinner="종균·액탱크 자료 만드는 중…")
def load_tanks(ver):
    return A.tank_batches()


@st.cache_data(show_spinner=False)
def load_retort(ver):
    return etl_retort.load_daily()


@st.cache_data(show_spinner=False)
def load_retort_cycles(ver):
    return etl_retort.load_cycles()


@st.cache_data(show_spinner=False, max_entries=40)
def load_retort_1min(ver, units, d1, d2):
    """필요한 솥·날짜만 parquet 에서 바로 읽는다.
    예전에는 6개월치 전체(80만 행·50MB)를 통째로 올려놓고 잘라 썼는데, 화면을 새로 그릴 때마다
    그 표를 통째로 복사하느라 살균 페이지가 눌렸다. 지금은 하루치 3천 행만 읽는다."""
    if not units:
        return pd.DataFrame()
    return etl_retort.load_1min(units=list(units), d1=d1, d2=d2)


@st.cache_data(show_spinner="살균 원본 읽는 중…")
def load_retort_day(ver, unit, d):
    return etl_retort.load_day(unit, d)


@st.cache_data(show_spinner="이상 항목 찾는 중…")
def load_anomalies(ver, days):
    return A.anomalies(load_env(ver), days=days)


def f(v, d=0, unit=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{d}f}{unit}"




# ───────────────────────── 사건 표시 ─────────────────────────
def events_df(d1=None, d2=None):
    E = pd.DataFrame(C.EVENTS, columns=["날짜", "사건", "설명"]); E["날짜"] = pd.to_datetime(E["날짜"])
    E = E.sort_values("날짜").reset_index(drop=True); E.insert(0, "번호", range(1, len(E) + 1))
    if d1 is not None:
        E = E[E["날짜"] >= pd.Timestamp(d1)]
    if d2 is not None:
        E = E[E["날짜"] <= pd.Timestamp(d2)]
    return E


def event_layers(x_field="날짜", d1=None, d2=None, temporal=True, months=False, gap_days=10):
    """추이 그래프에 겹칠 사건 점선+라벨. months=True 면 x가 'YYYY-MM' 문자열 축. 가까운 사건은 라벨을 하나로 묶는다."""
    E = events_df(d1, d2).sort_values("날짜")
    if E.empty:
        return []
    groups, cur = [], []
    for _, r in E.iterrows():
        key = r["날짜"].strftime("%Y-%m") if months else r["날짜"]
        if cur and ((months and key == cur[-1]["날짜"].strftime("%Y-%m")) or (not months and (r["날짜"] - cur[0]["날짜"]).days <= gap_days)):
            cur.append(r)
        else:
            if cur:
                groups.append(cur)
            cur = [r]
    if cur:
        groups.append(cur)
    L = pd.DataFrame([{"x": (g[0]["날짜"].strftime("%Y-%m") if months else g[0]["날짜"]),
                       "라벨": "·".join(str(r["번호"]) for r in g),
                       "내용": " / ".join(f"{r['번호']} {r['날짜']:%m/%d} {r['사건']}" for r in g)} for g in groups])
    E = E.assign(x=E["날짜"].dt.strftime("%Y-%m") if months else E["날짜"])
    enc = "x:N" if months else "x:T"
    rule = alt.Chart(E).mark_rule(color="#9c5fd6", strokeDash=[4, 3], opacity=0.6).encode(x=enc, tooltip=[alt.Tooltip("날짜:T", format="%m/%d"), "사건:N", "설명:N"])
    text = alt.Chart(L).mark_text(align="left", baseline="top", dx=3, dy=2, fontSize=11, fontWeight="bold", color="#7a48b8").encode(x=enc, y=alt.value(0), text="라벨:N", tooltip=["내용:N"])
    return [rule, text]


# ───────────────────────── 차트 ─────────────────────────
# 물방울 모양 (SVG path, 배치 탐색기 습도 그래프의 비 온 날 표시)
RAIN_DROP = "M0,-1 C0.45,-0.45 0.62,-0.1 0.62,0.3 A0.62,0.62 0 1,1 -0.62,0.3 C-0.62,-0.1 -0.45,-0.45 0,-1 Z"


def curve_chart(series, y_title, y_domain=None, rules=None, height=230, x_title="입상 후 일차", marks=None, repairs=None):
    """series: [(이름, DataFrame[cday, y], 색, 점선여부)] → 겹친 선 그래프. marks: DataFrame[cday, label] 를 x축 바로 위에 동그라미로 (비 온 날).
    repairs: DataFrame[cday, label] — 수리한 날을 주황 세로선과 🔧 로 (v1.23.0)"""
    frames = []
    for name, df, color, dash in series:
        if df is None or df.empty:
            continue
        x = df[["cday", "y"]].dropna().copy(); x["계열"] = name; x["_c"] = color; x["_d"] = "dash" if dash else "solid"
        frames.append(x)
    if not frames:
        return None
    data = pd.concat(frames)
    dom = list(dict.fromkeys(data["계열"]))
    rng = [data[data["계열"] == k]["_c"].iloc[0] for k in dom]
    dashes = [[6, 4] if data[data["계열"] == k]["_d"].iloc[0] == "dash" else [1, 0] for k in dom]
    # 구간 라벨이 선과 겹치지 않도록 위쪽에 여유(12%)를 둔다
    if y_domain:
        lo, hi = y_domain
    else:
        lo, hi = float(data.y.min()), float(data.y.max())
        if hi <= lo:
            lo, hi = lo - 1, hi + 1
        lo, hi = lo - 0.05 * (hi - lo), hi + 0.02 * (hi - lo)
    if rules:
        hi = hi + (0.22 if height < 200 else 0.12) * (hi - lo)
    y = alt.Y("y:Q", title=y_title, scale=alt.Scale(domain=[lo, hi], nice=False))
    ch = alt.Chart(data).mark_line(strokeWidth=2.2, interpolate="monotone").encode(
        x=alt.X("cday:Q", title=x_title), y=y,
        color=alt.Color("계열:N", scale=alt.Scale(domain=dom, range=rng), legend=alt.Legend(orient="top", title=None)),
        strokeDash=alt.StrokeDash("계열:N", scale=alt.Scale(domain=dom, range=dashes), legend=None),
        tooltip=[alt.Tooltip("계열:N"), alt.Tooltip("cday:Q", title="일차"), alt.Tooltip("y:Q", title=y_title, format=".1f")])
    layers = [ch]
    if rules:
        rd = pd.DataFrame([{"x": v, "label": k} for k, v in rules.items() if v is not None and not pd.isna(v)])
        if not rd.empty:
            layers.append(alt.Chart(rd).mark_rule(color="#9aa08f", strokeDash=[3, 3]).encode(x="x:Q"))
            # 오른쪽 끝 근처의 라벨은 선 왼쪽에 써서 잘리지 않게 한다
            xmax, xmin = data.cday.max(), data.cday.min()
            rd["right"] = rd.x > xmin + 0.8 * (xmax - xmin)
            for right, sub in rd.groupby("right"):
                layers.append(alt.Chart(sub).mark_text(align="right" if right else "left", baseline="top", dx=-4 if right else 4, dy=4,
                                                       color="#6b7160", fontSize=11).encode(x="x:Q", y=alt.value(0), text="label:N"))
    if marks is not None and len(marks):
        md = marks.copy(); md["y"] = lo + 0.03 * (hi - lo)
        layers.append(alt.Chart(md).mark_point(shape=RAIN_DROP, size=160, color="#2a78d6", strokeWidth=1, filled=True, opacity=0.85).encode(
            x="cday:Q", y="y:Q", tooltip=[alt.Tooltip("cday:Q", title="일차"), alt.Tooltip("label:N", title="비")]))
    if repairs is not None and len(repairs):
        xmin, xmax = data.cday.min(), data.cday.max()
        rp = repairs[(repairs.cday >= xmin) & (repairs.cday <= xmax)].copy()
        if len(rp):
            rp["y"] = lo + 0.1 * (hi - lo)
            tip = [alt.Tooltip("cday:Q", title="일차"), alt.Tooltip("label:N", title="수리")]
            layers.append(alt.Chart(rp).mark_rule(color="#e08a00", strokeWidth=1.6, opacity=0.7).encode(x="cday:Q", tooltip=tip))
            layers.append(alt.Chart(rp).mark_text(text="🔧", fontSize=13).encode(x="cday:Q", y="y:Q", tooltip=tip))
    return (alt.layer(*layers).properties(height=height, padding={"left": 8, "right": 12, "top": 4, "bottom": 4})
            .configure_axis(titleFontSize=12, labelFontSize=11, titlePadding=8).interactive(bind_y=False))


def prof_series(pe, bids, name, color, col, dash=False, max_day=None, min_day=None):
    p = A.profile(pe, bids, max_day, min_day)
    if p.empty:
        return None
    return (name, p.rename(columns={col: "y"}), color, dash)


# ───────────────────────── 페이지: 개요 ─────────────────────────
def page_overview(ver):
    env = load_env(ver)
    small = f'<span style="font-size:0.42em;font-weight:400;color:#8a8f87;margin-left:14px;white-space:nowrap">by {C.APP_AUTHOR} &nbsp; v{C.VERSION}</span>'
    if os.path.exists(ICON):
        c_ic, c_ti = st.columns([1, 14])
        c_ic.image(ICON, width=72)
        c_ti.markdown(f'<h1 style="margin:0;padding:0.35em 0 0 0">{C.APP_NAME}{small}</h1>', unsafe_allow_html=True)
    else:
        st.markdown(f'<h1 style="margin:0">🍄 {C.APP_NAME}{small}</h1>', unsafe_allow_html=True)
    if env.empty:
        st.warning("아직 환경 데이터 캐시가 없습니다. 왼쪽 **데이터 관리**에서 '환경 데이터 갱신'을 먼저 실행하세요.")
        return
    last = env.date.max()
    K, PE = load_paengi(ver); B, SE = load_ss(ver)
    pm, sm, cm = load_monthly(ver)
    an = load_anomalies(ver, 14)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("환경 데이터 최신", last.strftime("%m/%d")); c1.caption(f"{env.room.nunique()}개 방")
    c2.metric("팽이 배치(완결)", f"{int(K['완결'].sum()) if not K.empty else 0}"); c2.caption(f"전체 {len(K)}")
    c3.metric("새송이 배치(완결)", f"{int(B['완결'].sum()) if not B.empty else 0}"); c3.caption(f"전체 {len(B)}")
    hi = int((an["심각도"] == "높음").sum()) if not an.empty else 0
    c4.metric("이상 항목(14일)", f"{len(an)}", f"높음 {hi}", delta_color="inverse")
    if not pm.empty and "병당중량" in pm:
        r = pm.dropna(subset=["병당중량"]).iloc[-1]
        qc = r.get("QC(서식B)", np.nan) if pd.notna(r.get("QC(서식B)", np.nan)) else r.get("QC(서식A)", np.nan)
        c5.metric(f"팽이 병당중량 {r['월'][5:]}월", f(r["병당중량"], 1, "g")); c5.caption(f"QC {f(qc, 1)}점")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("월별 결과")
        tabs = st.tabs(["팽이", "새송이", "배양실"])
        with tabs[0]:
            if not pm.empty:
                st.dataframe(pm.iloc[::-1].style.format({c: "{:,.0f}" for c in ("생산kg", "입상병수")} | {c: "{:.1f}" for c in pm.columns if c.startswith(("병당", "QC"))} | {"상등급비중": "{:.2f}"}),
                             hide_index=True, **_FULL)
        with tabs[1]:
            if not sm.empty:
                st.dataframe(sm.iloc[::-1].style.format({c: "{:,.0f}" for c in ("생산kg", "입상병수", "배치병수", "병당g")} | {c: "{:.1f}" for c in ("파지%", "미니%", "깎기%", "수확소요일", "재입상간격")}),
                             hide_index=True, **_FULL)
                st.caption("병당g = 그 달 생산 kg ÷ 그 달 수확 시작한 배치의 입상 병수. 1월은 12월 입상분이 섞여 참고치.")
        with tabs[2]:
            if not cm.empty:
                pv = cm.pivot(index="m", columns="항목", values="비율%").round(2)
                st.dataframe(pv.tail(12).iloc[::-1], **_FULL)
    with right:
        st.subheader("최근 14일 이상 항목")
        (rtab,) = st.tabs(["심각도·분류별 건수"])          # 왼쪽 탭과 같은 높이에서 표가 시작되도록 탭으로 감싼다
        with rtab:
            if an.empty:
                st.success("감지된 항목이 없습니다.")
            else:
                cnt = an.groupby(["심각도", "분류"]).size().reset_index(name="건수")
                st.dataframe(cnt, hide_index=True, **_FULL)
                st.caption("자세한 내용은 왼쪽 **이상 감시** 페이지")


# ───────────────────────── 페이지: 배치 탐색기 ─────────────────────────
def page_batches(ver, crop):
    st.title(f"배치 탐색기 — {crop}")
    env = load_env(ver)
    if crop == "팽이":
        K, PE = load_paengi(ver)
        if K.empty:
            st.info("팽이 배치가 없습니다. 배양소관리(권지 시트)와 팽이 일지가 필요합니다."); return
        T = K.copy()
        T["방"] = T["생육실"].map(lambda r: f"생육실{int(r):02d}")
        T["발이"] = T["발이실"].map(lambda r: f"발이실{int(r):02d}" if pd.notna(r) else "—")
        show = T[["bid", "종합평가", "계절상위%", "종합점수", "입상일", "발이", "권지일", "방", "수확시작", "총일수", "병당중량_표시", "중량기준", "QC", "QC판정", "긁기불량률%", "권지불량률%", "season"]].rename(
            columns={"bid": "배치", "계절상위%": "계절 내 위치", "방": "생육실", "발이": "발이실", "병당중량_표시": "병당중량", "QC판정": "QC 판정", "season": "계절"})
        day_max, phase_rule = 30, "권지일"
        day_min = -30
    else:
        B, PE = load_ss(ver)
        if B.empty:
            st.info("새송이 배치가 없습니다. 새송이 일일작업일지가 필요합니다."); return
        T = B.copy()
        show = T[[c for c in ["bid", "종합평가", "계절상위%", "종합점수", "room", "입상일", "뒤집기_d", "솎기_d", "수확시작_d", "수확일수", "재입상_d", "bottles", "수확kg", "병당g", "미니비중%", "QC종합", "season"] if c in T]].rename(
            columns={"bid": "배치", "계절상위%": "계절 내 위치", "room": "방", "뒤집기_d": "뒤집기일차", "솎기_d": "솎기일차", "수확시작_d": "수확시작일차", "재입상_d": "재입상일차", "bottles": "입상병수", "season": "계절", "QC종합": "QC(50)"})
        day_max, phase_rule = 16, None
        day_min = -45

    with st.expander("필터", expanded=False):
        c1, c2, c3 = st.columns(3)
        seasons = c1.multiselect("계절", ["겨울", "봄", "여름", "가을"], default=[])
        rooms = c2.multiselect("방", sorted(show["생육실" if crop == "팽이" else "방"].unique()), default=[])
        only_done = c3.checkbox("수확 완료된 배치만", value=True)
        c4, c5 = st.columns([1, 2])
        grade = c4.multiselect("종합평가", ["좋음", "보통", "미흡", "대기"], default=[],
                               help="QC·불량률·병당중량을 같은 비중으로 같은 계절 배치와 견준 등급. 좋음 = 상위 1/4, 미흡 = 하위 1/4. 셋 중 하나라도 없으면 '—'. "
                                    "'계절 내 위치'는 점수가 아니라 같은 계절 배치 중 몇 % 안에 드는지입니다(상위 3% = 아주 좋음, 상위 97% = 하위 3%).")
        order = c5.radio("정렬", ["최근 입상 순", "좋은 순", "나쁜 순"], horizontal=True, key="bx_order")
    mask = pd.Series(True, index=show.index)
    if seasons:
        mask &= show["계절"].isin(seasons)
    if rooms:
        mask &= show["생육실" if crop == "팽이" else "방"].isin(rooms)
    if only_done:
        mask &= T["완결"]
    if grade:
        mask &= show["종합평가"].isin(grade)
    show = show[mask]
    if order == "좋은 순":
        show = show.sort_values(["종합점수", "입상일"], ascending=[False, False], na_position="last")
    elif order == "나쁜 순":
        show = show.sort_values(["종합점수", "입상일"], ascending=[True, False], na_position="last")
    else:
        show = show.sort_values("입상일", ascending=False)
    show = show.drop(columns=["종합점수"])
    n_g = int((show["종합평가"] == "좋음").sum())
    st.caption(f"{len(show)}개 배치 (좋음 {n_g}개) — 행을 클릭하면 아래에 상세가 나옵니다. '계절 내 위치'는 QC·불량률·병당중량을 합쳐 같은 계절(같은 QC서식) 배치 중 몇 % 안에 드는지(상대), 'QC 판정'은 품질 기준서 앵커 기준(절대)입니다.")
    ev = st.dataframe(show, hide_index=True, **_FULL, height=300, on_select="rerun", selection_mode="single-row",
                      column_config={"계절 내 위치": st.column_config.NumberColumn(format="상위 %d%%", help="같은 계절·같은 QC서식의 수확 완료 배치 중 몇 % 안에 드는지 (QC·불량률·병당중량 합산). 작을수록 좋음"),
                                     "QC 판정": st.column_config.TextColumn(help="품질 기준서(2026-06-19 서식) 항목 앵커로 계산한 절대 판정: 85 이상 양호 · 79 이상 보통 · 그 미만 미흡. 2026-06-18 이전 구서식 배치는 '—'"),
                                     "입상일": st.column_config.DateColumn(format="YYYY-MM-DD"), "권지일": st.column_config.DateColumn(format="YYYY-MM-DD"),
                                     "수확시작": st.column_config.DateColumn(format="YYYY-MM-DD"), "병당중량": st.column_config.NumberColumn(format="%.1f"),
                                     "QC": st.column_config.NumberColumn(format="%.1f"), "긁기불량률%": st.column_config.NumberColumn(format="%.2f"),
                                     "권지불량률%": st.column_config.NumberColumn(format="%.2f"), "입상병수": st.column_config.NumberColumn(format="%d"),
                                     "수확kg": st.column_config.NumberColumn(format="%.0f"), "병당g": st.column_config.NumberColumn(format="%.0f"), "미니비중%": st.column_config.NumberColumn(format="%.0f"),
                                     "QC(50)": st.column_config.NumberColumn(format="%.0f")})
    sel = ev.selection.rows if ev and ev.selection else []
    bid = show.iloc[sel[0]]["배치"] if sel else (show.iloc[0]["배치"] if len(show) else None)
    if bid is None:
        return
    b = T[T.bid == bid].iloc[0]
    st.session_state["_ctx_batch"] = bid          # 아래 질문창이 "이 배치"를 알아듣게
    st.divider()
    st.subheader(f"배치 {bid}")
    if str(b.get("종합평가", "—")) != "—":
        st.caption(f"종합평가 **{b['종합평가']}** (같은 계절 상위 {b['계절상위%']:.0f}%) — {b.get('평가근거', '')}"
                   + (f" · QC 절대 판정 **{b['QC판정']}** (기준서 앵커: 85 양호 / 79 보통)" if str(b.get('QC판정', '—')) != '—' else ""))

    # 상세: 배치 보고서를 화면 형태로 (원재료 → 입병 → 살균 → 냉각 → 접종 → 배양 → 균긁기 → 발이실 → 권지 → 생육실 → 수확). PDF는 배치 이력에서.
    if crop == "팽이":
        BR.render_streamlit(bid, G=load_batch_report(ver, bid))
    else:
        cols = st.columns(6)
        M = lambda c, l, v, d=None: (c.metric(label=l, value=v), c.caption(d) if d else None)
        M(cols[0], "입상", b["입상일"].strftime("%m/%d"), f"{f(b['bottles'])}병")
        M(cols[1], "뒤집기", (f(b["뒤집기_d"]) + "일차") if pd.notna(b["뒤집기_d"]) else "—")
        M(cols[2], "솎기", (f(b["솎기_d"]) + "일차") if pd.notna(b["솎기_d"]) else "—")
        M(cols[3], "수확", (f(b["수확시작_d"]) + "일차") if pd.notna(b["수확시작_d"]) else "—", f"{f(b['수확일수'])}일간")
        M(cols[4], "탈병", (f(b["탈병_d"]) + "일차") if pd.notna(b["탈병_d"]) else "—")
        M(cols[5], "재입상", (f(b["재입상_d"]) + "일차") if pd.notna(b["재입상_d"]) else "—")
        if pd.notna(b.get("접종일")):
            st.caption(f"배양: 접종 {b['접종일'].strftime('%m/%d')} → 균긁기 {b['긁기일'].strftime('%m/%d')} ({f(b.get('배양일수'))}일) · 배양출고 {f(b.get('배양출고_방'))}병 · 긁기 불량 {f(b.get('긁기불량_방'))}병")
        if pd.notna(b.get("수확kg")):
            c1_, c2_, c3_ = st.columns(3)
            src = "방별 포장량 자료" if pd.notna(b.get("미니비중%")) else "실별QC(정규철 생산량)"
            M(c1_, "이 방 수확량", f(b["수확kg"], 0, "kg"), src)
            M(c2_, "병당 수율", f(b["병당g"], 0, "g"), f"입상 {f(b.get('입상병수_방별') if pd.notna(b.get('입상병수_방별')) else b['bottles'])}병 기준")
            if pd.notna(b.get("미니비중%")):
                M(c3_, "미니 비중", f(b["미니비중%"], 0, "%"), f"미니 {f(b['미니kg'], 0, 'kg')}")
            elif pd.notna(b.get("QC종합")):
                M(c3_, "QC 종합", f(b["QC종합"], 0, "/50"), "생육·대모양·갓상태·병해·생산량 합계")
        else:
            st.caption("방별 수확량 없음 — 방별 포장량(2025-10\\~2026-04)·실별QC(2026-03\\~) 어느 쪽에도 이 배치가 없습니다.")
        if pd.notna(b.get("QC종합")):
            qc = [(n, b.get(n)) for n in A.SS_QC_COLS if pd.notna(b.get(n))]
            if qc:
                st.caption("QC " + f(b["QC종합"], 0) + "/50 — " + " · ".join(f"{n.replace('생산량점','생산량')} {v:.0f}" for n, v in qc))
        memo = " / ".join(str(b.get(k)) for k in ("발이메모", "QC비고") if b.get(k) and str(b.get(k)) not in ("nan", "None"))
        if memo:
            st.caption("현장 메모: " + memo)

    # 비교 대상
    c1, c2 = st.columns([1, 3])
    cmp = c1.radio("비교 곡선", ["전체 평균", "같은 계절 평균", "같은 방 평균", "없음"], horizontal=False)
    if cmp == "전체 평균":
        cbids = T[T["완결"]].bid.tolist()
    elif cmp == "같은 계절 평균":
        cbids = T[T["완결"] & (T.season == b.season)].bid.tolist()
    elif cmp == "같은 방 평균":
        key = "생육실" if crop == "팽이" else "room"
        cbids = T[T["완결"] & (T[key] == b[key])].bid.tolist()
    else:
        cbids = []
    mine = PE[PE.bid == bid]
    if mine.empty:
        st.warning("이 배치 기간의 환경 데이터가 캐시에 없습니다.")
        if crop == "팽이":
            st.divider(); batch_report_box(ver, bid, preview=False, where="explorer")
        return
    rules = {}
    if crop == "팽이":
        rules = {"균긁기·입상": 1, "생육실 입실": (b["권지일"] - b["입상일"]).days + 1}
        if pd.notna(b.get("수확시작")):
            rules["수확"] = (b["수확시작"] - b["입상일"]).days + 1
    else:
        rules = {"균긁기·입상": 0, "뒤집기": b["뒤집기_d"], "솎기": b["솎기_d"], "수확": b["수확시작_d"]}
    has_cult = (mine.phase == "배양").any()
    with c1:
        st.write("")
        show_cult = st.checkbox("배양실 구간 포함 (접종일부터)", value=False, disabled=not has_cult,
                                help="기본은 균긁기(입상) 이후만. 켜면 접종일부터 배양실 평균 환경을 앞에 이어 그립니다." if has_cult else "이 배치는 접종일이 연결되지 않아 배양 구간이 없습니다.")
    if not show_cult:
        mine = mine[mine.phase != "배양"]
        if mine.empty:
            st.info("이 배치의 발이실·생육실 환경 데이터가 캐시에 없습니다. (배양실 구간만 있음 — 위 체크박스를 켜면 보입니다)")
            return
    lo_day = day_min if show_cult else (1 if crop == "팽이" else 0)
    x_title = ("입상 후 일차 (입상일=1, 음수는 배양실)" if crop == "팽이" else "입상 후 일차 (입상일=0, 음수는 배양실)") if show_cult else ("입상 후 일차 (입상일=1)" if crop == "팽이" else "입상 후 일차 (입상일=0)")
    # 비 온 날(강수 RAIN_MARK_MM 이상) → 습도 그래프 x축 위 동그라미
    rain = pd.DataFrame()
    try:
        wx = load_weather(ver)
        if not wx.empty and "date" in mine:
            w2 = mine[["cday", "date"]].merge(wx[["date", "강수mm"]], on="date", how="inner")
            w2 = w2[w2["강수mm"] >= C.RAIN_MARK_MM]
            rain = pd.DataFrame({"cday": w2.cday, "label": w2["강수mm"].map(lambda v: f"{v:.0f}mm")})
    except Exception:
        pass
    with c2:
        for col, spc, title, unit, dom in (("temp", "sp_temp", "온도", "°C", None), ("hum", "sp_hum", "습도", "%", None), ("co2", "sp_co2", "CO2", "ppm", None)):
            series = [("실측", mine.rename(columns={col: "y"}), PAL["실측"], False)]
            if not (col == "hum" and mine[spc].max() > 101):          # 팽이 습도 설정값은 코드값이라 표시 안 함
                series.append(("설정", mine.rename(columns={spc: "y"}), PAL["설정"], True))
            if cbids:
                series.append(prof_series(PE, cbids, f"{cmp} (n={len(cbids)})", PAL["비교"], col, False, day_max, lo_day))
            ch = curve_chart([s for s in series if s], f"{title} ({unit})", dom, rules, x_title=x_title, marks=rain if col == "hum" else None)
            if ch is not None:
                st.altair_chart(ch, **_FULL)
                if col == "hum" and len(rain):
                    st.caption(f"💧 비 온 날(강수 {C.RAIN_MARK_MM:.0f}mm 이상, 기상청 자료) — {len(rain)}일. 물방울에 마우스를 대면 강수량이 보입니다.")
        series = [("냉방", mine.assign(y=mine.cool * 100), PAL["실측"], False), ("난방", mine.assign(y=mine.heat * 100), PAL["비교"], False), ("가습", mine.assign(y=mine.humid * 100), PAL["보조"], False)]
        ch = curve_chart(series, "가동률 (%)", [0, 100], rules, height=160, x_title=x_title)
        if ch is not None:
            st.altair_chart(ch, **_FULL)
    with st.expander("일차별 수치 표"):
        _cols = ["cday", "작업" if (crop == "새송이" and "작업" in mine) else "phase", "room", "date", "temp", "sp_temp", "hum", "sp_hum", "가습주기", "co2", "sp_co2", "cool", "heat", "humid", "run"]
        tbl = mine[[c for c in _cols if c in mine]].copy()
        if "가습주기" in tbl and tbl["가습주기"].isna().all():
            tbl = tbl.drop(columns=["가습주기"])          # 새송이처럼 습도 % 로 제어하는 방은 빈 열
        for c in ("cool", "heat", "humid", "run"):
            tbl[c] = tbl[c] * 100
        tbl = tbl.rename(columns={"cday": "일차", "phase": "구간", "작업": "작업(일지)", "room": "방", "date": "날짜", "temp": "온도", "sp_temp": "설정온도", "hum": "습도", "sp_hum": "설정습도",
                                  "co2": "CO2", "sp_co2": "설정CO2", "cool": "냉방%", "heat": "난방%", "humid": "가습%", "run": "운전%"}).round(1)
        st.caption("온도·습도·CO2 = 운전중 구간의 하루 평균 실측값, 설정 = 제어기 목표값 평균. 냉방·난방·가습% = 운전 시간 중 가동 비율, 운전% = 하루 중 운전중 비율. "
                   "가습 주기 = 제어기가 습도 목표(%) 대신 '고정' 방식으로 돌 때의 가습기 작동/정지 시간 — 팽이 발이실·생육실이 여기 해당하며, 그때 설정습도는 빈칸입니다.")
        st.dataframe(tbl, hide_index=True, **_FULL, column_config={"날짜": st.column_config.DateColumn(format="YYYY-MM-DD")})
    if crop == "팽이":
        st.divider()
        st.markdown("**이 배치 보고서를 A4로**")
        batch_report_box(ver, bid, preview=False, where="explorer")


# ───────────────────────── 페이지: 계절·월별 비교 ─────────────────────────
def page_compare(ver, crop):
    st.title(f"계절·월별 비교 — {crop}")
    if crop == "팽이":
        T, PE = load_paengi(ver); room_key = "생육실"; day_max = 30; x_title = "입상 후 일차 (입상일=1)"
        rules = {"생육실 입실(평균)": 18}
    else:
        T, PE = load_ss(ver); room_key = "room"; day_max = 16; x_title = "입상 후 일차 (입상일=0)"
        rules = {"뒤집기": 7, "솎기": 11, "수확": 14}
    if T.empty or PE.empty:
        st.info("배치 데이터가 없습니다."); return
    T = T[T["완결"]].copy()
    if crop == "팽이":
        T["방"] = T["생육실"].map(lambda r: f"생육실{int(r):02d}"); T["동"] = "진성"
    else:
        T["방"] = T["room"]; T["동"] = T["block"] + "동"
    has_yield = crop == "새송이" and "병당g" in T and T["병당g"].notna().sum() >= 8
    dims = (["계절", "월", "동", "방", "관리자 교체 전후(새송이)"] + (["병당수율 상·하위"] if has_yield else [])) if crop == "새송이" else ["계절", "월", "방", "병당중량 상·하위"]
    dim = st.radio("비교 기준", dims, horizontal=True)
    if dim == "계절":
        T["grp"] = T.season
    elif dim == "월":
        T["grp"] = T.month
    elif dim == "동":
        T["grp"] = T["동"]
    elif dim == "방":
        T["grp"] = T["방"]
    elif dim.startswith("관리자"):
        T["grp"] = np.where(T["입상일"] < "2026-04-09", "교체 전(~4/8 입상)", "교체 후(4/9~ 입상)")
    else:
        ycol = "병당g" if dim.startswith("병당수율") else "병당중량_표시"
        s = T.dropna(subset=[ycol])
        q1, q3 = s[ycol].quantile(.25), s[ycol].quantile(.75)
        T["grp"] = np.where(T.bid.isin(s[s[ycol] >= q3].bid), f"상위 25% (≥{q3:.0f}g)", np.where(T.bid.isin(s[s[ycol] <= q1].bid), f"하위 25% (≤{q1:.0f}g)", None))
        T = T.dropna(subset=["grp"])
        if ycol == "병당g":
            st.caption(f"방 단위 수확 자료가 있는 {len(s)}개 배치(입상 {s['입상일'].min():%Y-%m-%d}\\~{s['입상일'].max():%Y-%m-%d}) 기준. 병당g = 그 방 수확 kg ÷ 입상 병수. 출처: 방별 포장량(\\~2026-04) + 실별QC(2026-03\\~).")
    groups = sorted(T.grp.dropna().unique())
    default = groups if len(groups) <= 4 else (groups[-4:] if dim == "월" else groups[:4])   # 월은 최근 4개월
    pick = st.multiselect("보여줄 그룹", groups, default=default)
    if not pick:
        return
    inc_cult = st.checkbox("배양 구간(접종일부터) 포함", value=False)
    day_min = (-30 if crop == "팽이" else -45) if inc_cult else (1 if crop == "팽이" else 0)
    if inc_cult:
        rules = {"균긁기·입상": 1 if crop == "팽이" else 0, **rules}
    colors = ["#2a78d6", "#eb6834", "#1baf7a", "#9c5fd6", "#d6a12a", "#3aa7c9", "#c9486b", "#6b7160"]
    cnt = {g: int((T.grp == g).sum()) for g in pick}
    st.caption(" · ".join(f"{g}: {n}개 배치" for g, n in cnt.items()))
    c1, c2 = st.columns(2)
    for i, (col, title, unit) in enumerate((("temp", "온도", "°C"), ("hum", "습도", "%"), ("co2", "CO2", "ppm"), ("cool", "냉방 가동률", "%"))):
        series = []
        for j, g in enumerate(pick):
            p = A.profile(PE, T[T.grp == g].bid.tolist(), day_max, day_min)
            if p.empty:
                continue
            y = p[col] * (100 if col == "cool" else 1)
            series.append((g, p.assign(y=y), colors[j % len(colors)], False))
        ch = curve_chart(series, f"{title} ({unit})", None, rules, x_title=x_title)
        if ch is not None:
            (c1 if i % 2 == 0 else c2).altair_chart(ch, **_FULL)
    # 설정온도 비교
    series = []
    for j, g in enumerate(pick):
        p = A.profile(PE, T[T.grp == g].bid.tolist(), day_max, day_min)
        if not p.empty:
            series.append((g, p.assign(y=p.sp_temp), colors[j % len(colors)], True))
    ch = curve_chart(series, "설정온도 (°C)", None, rules, x_title=x_title)
    if ch is not None:
        st.altair_chart(ch, **_FULL)
    # 그룹별 결과 지표
    st.subheader("그룹별 결과")
    if crop == "팽이":
        g = T.groupby("grp").agg(배치=("bid", "count"), 병당중량=("병당중량_표시", "mean"), 단일방배치=("단일방수확", "sum"), QC=("QC", "mean"), 긁기불량률=("긁기불량률%", "mean"), 권지불량률=("권지불량률%", "mean"), 총일수=("총일수", "mean")).round(2)
    else:
        g = T.groupby("grp").agg(배치=("bid", "count"), 입상병수=("bottles", "mean"), 수확시작일차=("수확시작_d", "mean"), 수확일수=("수확일수", "mean"), 재입상일차=("재입상_d", "mean"),
                                 병당g=("병당g", "mean"), 병당g_배치수=("병당g", "count"), 미니비중=("미니비중%", "mean")).round(1)
    st.dataframe(g.loc[[p for p in pick if p in g.index]].rename_axis("그룹"), **_FULL)
    pm, sm, cm = load_monthly(ver)
    with st.expander("월별 지표 표"):
        st.dataframe((pm if crop == "팽이" else sm).iloc[::-1].round(1), hide_index=True, **_FULL)
    with st.expander("배양실 손실·불량률(월별)"):
        if not cm.empty:
            pv = cm.pivot(index="m", columns="항목", values="비율%").round(2)
            st.dataframe(pv.iloc[::-1], **_FULL)
            long = cm[cm["항목"].str.contains("팽이" if crop == "팽이" else "새송이")]
            ch = alt.Chart(long).mark_line(point=True).encode(x=alt.X("m:N", title="월"), y=alt.Y("비율%:Q"), color="항목:N", tooltip=["m", "항목", alt.Tooltip("비율%:Q", format=".2f")]).properties(height=220)
            st.altair_chart(ch, **_FULL)


# ───────────────────────── 페이지: 배양실 ─────────────────────────
@st.cache_data(show_spinner=False)
def load_cult(ver):
    env = load_env(ver)
    return A.cult_env_monthly(env), A.cult_monthly()


def page_cult(ver):
    st.title("배양실")
    cm_env, cm_rate = load_cult(ver)
    if cm_env.empty:
        st.info("배양실(재배동 코드 31·32) 환경 데이터가 캐시에 없습니다."); return
    rooms = st.multiselect("방", sorted(cm_env.room.unique()), default=["배양실1", "배양실2"])
    x = cm_env[cm_env.room.isin(rooms)]
    c1, c2 = st.columns(2)
    for i, (col, title, unit) in enumerate((("temp", "온도", "°C"), ("hum", "습도", "%"), ("co2", "CO2", "ppm"), ("track_err", "온도 추종오차", "°C"))):
        ch = alt.Chart(x).mark_line(point=True).encode(x=alt.X("m:N", title="월"), y=alt.Y(f"{col}:Q", title=f"{title} ({unit})", scale=alt.Scale(zero=False)),
                                                    color=alt.Color("room:N", title=None, legend=alt.Legend(orient="top")),
                                                    tooltip=["room", "m", alt.Tooltip(f"{col}:Q", format=".1f")]).properties(height=220)
        (c1 if i % 2 == 0 else c2).altair_chart(ch, **_FULL)
    st.subheader("배양실 환경 ↔ 불량률")
    st.caption("같은 달의 배양실 평균 환경과 균긁기·권지·접종 불량률을 나란히 놓았습니다. 균긁기 불량은 배양 중 오염이 긁을 때 드러난 것이라 배양실 환경의 결과 지표입니다.")
    if not cm_rate.empty:
        r = cm_rate.copy()
        ch = alt.Chart(r).mark_line(point=True).encode(x=alt.X("m:N", title="월"), y=alt.Y("비율%:Q"), color=alt.Color("항목:N", legend=alt.Legend(orient="top", title=None)),
                                                    tooltip=["m", "항목", alt.Tooltip("비율%:Q", format=".2f")]).properties(height=260)
        ev = [l for l in event_layers(months=True) if True]
        st.altair_chart(alt.layer(ch, *ev) if ev else ch, **_FULL)
        with st.expander("사건 기록 (보라 점선 번호)", expanded=True):
            st.dataframe(events_df().assign(날짜=lambda d: d.날짜.dt.strftime("%Y-%m-%d")), hide_index=True, **_FULL, height=60 + 35 * len(C.EVENTS))
            st.caption("사건은 config.py EVENTS 에 (날짜, 이름, 설명) 한 줄로 추가합니다. 종균·살균 페이지 추이와 병목 찾기 기간에도 같이 쓰입니다.")
        both = cm_env[cm_env.room.isin(["배양실1", "배양실2"])].groupby("m").agg(온도=("temp", "mean"), 습도=("hum", "mean"), CO2=("co2", "mean")).round(1)
        pv = r.pivot(index="m", columns="항목", values="비율%").round(2)
        tbl = both.join(pv, how="outer").sort_index(ascending=False)
        st.dataframe(tbl.rename_axis("월"), **_FULL)
        # 상관 (월 단위, 표본 적음 — 참고용)
        j = tbl.dropna(subset=["균긁기불량(팽이)", "온도", "습도"]) if "균긁기불량(팽이)" in tbl else pd.DataFrame()
        if len(j) >= 6:
            cc = {k: j[k].corr(j["균긁기불량(팽이)"]) for k in ("온도", "습도", "CO2")}
            st.caption("월별 팽이 균긁기 불량률과의 상관: " + " · ".join(f"{k} r={v:.2f}" for k, v in cc.items()) + f" (n={len(j)}개월, 참고용)")
    st.subheader("배치별 배양 결과 (팽이)")
    K, _ = load_paengi(ver)
    if not K.empty:
        d = K.dropna(subset=["긁기불량률%", "배양일수"])
        if not d.empty:
            sc = alt.Chart(d).mark_circle(size=60, opacity=.6).encode(x=alt.X("배양일수:Q", scale=alt.Scale(zero=False)), y=alt.Y("긁기불량률%:Q"),
                                                                       color=alt.Color("season:N", title="입상 계절"), tooltip=["bid", "접종일", "입상일", "배양일수", alt.Tooltip("긁기불량률%:Q", format=".2f"), "탱크"]).properties(height=260)
            st.altair_chart(sc, **_FULL)
            with st.expander("액탱크별 불량률 순위 (팽이)", expanded=True):
                tr = A.tank_rank()
                if tr.empty:
                    st.info("탱크 기록이 없습니다.")
                else:
                    pr = tr.attrs.get("period", ("", ""))
                    st.caption(f"균긁기 일지의 탱크 기록 기간 {pr[0]} ~ {pr[1]}. 두 탱크를 함께 쓴 배치는 병수를 반씩 배분. "
                               "월평균대비pp = 같은 달 전체 불량률과의 차이(계절·배양실 영향 보정) — 양수면 그 달 다른 탱크보다 나쁨. 불량률이 높은 순.")
                    st.dataframe(tr.style.format({"불량률%": "{:.2f}", "단독불량률%": "{:.2f}", "최근90일%": "{:.2f}", "월평균대비pp": "{:+.2f}"}), **_FULL)
            g = d.groupby("탱크").agg(배치=("bid", "count"), 긁기불량률=("긁기불량률%", "mean"), 병당중량=("병당중량_표시", "mean")).round(2).sort_values("긁기불량률")
            with st.expander("탱크 조합별 (배치 기준, 3개 이상)"):
                st.dataframe(g[g.배치 >= 3], **_FULL)


# ───────────────────────── 페이지: 이상 감시 ─────────────────────────
def page_anomalies(ver):
    st.title("이상 감시")
    days = st.slider("최근 며칠", 7, 60, 14, step=7)
    an = load_anomalies(ver, days)
    RP = load_repairs(ver)[0]
    if an.empty:
        st.success("감지된 항목이 없습니다.")
        _recent_repairs_box(ver, RP, days)
        return
    c1, c2 = st.columns([1, 3])
    sev = c1.multiselect("심각도", ["높음", "중간", "낮음"], default=["높음", "중간"])
    cats = c2.multiselect("분류", sorted(an["분류"].unique()), default=sorted(an["분류"].unique()))
    x = an[an["심각도"].isin(sev) & an["분류"].isin(cats)].copy()
    if not RP.empty and len(x):
        x.insert(x.columns.get_loc("내용") + 1, "최근 수리", A.anomaly_repairs(x, RP).values)
    st.caption(f"{len(x)}건" + (" · '최근 수리' = 같은 방의 수리 기록 중 그 날짜 앞뒤 30일 안의 것 (수리 이력 메뉴)" if "최근 수리" in x else ""))
    st.dataframe(x, hide_index=True, **_FULL, height=min(500, 38 + 35 * max(len(x), 1) + 3),
                 column_config={"값": st.column_config.NumberColumn(format="%.2f"), "최근 수리": st.column_config.TextColumn(width="medium")})
    if not RP.empty and len(x):
        _anomaly_repair_view(ver, x, RP, days)
    _recent_repairs_box(ver, RP, days)
    with st.expander("판정 기준"):
        R = C.RULES
        st.markdown(f"""
기준은 2026년 1\\~10월 실측 분포로 정했습니다(v1.22.0). 정상 운전에서도 늘 생기는 값은 이상으로 보지 않고, 같은 방에서 며칠 이어진 센서 이상·장비 에러·추종오차는 **한 줄(N일 연속)** 로 묶어 마지막 날짜에 표시합니다.
- **로그 중단**: 최근 60일 가동한 방의 마지막 기록이 {R['log_stale_days']}일 이상 전
- **센서 이상**: 센서가 물리적으로 나올 수 없는 값을 보낸 시간이 하루 {R['sensor_bad_min']}분 이상. 습도 101\\~{R['hum_sat_max']:.0f}%는 센서 포화(물기)로, 냉각실 온도 {R['cool_room_temp_max']:.0f}℃까지는 뜨거운 솥 입고로 보고 빼며, 그 밖의 값(온도 −5\\~45°C 밖, 습도 음수·{R['hum_sat_max']:.0f}% 초과, CO2 0\\~20,000ppm 밖)만 셉니다. 내용에 어떤 항목·몇 번 센서가 어떤 값(최대/최소)을 보냈는지 표시됩니다. 범위 밖 값은 평균 계산에서 제외됩니다.
- **장비 에러**: 에러 상태 필드가 하루 {R['err_min']}분 이상 (짧은 경보는 문 여닫이·제상 등으로 흔함)
- **온도 추종오차**: 운전 80% 이상, 설정을 바꾸지 않은 날의 실측−설정 평균차 > {R['track_err_c']}°C. 입실·입상 당일과 다음날(병이 들어와 식는 중), 냉각실(매일 뜨거운 솥), 배양실(아래 따로), 발이실 1·2번은 보지 않음
- **설정값 미사용**: 새송이 운전 중인데 습도 설정 <50% 또는 CO2 설정 >2,500ppm (2026년 4월 이전 방식)
- **억제 스케줄 늦음**: 팽이 발이실 11\\~14일차 설정온도가 1\\~6월 방식(14.5/11/5.5/4°C)보다 {R['suppress_tol_c']}°C 이상 높음
- **배양 불량률**: 주간 균긁기 불량률 팽이 >{R['scrape_bad_paengi']}%, 새송이 >{R['scrape_bad_ss']}% (기준의 {R['scrape_high_mult']:g}배 이상이면 높음); 권지 불량률 >{R['kwonji_bad']}%
- **배양실**: 습도 {R['cult_hum']}% 초과, 또는 설정과 온도가 {R['cult_temp_c']:g}°C 넘게 차이 나는 날이 최근 {R['cult_days']}일 이상 (배양실2는 평소 1.3°C 낮게 운전)
- **입상 병수**: 새송이 방당 병수가 같은 동(A~D) 최근 120일 중앙값에서 ±{R['ss_bottles_tol']:.0%} 넘게 벗어남 (동마다 방 크기가 다름)
- **접종 배지온도**: 종합관리의 솥별 접종온도가 {R['inoc_temp_low']:g}\\~{R['inoc_temp_high']:g}℃ 밖이면 중간, {R['inoc_temp_low_hi']:g}℃ 미만·{R['inoc_temp_high_hi']:g}℃ 초과면 높음 (2026년 실측 중앙 14.5℃). 접종일·작물별 한 줄에 순번과 온도를 적습니다 — 과냉(너무 차가움)·덜 식음(너무 뜨거움)
- **냉각 느림·과냉**: 평소(같은 방·같은 솥수)의 {R['cool_slow_ratio']:g}배 이상이면서 {R['cool_slow_add_min']}분 넘게 늦게 식음 / 설정보다 {R['overcool_c']:g}℃ 넘게 낮은 시간이 하루 {R['overcool_min']}분 이상. 냉각실은 자체 시스템이 제어하고 uni-farm 센서는 모니터링용이라 과냉은 **낮음**(참고)으로만 표시
- 감시 제외 방: {', '.join(sorted(C.MONITOR_EXCLUDE))} (창고 등 재배와 무관한 용도)
- **최근 수리**: 같은 방의 수리 기록(기계수리.xlsx) 중 그 날짜 앞뒤 30일 안의 것. 고친 날 전부터 문제가 있었을 수 있어 뒤쪽도 봅니다. 배양 불량률 줄('배양실')은 배양실1·2를 함께 봅니다.
""")


def _anomaly_repair_view(ver, x, RP, days):
    """이상 감시에 걸린 방 하나를 골라 일별 온도와 수리한 날을 한 그래프에."""
    env = load_env(ver)
    rooms = [r for r in dict.fromkeys(x["방"].astype(str)) if r]
    if not rooms:
        return
    today = env.date.max() if not env.empty else pd.Timestamp.today().normalize()
    d0 = today - pd.Timedelta(days=max(days, 30) + 30)
    L = A.repairs_by(RP, "방")
    L = L[L.date >= d0]
    want = lambda r: {"배양실1", "배양실2"} if r == "배양실" else {r}
    n_rep = {r: int(L[L["_key"].isin(want(r))].drop_duplicates(["date", "내용"]).shape[0]) for r in rooms}
    n_an = x.groupby("방").size().to_dict()
    st.subheader("이상 항목과 수리 기록 같이 보기")
    rooms = sorted(rooms, key=lambda r: (-n_rep.get(r, 0), A.room_sort_key(r)))
    room = st.selectbox("방", rooms, key="an_rep_room",
                        format_func=lambda r: f"{r} — 이상 {n_an.get(r, 0)}건 · 수리 {n_rep.get(r, 0)}건 ({d0:%m/%d}~)")
    reps = L[L["_key"].isin(want(room))].drop_duplicates(["date", "내용"]).sort_values("date", ascending=False)
    marks = x[x["방"].astype(str) == room][["날짜", "분류", "내용"]].rename(columns={"날짜": "date"})
    env_room = "배양실1" if room == "배양실" else room
    charts = room_repair_chart(env, env_room, d0, today, reps, marks)
    for ch in charts:
        st.altair_chart(ch, **_FULL)
    if charts:
        st.caption("파란 선 실측 · 회색 점선 설정 · 빨간 점 = 이상 감시에 걸린 날(며칠 이어진 항목은 마지막 날) · 주황 세로선 🔧 = 수리한 날 (마우스를 올리면 내용). "
                   "아래 칸은 냉각기 가동률 — 냉매가 새면 가동률이 오르는데도 온도가 설정을 못 따라갑니다.")
    if len(reps):
        st.dataframe(reps[["date", "장소", "분류", "내용", "업체", "비고"]].rename(columns={"date": "날짜"}).assign(날짜=lambda d: d["날짜"].dt.strftime("%Y-%m-%d")),
                     hide_index=True, **_FULL, height=min(320, 38 + 35 * len(reps)))
    else:
        st.caption(f"{room}: {d0:%m/%d} 이후 수리 기록 없음")


def _recent_repairs_box(ver, RP, days):
    """최근 며칠 동안의 수리 기록 전체 (방이 정해지지 않은 장비 수리도 포함)."""
    if RP is None or RP.empty:
        return
    today = pd.Timestamp.today().normalize()
    y = RP[RP.date >= today - pd.Timedelta(days=days)].sort_values("date", ascending=False)
    with st.expander(f"🔧 최근 {days}일 수리 기록 {len(y)}건 (기계수리.xlsx 마지막 기록 {RP.date.max():%m/%d})"):
        if y.empty:
            st.caption("이 기간 수리 기록이 없습니다.")
        else:
            st.dataframe(y[["date", "구역", "장소", "분류", "내용", "업체"]].rename(columns={"date": "날짜"}).assign(날짜=lambda d: d["날짜"].dt.strftime("%Y-%m-%d")),
                         hide_index=True, **_FULL, height=min(400, 38 + 35 * len(y)))


# ───────────────────────── 페이지: 수리 이력 (v1.23.0) ─────────────────────────
@st.cache_data(show_spinner="수리 기록 읽는 중…")
def load_repairs(ver):
    return A.repairs(), A.repair_costs()


def room_repair_chart(env, room, d0, d1, reps=None, marks=None, height=260):
    """방 하나의 일별 실측·설정 온도(위)와 냉각기 가동률(아래) 그래프 목록. 수리한 날 = 주황 세로선·🔧, 이상 감시 날 = 빨간 점.
    두 그래프를 따로 그리고(화면 폭에 맞추려고) x축 범위를 같게 맞춘다."""
    if env is None or env.empty:
        return []
    d0, d1 = pd.Timestamp(d0).normalize(), pd.Timestamp(d1).normalize()
    e = env[(env.room == room) & (env.date >= d0) & (env.date <= d1)].sort_values("date")
    if e.empty:
        return []
    dom = [alt.DateTime(year=d.year, month=d.month, date=d.day) for d in (d0, d1)]
    xenc = alt.X("date:T", title=None, axis=alt.Axis(format="%m/%d"), scale=alt.Scale(domain=dom))
    Lt = e[["date", "temp", "sp_temp"]].rename(columns={"temp": "실측", "sp_temp": "설정"}).melt("date", var_name="계열", value_name="온도").dropna()
    top = alt.Chart(Lt).mark_line(strokeWidth=2).encode(
        x=xenc, y=alt.Y("온도:Q", title=f"{room} 온도(℃)", scale=alt.Scale(zero=False), axis=alt.Axis(minExtent=40)),
        color=alt.Color("계열:N", scale=alt.Scale(domain=["실측", "설정"], range=[PAL["실측"], PAL["설정"]]), legend=alt.Legend(orient="top", title=None)),
        strokeDash=alt.StrokeDash("계열:N", scale=alt.Scale(domain=["실측", "설정"], range=[[1, 0], [6, 4]]), legend=None),
        tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"), "계열:N", alt.Tooltip("온도:Q", format=".1f")])
    layers, low = [top], []
    if reps is not None and len(reps):
        rp = reps[(reps.date >= d0) & (reps.date <= d1)][["date", "분류", "내용"]].copy()
        if len(rp):
            tip = [alt.Tooltip("date:T", title="수리", format="%Y-%m-%d"), "분류:N", "내용:N"]
            rule = alt.Chart(rp).mark_rule(color="#e08a00", strokeWidth=2, opacity=0.75).encode(x=xenc, tooltip=tip)
            layers += [rule, alt.Chart(rp).mark_text(text="🔧", fontSize=13, baseline="top", dy=2).encode(x=xenc, y=alt.value(0), tooltip=tip)]
            low.append(rule)
    if marks is not None and len(marks):
        mk = marks.assign(date=pd.to_datetime(marks["date"])).merge(e[["date", "temp"]], on="date", how="inner")
        if len(mk):
            layers.append(alt.Chart(mk).mark_point(size=110, color="#e0312a", filled=True, opacity=0.9).encode(
                x=xenc, y="temp:Q", tooltip=[alt.Tooltip("date:T", title="이상", format="%Y-%m-%d"), "분류:N", "내용:N"]))
    out = [alt.layer(*layers).properties(height=height)]
    if "cool" in e and e["cool"].notna().any():
        cc = alt.Chart(e[["date", "cool"]].assign(냉각기=100 * e["cool"])).mark_area(opacity=0.3, color="#3bb6d6", line={"color": "#3bb6d6"}).encode(
            x=xenc, y=alt.Y("냉각기:Q", title="냉각기(%)", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(minExtent=40)),
            tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"), alt.Tooltip("냉각기:Q", title="냉각기 가동(%)", format=".0f")])
        out.append(alt.layer(cc, *low).properties(height=130))
    return out


EQUIP_COLOR = {"냉방": "#2a78d6", "난방": "#eb6834", "가습": "#1baf7a", "환기": "#8a5cd0"}


@st.cache_data(show_spinner="설비 가동률 계산 중…")
def load_equip(ver):
    K, PE = load_paengi(ver)
    _, SE = load_ss(ver)
    D = A.equip_cycle_days(PE, SE, K)
    return D, A.equip_cycles(D)


def _equip_tab(ver, RP):
    """설비 가동률: 같은 사양 방끼리(새송이 동별·팽이 발이실·생육실) 재배 1주기 동안 냉방·난방·가습·환기가 '같은 주기 일차·비슷한 시기'
    다른 방들보다 얼마나 더 돌았나 → 유독 높은 방과 항목"""
    D, CY = load_equip(ver)
    if CY.empty:
        st.info("재배 주기와 유니팜 환경 기록을 잇지 못했습니다(배치 기록 또는 환경 데이터 없음)."); return
    c1, c2 = st.columns([3.2, 1.6])
    grp = c1.radio("묶음 (방마다 설비 사양이 같은 곳끼리)", list(A.EQUIP_GROUPS), horizontal=True, key="eq_grp")
    ncyc = c2.radio("비교 범위", [1, 3], horizontal=True, key="eq_ncyc", format_func=lambda n: "최근 1주기" if n == 1 else "최근 3주기 평균")
    S = A.equip_rooms(CY, grp, ncyc)
    if S.empty:
        st.info(f"{grp}: 비교할 주기가 없습니다."); return
    unit = {"팽이 발이실": "입상~권지", "팽이 생육실": "권지~수확"}.get(grp, "입상~폐상")
    st.caption(f"1주기 = {unit}. 주기 안의 날마다(예: 6일째) 같은 묶음 다른 방들 가운데 시작일이 ±{A.EQUIP_NEAR_DAYS}일 안인(날씨가 비슷한) 주기의 "
               f"중앙값을 '기대 가동률'로 두고, 이 방이 한 주기 동안 그보다 평균 몇 %p 더 돌았나를 봅니다. 가동률 = 운전 중인 시간 가운데 그 설비가 켜져 있던 비율. "
               f"환기 = 시로코팬(CO2 릴레이). 기대보다 +{A.EQUIP_HIGH}%p 넘으면 '유독 높음'.")
    L = A.repairs_by(RP, "방")
    last_rep = L.sort_values("date").groupby("_key").tail(1).set_index("_key")
    S["마지막 수리"] = [f"{last_rep.at[r, 'date']:%Y-%m-%d} {A._short(last_rep.at[r, '내용'], 26)}" if r in last_rep.index else "—" for r in S["방"]]
    hi = S[S["_n_high"] > 0].sort_values("_sum", ascending=False)
    if len(hi):
        st.markdown(f"**유독 가동률이 높은 방 — {grp}, {'최근 1주기' if ncyc == 1 else '최근 3주기 평균'}**")
        for _, r in hi.iterrows():
            st.markdown(BR._md(f"- **{r['방']}** — {r['유독 높은 항목']} (주기 {r['주기']}, {r['기간']}) · 마지막 수리: {r['마지막 수리']}"))
    else:
        st.success(f"{grp}: {'최근 1주기' if ncyc == 1 else '최근 3주기'} 동안 다른 방보다 유독 많이 돈 설비가 없습니다.")

    # 방 × 항목 칸: 색 = 기대 대비 차이(%p), 글자 = 차이
    M = S.melt(id_vars=["방", "주기", "기간"], value_vars=[n + " 차이" for _, n in A.EQUIP], var_name="항목", value_name="차이")
    M["항목"] = M["항목"].str.replace(" 차이", "", regex=False)
    M["가동률"] = [S.set_index("방").at[r, n] for r, n in zip(M["방"], M["항목"])]
    M["기대"] = M["가동률"] - M["차이"]
    M["글"] = M["차이"].map(lambda v: "" if pd.isna(v) else f"{v:+.0f}")
    rooms = list(S["방"])
    # 이 묶음에서 거의 안 쓰는 설비(예: 팽이 생육실 난방)는 빼고 보여 준다
    items = [n for _, n in A.EQUIP if CY.loc[CY["묶음"] == grp, n].max() >= 1]
    M = M[M["항목"].isin(items)]
    base = alt.Chart(M).encode(x=alt.X("항목:N", sort=items, title=None, axis=alt.Axis(orient="top", labelAngle=0, labelFontSize=13)),
                               y=alt.Y("방:N", sort=rooms, title=None, axis=alt.Axis(labelFontSize=12)))
    tip = [alt.Tooltip("방:N"), alt.Tooltip("항목:N"), alt.Tooltip("가동률:Q", title="이 방 평균 가동률(%)", format=".0f"),
           alt.Tooltip("기대:Q", title="같은 조건 기대(%)", format=".0f"), alt.Tooltip("차이:Q", title="차이(%p)", format="+.1f"), "주기:N", "기간:N"]
    rect = base.mark_rect(stroke="white", strokeWidth=2).encode(
        color=alt.Color("차이:Q", title="기대 대비 %p", scale=alt.Scale(scheme="redblue", domain=[-20, 20], reverse=True, clamp=True), legend=alt.Legend(orient="right")),
        tooltip=tip)
    txt = base.mark_text(fontSize=12).encode(text="글:N", color=alt.condition(f"abs(datum.차이) >= {A.EQUIP_HIGH * 1.4}", alt.value("white"), alt.value("#333")), tooltip=tip)
    cc1, cc2 = st.columns([1.1, 2])
    with cc1:
        st.altair_chart((rect + txt).properties(height=alt.Step(26), width=320))
        st.caption("숫자 = 기대보다 몇 %p 더(+)·덜(−) 돌았나. 빨갈수록 유독 많이 돎. 마우스를 올리면 이 방 평균 가동률과 기대값.")
    with cc2:
        V = S[["방", "기간"] + [c for n in items for c in (n, n + " 차이")] + ["유독 높은 항목", "마지막 수리"]].copy()
        cfg = {"방": st.column_config.TextColumn("방", width=80), "기간": st.column_config.TextColumn("기간", width=95)}
        for n in items:
            cfg[n] = st.column_config.NumberColumn(f"{n}%", format="%.0f", help=f"이 방 {n} 평균 가동률")
            cfg[n + " 차이"] = st.column_config.NumberColumn(f"{n} ±", format="%+.0f", help="같은 조건 다른 방(기대) 대비 %p")
        st.dataframe(V, hide_index=True, **_FULL, height=min(560, 38 + 35 * len(V)), column_config=cfg)

    st.markdown("#### 방 하나 자세히")
    order = list(hi["방"]) + [r for r in rooms if r not in set(hi["방"])]
    room = st.selectbox("방", order, key="eq_room",
                        format_func=lambda r: f"{r} — {S.set_index('방').at[r, '유독 높은 항목']}" if S.set_index("방").at[r, "유독 높은 항목"] != "—" else r)
    st.session_state["_ctx_note"] = f"설비 가동률 · {grp} · {room}"
    H = CY[(CY["묶음"] == grp) & (CY["방"] == room)].sort_values("시작")
    Lr = L[L["_key"] == room].drop_duplicates(["date", "내용"])
    if len(H):
        HM = H.melt(id_vars=["주기", "시작", "일수"], value_vars=[n + " 차이" for n in items], var_name="항목", value_name="차이")
        HM["항목"] = HM["항목"].str.replace(" 차이", "", regex=False)
        dom = [H["시작"].min().isoformat(), (H["끝"].max() + pd.Timedelta(days=1)).isoformat()]
        xenc = alt.X("시작:T", title="주기 시작일", scale=alt.Scale(domain=dom), axis=alt.Axis(format="%m/%d", labelAngle=0))
        lines = alt.Chart(HM.dropna(subset=["차이"])).mark_line(point=True).encode(
            x=xenc, y=alt.Y("차이:Q", title="기대 대비 %p", axis=alt.Axis(minExtent=40)),
            color=alt.Color("항목:N", sort=items, scale=alt.Scale(domain=items, range=[EQUIP_COLOR[n] for n in items]), legend=alt.Legend(orient="top", title=None)),
            tooltip=["주기:N", alt.Tooltip("시작:T", format="%Y-%m-%d"), "항목:N", alt.Tooltip("차이:Q", format="+.1f"), "일수:Q"])
        z0 = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#999").encode(y="y:Q")
        hi_ = alt.Chart(pd.DataFrame({"y": [A.EQUIP_HIGH]})).mark_rule(color="#999", strokeDash=[4, 3]).encode(y="y:Q")
        layers = [z0, hi_, lines]
        Lw = Lr[(Lr.date >= H["시작"].min()) & (Lr.date <= H["끝"].max())]
        if len(Lw):
            layers.append(alt.Chart(Lw.assign(날짜=Lw.date.dt.strftime("%Y-%m-%d"))).mark_rule(color="#e08a00", strokeWidth=2).encode(
                x=alt.X("date:T", scale=alt.Scale(domain=dom)), tooltip=[alt.Tooltip("날짜:N", title="수리한 날"), "내용:N"]))
        st.altair_chart(alt.layer(*layers).properties(height=240, title=f"{room} 주기마다 기대 대비 가동률 — 계속 높은지, 수리 뒤 내려왔는지"), **_FULL)
        st.caption(f"점 하나 = 주기 하나. 회색 점선 = +{A.EQUIP_HIGH}%p(유독 높음 기준). 주황 세로선 = 이 방을 수리한 날(마우스를 올리면 내용).")
    # 최근 주기의 일차별 곡선: 이 방 vs 기대
    lastc = H.dropna(subset=[n + " 차이" for _, n in A.EQUIP], how="all").tail(1)
    if len(lastc):
        cyc = lastc["cyc"].iloc[0]
        G = D[D["cyc"] == cyc].sort_values("cday")
        st.markdown(f"**최근 주기 {lastc['주기'].iloc[0]} ({lastc['시작'].iloc[0]:%m/%d}~{lastc['끝'].iloc[0]:%m/%d}) — 주기 일차별 이 방 vs 기대**")
        cols = st.columns(2)
        shown = 0
        for c_, n_ in A.EQUIP:
            if n_ not in items or (not (G[c_].fillna(0) >= 3).any() and not (G[c_ + "_exp"].fillna(0) >= 3).any()):
                continue
            P = pd.concat([G[["cday", c_]].rename(columns={c_: "v"}).assign(k=room), G[["cday", c_ + "_exp"]].rename(columns={c_ + "_exp": "v"}).assign(k="기대(다른 방)")])
            ch = alt.Chart(P).mark_line(point=alt.OverlayMarkDef(size=14)).encode(
                x=alt.X("cday:Q", title="주기 일차", axis=alt.Axis(tickMinStep=1)),
                y=alt.Y("v:Q", title=f"{n_}(%)", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(minExtent=36)),
                color=alt.Color("k:N", title=None, scale=alt.Scale(domain=[room, "기대(다른 방)"], range=[EQUIP_COLOR[n_], "#b9bcb4"]), legend=alt.Legend(orient="top")),
                strokeDash=alt.StrokeDash("k:N", scale=alt.Scale(domain=[room, "기대(다른 방)"], range=[[1, 0], [4, 3]]), legend=None),
                tooltip=[alt.Tooltip("cday:Q", title="일차"), "k:N", alt.Tooltip("v:Q", title="가동률(%)", format=".0f")])
            cols[shown % 2].altair_chart(ch.properties(height=230), **_FULL)
            shown += 1


def page_repairs(ver, me):
    st.title("수리 이력")
    RP, K = load_repairs(ver)
    if RP.empty and not os.path.exists(etl_logs._p("repairs")):
        # 처음 한 번: 아직 수리 기록을 읽은 적이 없으면 바로 읽는다 (바뀐 엑셀만 읽으므로 보통 금방 끝남)
        with st.spinner("수리 기록 엑셀을 처음 읽는 중… (바뀐 일지도 같이 읽습니다)"):
            try:
                etl_logs.run()
            except Exception as e:
                st.warning(f"일지를 읽다가 오류가 났습니다: {e}")
        st.cache_data.clear()
        RP, K = load_repairs(_ver())
    if RP.empty:
        st.info(f"수리 기록이 없습니다. 권석현 폴더(`{C.log_dir('kwon_ss')}`)의 기계수리.xlsx 를 읽어야 합니다 — 데이터 관리 → '일지 다시 읽기'."); return
    today = pd.Timestamp.today().normalize()
    st.caption(f"기계수리.xlsx(구역별 시트 + 일백 냉동 공사 시트) · 가습기 수리내역.xlsx — {RP.date.min():%Y-%m-%d} ~ {RP.date.max():%Y-%m-%d}, {len(RP):,}건. "
               "같은 수리를 구역 시트와 일백 시트에 둘 다 적은 줄은 한 줄로 합쳤습니다(다른 시트의 글은 비고에). "
               "장소(방·장비)와 분류는 내용 글에서 자동으로 뽑은 것이라 가끔 틀릴 수 있습니다.")
    c1, c2, c3, c4 = st.columns([1.1, 1.6, 1.6, 1.4])
    per = c1.selectbox("기간", ["최근 90일", "최근 180일", "최근 1년", "전체"], index=2, key="rp_per")
    d0 = {"최근 90일": today - pd.Timedelta(days=90), "최근 180일": today - pd.Timedelta(days=180),
          "최근 1년": today - pd.Timedelta(days=365)}.get(per, RP.date.min())
    areas = c2.multiselect("구역", sorted(RP["구역"].unique()), key="rp_area", placeholder="전체 구역")
    cats = c3.multiselect("분류", sorted(RP["분류"].unique()), key="rp_cat", placeholder="전체 분류")
    q = c4.text_input("내용 찾기", key="rp_q", placeholder="예: 냉매, 팽창변, 6번")
    X = RP[RP.date >= d0]
    if areas:
        X = X[X["구역"].isin(areas)]
    if cats:
        X = X[X["분류"].isin(cats)]
    if q.strip():
        k = q.strip()
        X = X[X["내용"].str.contains(k, case=False, regex=False) | X["장소"].str.contains(k, case=False, regex=False) | X["비고"].str.contains(k, case=False, regex=False)]
    LR = A.repairs_by(X, "방")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("수리 건수", f"{len(X):,}건", f"최근 30일 {int((X.date >= today - pd.Timedelta(days=30)).sum())}건", delta_color="off")
    m2.metric("수리한 방", f"{LR['_key'].nunique()}곳", "재배·배양·냉각실과 살균솥", delta_color="off")
    m3.metric("냉매·누수", f"{int((X['분류'] == '냉매·누수').sum())}건", "냉매 보충·누수 용접", delta_color="off")
    PP = A.repairs_by(X, "장소")
    PP = PP[(PP["방"] != "") & ~PP["_key"].str.contains("공용")]      # 방·냉동기 하나로 콕 집힌 수리만
    top = PP["_key"].value_counts()
    m4.metric("가장 잦은 곳", top.index[0] if len(top) else "—", f"{int(top.iloc[0])}건" if len(top) else None, delta_color="off")
    is_admin = me.get("role") == "관리자"
    names = ["방별 한눈에", "설비 가동률", "장소별 이력", "전체 목록", "분류·월별"] + (["수리비 (완료 보고서)"] if is_admin else [])
    TB = dict(zip(names, st.tabs(names)))

    with TB["방별 한눈에"]:
        if LR.empty:
            st.info("이 조건에 방이 정해진 수리 기록이 없습니다. (장비 수리는 '장소별 이력'에서 보세요)")
        else:
            G = LR.assign(월=LR.date.dt.strftime("%Y-%m")).drop_duplicates(["_key", "date", "내용"])
            g = G.groupby(["_key", "월"]).agg(n=("내용", "size"), 내용=("내용", lambda s: " / ".join(A._short(v, 20) for v in s.head(4)) + (f" 외 {len(s) - 4}건" if len(s) > 4 else ""))).reset_index().rename(columns={"_key": "방"})
            disp = lambda r: f"살균솥 {r}" if str(r).endswith("호기") else r          # 살균솥은 '1호기' 로 기록됨
            rooms = [disp(r) for r in sorted(g["방"].unique(), key=A.room_sort_key)]
            g["방"] = g["방"].map(disp)
            months = [p.strftime("%Y-%m") for p in pd.period_range(pd.Timestamp(max(d0, RP.date.min())), today, freq="M")]
            base = alt.Chart(g).encode(x=alt.X("월:O", title=None, sort=months, scale=alt.Scale(domain=months), axis=alt.Axis(labelAngle=-45)),
                                       y=alt.Y("방:N", sort=rooms, title=None))
            heat = base.mark_rect(stroke="white", strokeWidth=1).encode(
                color=alt.Color("n:Q", title="수리 건수", scale=alt.Scale(scheme="orangered", domainMin=0), legend=alt.Legend(orient="top")),
                tooltip=["방:N", "월:N", alt.Tooltip("n:Q", title="건수"), "내용:N"])
            txt = base.mark_text(fontSize=10).encode(text="n:Q", color=alt.condition(alt.datum.n >= 3, alt.value("white"), alt.value("#333")))
            st.altair_chart((heat + txt).properties(height=alt.Step(22)), **_FULL)
            st.caption("칸 = 방 × 달, 숫자 = 그달 수리 건수 (마우스를 올리면 내용). 배양실 냉동기 수리는 어느 방 것인지 기록이 없어 배양실1·2 둘 다에, "
                       "번호 없이 '냉각실'만 적은 수리는 냉각1실·냉각실2 둘 다에 셉니다. 살균솥(가마)은 '살균솥 N호기'로 표시합니다.")
            S = G.groupby("_key").agg(건수=("내용", "size"), 마지막=("date", "max"),
                                     냉매누수=("분류", lambda s: int((s == "냉매·누수").sum()))).reset_index().rename(columns={"_key": "방", "냉매누수": "냉매·누수"})
            S["최근 90일"] = S["방"].map(G[G.date >= today - pd.Timedelta(days=90)].groupby("_key").size()).fillna(0).astype(int)
            last_txt = G.sort_values("date").groupby("_key").tail(1).set_index("_key")["내용"]
            S["마지막 수리"] = [f"{d:%Y-%m-%d} {A._short(last_txt.get(r), 30)}" for r, d in zip(S["방"], S["마지막"])]
            S = S.sort_values("방", key=lambda s: s.map(A.room_sort_key)).drop(columns=["마지막"])
            S["방"] = S["방"].map(disp)
            with st.expander(f"방별 표 ({len(S)}곳)"):
                st.dataframe(S, hide_index=True, **_FULL, height=min(520, 38 + 35 * len(S)))

    with TB["설비 가동률"]:
        _equip_tab(ver, RP)

    with TB["장소별 이력"]:
        P = A.repairs_by(X, "장소")
        if P.empty:
            st.info("이 조건에 맞는 수리 기록이 없습니다.")
        else:
            cnt = P.groupby("_key").agg(건수=("내용", "size"), 마지막=("date", "max")).sort_values(["건수", "마지막"], ascending=False)
            sel = st.selectbox("장소 (건수 많은 순)", list(cnt.index), key="rp_place",
                               format_func=lambda k: f"{k} — {cnt.loc[k, '건수']}건 · 마지막 {cnt.loc[k, '마지막']:%Y-%m-%d}")
            st.session_state["_ctx_note"] = f"장소 {sel}"
            Y = P[P["_key"] == sel].sort_values("date", ascending=False)
            env = load_env(ver)
            env_rooms = set(env.room) if not env.empty else set()
            # 고른 장소가 방이면 그 방만, 냉동기·공용처럼 방이 아니면 그 수리들이 걸린 방
            rooms = [sel] if sel in env_rooms else [r for r in dict.fromkeys(r for v in Y["방"] for r in A._split(v)) if r in env_rooms]
            if rooms:
                room = rooms[0] if len(rooms) == 1 else st.radio("환경 기록 방", rooms, horizontal=True, key="rp_room")
                charts = room_repair_chart(env, room, max(d0, env.date.min()), today, Y)
                for ch in charts:
                    st.altair_chart(ch, **_FULL)
                if charts:
                    st.caption("파란 선 실측 · 회색 점선 설정 · 주황 세로선 🔧 = 이 장소를 수리한 날 · 아래 칸 = 냉각기 가동률. "
                               "고친 뒤 온도가 설정으로 돌아왔는지, 수리 전에 냉각기가 쉬지 않고 돌았는지 볼 수 있습니다.")
            st.dataframe(Y[["date", "분류", "내용", "사양", "업체", "비고", "구역", "시트"]].rename(columns={"date": "날짜"}).assign(날짜=lambda d: d["날짜"].dt.strftime("%Y-%m-%d")),
                         hide_index=True, **_FULL, height=min(480, 38 + 35 * len(Y)))

    with TB["전체 목록"]:
        V = X.sort_values("date", ascending=False)[["date", "구역", "장소", "분류", "내용", "사양", "업체", "비고", "시트"]].rename(columns={"date": "날짜"})
        V = V.assign(날짜=V["날짜"].dt.strftime("%Y-%m-%d"))
        st.dataframe(V, hide_index=True, **_FULL, height=560)
        st.download_button("표 내려받기 (CSV, 엑셀에서 열림)", V.to_csv(index=False).encode("utf-8-sig"), file_name=f"수리이력_{today:%Y%m%d}.csv", mime="text/csv")

    with TB["분류·월별"]:
        if X.empty:
            st.info("이 조건에 맞는 수리 기록이 없습니다.")
        else:
            M = X.assign(월=X.date.dt.strftime("%Y-%m"))
            st.altair_chart(alt.Chart(M).mark_bar().encode(
                x=alt.X("월:O", title=None), y=alt.Y("count():Q", title="건수"),
                color=alt.Color("분류:N", legend=alt.Legend(orient="right", title=None), scale=alt.Scale(scheme="tableau20")),
                tooltip=["월:N", "분류:N", alt.Tooltip("count():Q", title="건수")]).properties(height=280, title="달마다 수리 건수 — 분류별"), **_FULL)
            pv = pd.crosstab(X["구역"], X["분류"])
            pv["합계"] = pv.sum(axis=1)
            st.markdown("**구역 × 분류 건수**")
            st.dataframe(pv.sort_values("합계", ascending=False), **_FULL)
            H = X[X["분류"] == "가습기 부품"]
            if len(H):
                with st.expander(f"가습기 부품 교체 (팽이 재배사 · 가습기 수리내역.xlsx, {len(H)}일)"):
                    rows = []
                    for _, r in H.iterrows():
                        for name, n in re.findall(r"([A-Za-z가-힣.]+)\s*(\d+)", str(r["내용"]).split("—")[-1]):
                            rows.append({"월": r["date"].strftime("%Y-%m"), "부품": name, "개수": int(n)})
                    if rows:
                        Hp = pd.DataFrame(rows).pivot_table(index="월", columns="부품", values="개수", aggfunc="sum", fill_value=0)
                        st.dataframe(Hp.sort_index(ascending=False), **_FULL)
                    st.caption("가습기 번호(YJ…)는 비고에 있습니다. 어느 재배실 가습기인지는 기록에 없습니다.")

    if is_admin:
        with TB["수리비 (완료 보고서)"]:
            if K is None or K.empty:
                st.info(f"수리 완료 보고서를 읽지 못했습니다. `{C.log_dir('kwon_ss')}` 의 '수리 완료 보고서.xls' — 옛 엑셀(.xls)은 xlrd 패키지가 필요합니다(없으면 자동 설치를 시도).")
            else:
                Kc = K.copy()
                Kc["연"] = Kc["date"].dt.year
                yr = Kc.groupby("연")["수리비"].sum()
                cols = st.columns(min(4, max(1, len(yr))))
                for col, (y_, v_) in zip(cols, list(yr.items())[-4:]):
                    col.metric(f"{int(y_)}년 수리비", f"{v_ / 1e4:,.0f}만원", f"{int((Kc['연'] == y_).sum())}건", delta_color="off")
                st.altair_chart(alt.Chart(Kc.assign(월=Kc["date"].dt.strftime("%Y-%m"))).mark_bar().encode(
                    x=alt.X("월:O", title=None), y=alt.Y("sum(수리비):Q", title="원"),
                    color=alt.Color("업체:N", legend=alt.Legend(orient="top", title=None)),
                    tooltip=["월:N", "업체:N", "수리명:N", alt.Tooltip("sum(수리비):Q", title="수리비", format=",.0f")]).properties(height=240), **_FULL)
                V2 = Kc.sort_values("date", ascending=False)[["date", "완료일", "구역", "수리명", "업체", "수리비", "기간", "내용"]].rename(columns={"date": "수리일"})
                V2["수리일"] = V2["수리일"].dt.strftime("%Y-%m-%d"); V2["완료일"] = V2["완료일"].dt.strftime("%Y-%m-%d")
                st.dataframe(V2, hide_index=True, **_FULL, column_config={"수리비": st.column_config.NumberColumn(format="%d")},
                             height=min(520, 38 + 35 * len(V2)))
                st.caption("수리 완료 보고서.xls 의 보고서 한 장 = 한 줄. 일백(냉동) 공사는 한두 달 치를 묶어 한 장으로 내므로 방별로 나누지 않았습니다. "
                           "금액은 보고서에 적힌 수리비(견적서 기준)입니다.")


# ───────────────────────── 페이지: 원가 ─────────────────────────
@st.cache_data(show_spinner="원가 계산 중…")
def load_cost(ver):
    return A.cost_monthly()


def page_cost(ver):
    st.title("원가 — 병당 재료·에너지 원가")
    out, price = load_cost(ver)
    if out.empty:
        st.info("원가 자료가 없습니다. 재배 관리자 폴더(원재료입고.xlsx, 전기세.xlsx, 가스 및 경유사용량.xlsx, 재배일보)가 필요합니다."); return
    st.caption("병당 원가(원/병) = 원재료비 + 연료비(LPG·경유, 두 작물 입병수 합으로 배분) + 배양 전기(공통 계량기, 입병수 합으로 배분) + 생육 전기(작물별 계량기 ÷ 그 작물 입병수). "
               "인건비·포장재·종균·감가상각은 포함하지 않습니다. 월말보고서와 같은 산식이며, 보고서 값이 있는 달은 옆에 같이 보여 줍니다.")
    done = out.dropna(subset=["합계"])
    # 최근 달 지표
    if not done.empty:
        last_m = done["월"].max()
        cols = st.columns(4)
        for i, c in enumerate(("팽이", "새송이")):
            r = done[(done["월"] == last_m) & (done["작물"] == c)]
            if len(r):
                r = r.iloc[0]
                prev = done[(done["작물"] == c) & (done["월"] < last_m)].sort_values("월").tail(1)
                delta = f"{r['합계'] - prev.iloc[0]['합계']:+.1f}원 (전월 대비)" if len(prev) else None
                cols[i * 2].metric(f"{c} 병당 원가 {last_m[5:]}월", f"{r['합계']:.1f}원", delta, delta_color="inverse")
                cols[i * 2 + 1].metric(f"{c} 원재료비", f"{r['원재료비']:.1f}원"); cols[i * 2 + 1].caption(f"입병 {r['입병수']:,.0f}병 · {r['입병일수']:.0f}일" if pd.notna(r['입병일수']) else f"입병 {r['입병수']:,.0f}병")
    # 구성 막대 그래프
    long = done.melt(id_vars=["월", "작물"], value_vars=["원재료비", "연료비", "배양전기", "생육전기"], var_name="항목", value_name="원/병")
    if not long.empty:
        ch = alt.Chart(long).mark_bar().encode(
            x=alt.X("월:N", title=None), y=alt.Y("sum(원/병):Q", title="원/병"),
            color=alt.Color("항목:N", scale=alt.Scale(domain=["원재료비", "연료비", "배양전기", "생육전기"], range=["#2a78d6", "#eb6834", "#d6a12a", "#1baf7a"]), legend=alt.Legend(orient="top", title=None)),
            column=alt.Column("작물:N", title=None, header=alt.Header(labelFontSize=13)),
            tooltip=["월", "작물", "항목", alt.Tooltip("원/병:Q", format=".1f")]).properties(height=280, width=max(260, 22 * long["월"].nunique() + 60)).resolve_scale(x="independent")
        st.altair_chart(ch)
    # 표
    show = out.sort_values(["월", "작물"], ascending=[False, True])
    show = show[["월", "작물", "입병수", "입병일수", "원재료비", "연료비", "배양전기", "생육전기", "합계", "보고서_합계", "재료비출처", "입병수출처"]].copy()
    show["보고서 차이"] = show["합계"] - show["보고서_합계"]
    st.dataframe(show.rename(columns={"보고서_합계": "월말보고 합계"}), hide_index=True, **_FULL,
                 column_config={c: st.column_config.NumberColumn(format="%.1f") for c in ("원재료비", "연료비", "배양전기", "생육전기", "합계", "월말보고 합계", "보고서 차이")}
                 | {"입병수": st.column_config.NumberColumn(format="%d"), "입병일수": st.column_config.NumberColumn(format="%d")})
    st.caption("보고서 차이는 주로 전기요금 배분 방식(계량기 역할)과 새송이 입병수 집계 기준 차이에서 생깁니다. 원재료비는 원재료입고.xlsx 값을 그대로 씁니다.")
    # 금액(원) 표
    with st.expander("월별 금액(원) 보기"):
        won = out.sort_values(["월", "작물"], ascending=[False, True])[["월", "작물", "입병수", "원재료비_원", "연료비_원", "배양전기_원", "생육전기_원"]]
        st.dataframe(won, hide_index=True, **_FULL, column_config={c: st.column_config.NumberColumn(format="%d") for c in won.columns if c != "월" and c != "작물"})
    # 단가 추이
    if not price.empty:
        st.subheader("원재료 단가 추이 (원/kg)")
        items = [c for c in ("콘컵", "미강", "소맥피", "비트", "대두피", "파옥쇄", "옥베아박", "대두박") if c in price.columns]
        pl = price[items].reset_index().melt(id_vars="month", var_name="품목", value_name="단가").dropna()
        pl = pl[pl.month >= "2024-01"]
        ch = alt.Chart(pl).mark_line(point=True).encode(x=alt.X("month:N", title=None), y=alt.Y("단가:Q", title="원/kg", scale=alt.Scale(zero=False)),
                                                        color=alt.Color("품목:N", legend=alt.Legend(orient="top", title=None)),
                                                        tooltip=["month", "품목", "단가"]).properties(height=280)
        st.altair_chart(ch, **_FULL)
        with st.expander("단가 표"):
            st.dataframe(price[items].sort_index(ascending=False), **_FULL)

    # ── 매출·마진 ──
    st.subheader("매출과 병당 마진 (재배 관리자 매출분석)")
    SM = load_sales(ver)
    if SM.empty:
        st.info("매출 자료가 없습니다 (정규철 작업 일지의 매출분석 시트).")
    else:
        crop_s = st.radio("작물", ["팽이", "새송이"], horizontal=True, key="sales_crop")
        X = SM[SM.작물 == crop_s].copy()
        cur = X[X.연 == X.연.max()]
        if "병당마진" in X and cur["병당마진"].notna().any():
            last = cur.dropna(subset=["병당마진"]).iloc[-1]
            k1, k2, k3, k4 = st.columns(4)
            k1.metric(f"{last.월} 매출", f"{last.매출원/1e6:,.0f}백만원", f"출고 {last.출고kg:,.0f}kg", delta_color="off")
            k2.metric("병당 매출", f(last.병당매출, 0, "원"), f"단가 {last.단가kg:,.0f}원/kg", delta_color="off")
            k3.metric("병당 원가(재료·에너지)", f(last.병당원가, 0, "원"))
            k4.metric("병당 마진", f(last.병당마진, 0, "원"), f"{100*last.병당마진/last.병당매출:.0f}% (인건비·포장재 제외)", delta_color="off")
        c1, c2 = st.columns(2)
        Y = X.dropna(subset=["매출원"])
        c1.altair_chart(alt.Chart(Y).mark_line(point=True).encode(
            x=alt.X("월n:O", title="월"), y=alt.Y("매출원:Q", title="매출(원)"), color=alt.Color("연:N", title=None, legend=alt.Legend(orient="top")),
            tooltip=["월:N", alt.Tooltip("매출원:Q", format=",.0f"), alt.Tooltip("출고kg:Q", format=",.0f"), alt.Tooltip("단가kg:Q", format=",.0f")]).properties(height=240, title="월 매출 — 연도 비교"), **_FULL)
        c2.altair_chart(alt.Chart(Y).mark_line(point=True).encode(
            x=alt.X("월n:O", title="월"), y=alt.Y("단가kg:Q", title="원/kg", scale=alt.Scale(zero=False)), color=alt.Color("연:N", title=None, legend=alt.Legend(orient="top")),
            tooltip=["월:N", alt.Tooltip("단가kg:Q", format=",.0f")]).properties(height=240, title="판매 단가(원/kg) — 연도 비교"), **_FULL)
        if "병당마진" in X and X["병당마진"].notna().any():
            Z = X.dropna(subset=["병당마진"]).melt(id_vars=["월"], value_vars=["병당원가", "병당마진"], var_name="구분", value_name="원")
            st.altair_chart(alt.Chart(Z).mark_bar().encode(
                x=alt.X("월:N", title=None), y=alt.Y("원:Q", title="원/병", stack=True), color=alt.Color("구분:N", scale=alt.Scale(domain=["병당원가", "병당마진"], range=["#8a8f84", "#1baf7a"]), legend=alt.Legend(orient="top", title=None)),
                tooltip=["월:N", "구분:N", alt.Tooltip("원:Q", format=",.0f")]).properties(height=220, title="병당 매출 = 원가 + 마진 (원가 자료 있는 달)"), **_FULL)
        T = X.sort_values("월", ascending=False)[[c for c in ["월", "출고kg", "매출원", "단가kg", "단가박스", "수확병수", "병당g", "입병수", "병당매출", "병당원가", "병당마진"] if c in X]].copy()
        T["매출원"] = T["매출원"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "—")
        st.dataframe(T.round(0), hide_index=True, **_FULL, height=320)
        st.caption("매출·출고·단가는 재배 관리자 매출분석 시트(연도 비교표) 값. 병당 매출 = 월 매출 ÷ 그 달 입병수(원가 페이지와 같은 기준)라 입병과 출고의 시차가 있는 근사값입니다.")

    with st.expander("계산 방식·계량기 역할"):
        st.markdown("- 입병수: 원재료입고.xlsx 월 블록의 입병수를 우선 사용, 없으면 재배일보(종합관리)의 혼합기별 수량 합계\n"
                    "- 원재료비: 원재료입고.xlsx의 작물별 금액 → 없으면 작물별 kg×단가 → 없으면 최근 배합비(kg/입병일)×입병일수×단가\n"
                    "- 연료비: 가스·경유 충진 금액 월 합계 ÷ (팽이+새송이 입병수)\n"
                    "- 전기: 전기세.xlsx 계량기별 요금을 아래 역할로 배분 (config.py METER_ROLE 에서 변경)")
        st.dataframe(pd.DataFrame([{"계량기 이름에 포함": k, "역할": v} for k, v in C.METER_ROLE.items()]), hide_index=True)



# ───────────────────────── 페이지: 재고 대사 ─────────────────────────
@st.cache_data(show_spinner="재고 대사 중…")
def load_recon(ver, crop):
    return A.stock_recon(crop)


def page_stock(ver):
    st.title("재고 대사 — 생산·판매·재고 정확성")
    st.caption("생산 상세일지의 '입출고내역'(재고·입고·출고)을 읽어 매달 기초 + 입고 − 출고 = 계산 기말이 다음 달 기초와 맞는지 확인합니다. "
               "차이(월초 조정)는 실사·정정으로 고쳐 넣은 양이라 장부와 실물의 차이를 가늠하는 지표입니다. 일별로 출고가 장부 재고를 넘는 경우(음수 잔고)와 단가 없는 출고도 같이 봅니다.")
    crop = st.radio("작물", ["팽이", "새송이"], horizontal=True, key="stock_crop")
    R, I, N, U = load_recon(ver, crop)
    if R.empty:
        st.info("입출고내역 자료가 없습니다. 생산 상세일지(팽이) / 새송이 상세일지에 '입출고내역' 시트가 있어야 합니다."); return
    done = R.dropna(subset=["월초조정"])
    c = st.columns(5)
    last = R.iloc[-1]
    c[0].metric("장부 기간", f"{len(R)}개월", f"{R['월'].iloc[0]} ~ {R['월'].iloc[-1]}", delta_color="off")
    c[1].metric("월초 조정 발생", f"{int((done['월초조정'].abs() > 1).sum())} / {len(done)}개월", "기준 |조정| > 1kg", delta_color="off")
    big = done.loc[done["월초조정"].abs().idxmax()] if len(done) else None
    c[2].metric("최대 조정", f"{big['월초조정']:+,.0f} kg" if big is not None else "—", f"{big['월']} · 입고의 {big['조정률%']:+.1f}%" if big is not None else None, delta_color="inverse")
    c[3].metric("일별 음수 잔고", f"{len(N)}건", "출고 > 장부 재고", delta_color="off")
    c[4].metric("최근 기말 재고", f"{last['계산기말']:,.0f} kg", f"{last['기말재고일수']:.1f}일치 출고량", delta_color="off")
    # 그래프: 월초조정 막대 + 기말재고일수 선
    ch1 = alt.Chart(done).mark_bar().encode(x=alt.X("월:N", title=None), y=alt.Y("월초조정:Q", title="월초 조정 (kg)"),
                                            color=alt.condition(alt.datum.월초조정 > 0, alt.value("#eb6834"), alt.value("#2a78d6")),
                                            tooltip=["월", alt.Tooltip("월초조정:Q", format=",.0f"), alt.Tooltip("조정률%:Q", format=".1f")]).properties(height=200)
    ch2 = alt.Chart(R).mark_line(point=True, color="#1baf7a").encode(x=alt.X("월:N", title=None), y=alt.Y("기말재고일수:Q", title="기말 재고 (일치 출고량)"),
                                                                     tooltip=["월", alt.Tooltip("계산기말:Q", format=",.0f"), alt.Tooltip("기말재고일수:Q", format=".1f")]).properties(height=200)
    c1, c2 = st.columns(2)
    c1.altair_chart(ch1, **_FULL); c2.altair_chart(ch2, **_FULL)
    st.caption("월초 조정: 양수 = 다음 달 기초가 계산 기말보다 많음(장부보다 실물이 많았음 → 출고 과다 기록 또는 입고 누락), 음수 = 반대. 기말 재고 일수가 3일을 넘으면 신선 버섯 특성상 장부 과대 가능성.")
    st.subheader("월별 대사")
    st.dataframe(R.sort_values("월", ascending=False), hide_index=True, **_FULL,
                 column_config={k: st.column_config.NumberColumn(format="%.0f") for k in ("기초재고", "입고", "출고", "계산기말", "일평균출고", "다음달기초", "월초조정", "항목별조정절대합")}
                 | {"기말재고일수": st.column_config.NumberColumn(format="%.1f"), "조정률%": st.column_config.NumberColumn(format="%+.1f")})
    KO = {"name": "규격명", "spec": "규격", "grade": "등급", "cust": "거래처"}
    I, N = I.rename(columns=KO), N.rename(columns=KO)
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("월초 조정 상세 (규격별)")
        st.dataframe(I.sort_values(["월"], ascending=False) if not I.empty else pd.DataFrame({"안내": ["조정 없음"]}), hide_index=True, **_FULL,
                     column_config={k: st.column_config.NumberColumn(format="%+.0f") for k in ("조정",)} | {k: st.column_config.NumberColumn(format="%.0f") for k in ("계산기말", "다음달기초")})
    with c2:
        st.subheader("일별 음수 잔고 (출고 > 장부 재고)")
        if N.empty:
            st.success("없음")
        else:
            key = [KO[k] for k in A.LEDGER_KEY[crop] if KO[k] in N.columns]
            g = N.groupby(["월"] + key).agg(일수=("장부잔고kg", "size"), 최저잔고kg=("장부잔고kg", "min")).reset_index().sort_values("월", ascending=False)
            st.dataframe(g, hide_index=True, **_FULL)
            st.caption("같은 규격이 입고와 출고에서 다른 등급/거래처로 적히면 생깁니다(예: 파지 등급 '특' vs '파지'). 규격 등록을 통일하면 사라집니다.")
    with st.expander(f"단가 없는 출고 ({len(U)}건)"):
        st.dataframe(U.sort_values("날짜", ascending=False), hide_index=True, **_FULL, column_config={"날짜": st.column_config.DateColumn(format="YYYY-MM-DD")})
        st.caption("최근 달은 미정산분이 대부분입니다. 오래된 달에 남아 있으면 정산 누락 가능성.")

# ───────────────────────── 페이지: 보고서 ─────────────────────────
def page_report(ver):
    st.title("보고서")
    st.caption("월간(8쪽: 요약·원가·배양실·수확 배치·이상 감시·살균·종균·재고)과 주간(2쪽: 생산·불량·살균·이상 요약) PDF·엑셀. "
               "매주 월요일 아침과 매월 1일 아침 갱신 때 자동으로도 만들어져 `분석결과\\01_리포트` 에 저장됩니다.")
    pm, sm, cm = load_monthly(ver)
    kind = st.radio("종류", ["월간", "주간"], horizontal=True, key="rp_kind")
    c1, c2 = st.columns([1, 3])
    if kind == "월간":
        months = sorted(set(pm["월"].astype(str)) | set(sm["월"].astype(str)) if not pm.empty or not sm.empty else set(), reverse=True)
        if not months:
            st.info("월별 자료가 없습니다."); return
        cur = pd.Timestamp.today().strftime("%Y-%m")
        key = c1.selectbox("대상 월", months, index=1 if (months[0] == cur and len(months) > 1) else 0)
        stem = f"월간보고서_{key}"
    else:
        today = pd.Timestamp.today().normalize()
        last_sun = today - pd.Timedelta(days=today.weekday() + 1)
        weeks = [last_sun - pd.Timedelta(days=7 * i) for i in range(12)]
        key = c1.selectbox("대상 주 (일요일 기준, 그 전 7일)", weeks, format_func=lambda d: f"{(d - pd.Timedelta(days=6)):%m/%d} ~ {d:%m/%d}")
        stem = f"주간보고서_{(key - pd.Timedelta(days=6)):%Y-%m-%d}_{key:%m-%d}"
    c2.write("")
    if c2.button("보고서 만들기", type="primary"):
        with st.spinner("보고서 생성 중… (10~20초)"):
            try:
                env = load_env(ver); K, _ = load_paengi(ver); B, _ = load_ss(ver); cost, _ = load_cost(ver)
                CY = load_retort_cycles(ver)
                if kind == "월간":
                    m_end = pd.Timestamp(key) + pd.offsets.MonthEnd(0)
                    an = A.anomalies(env, days=31, ref_date=m_end)
                    stock = {}
                    for crop in ("팽이", "새송이"):
                        try:
                            stock[crop] = A.stock_recon(crop)[0]
                        except Exception:
                            pass
                    out = RP.build_monthly(key, pm, sm, cm, cost, K, B, an, env, A.tank_rank(), cycles=CY, tanks=load_tanks(ver), stock=stock)
                else:
                    an = A.anomalies(env, days=8, ref_date=key)
                    out = RP.build_weekly(key, pm, K, B, an, env, CY)
                st.session_state["report"] = (stem, out)
            except Exception as e:
                st.error(f"생성 실패: {e}")
    if st.session_state.get("report") and st.session_state["report"][0] == stem:
        out = st.session_state["report"][1]
        d1, d2, d3 = st.columns(3)
        d1.download_button("📄 PDF 내려받기 (인쇄용)", out["pdf"], file_name=f"{stem}.pdf", mime="application/pdf", **_FULL)
        d2.download_button("📊 엑셀 내려받기 (표)", out["xlsx"], file_name=f"{stem}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **_FULL)
        if d3.button("📁 리포트 폴더에 저장", **_FULL):
            paths = RP.save(out, stem); st.success("저장: " + paths[0])
        st.image(out["png1"], caption="1쪽 미리보기 (나머지는 PDF에서)", **_FULL)
    saved = sorted(glob.glob(os.path.join(RP.OUT_DIR, "*보고서*.pdf")), key=os.path.getmtime, reverse=True)[:10]
    if saved:
        with st.expander(f"저장된 보고서 ({len(saved)}개 최근)"):
            for pth in saved:
                st.write(f"{os.path.basename(pth)} — {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(pth)))}")

# ───────────────────────── 페이지: AI 질문 ─────────────────────────
def page_ai(ver):
    st.title("AI 질문")
    key = ai_chat.api_key()
    if not key:
        st.info("AI 질문창을 쓰려면 Anthropic API 키가 필요합니다.")
        st.markdown(f"""1. https://console.anthropic.com 에서 API 키를 만듭니다 (사용량만큼 과금, 질문 1건에 보통 수십 원).
2. 키를 메모장으로 **`{ai_chat.KEY_FILE}`** 파일에 한 줄로 저장합니다. (또는 환경변수 `ANTHROPIC_API_KEY`)
   - 파일 이름은 `{'` 또는 `'.join(ai_chat.C.AI_KEY_NAMES)}` 중 아무거나 되고, Downloads 바로 아래 폴더에 두어도 찾습니다.
   - 다른 곳에 두었다면 `mushroom_app\\config.py` 의 `AI_KEY_FILE` 에 전체 경로를 적으세요.
3. 이 화면을 새로고침하면 질문창이 열립니다.

* 키 파일은 메인PC에만 있고 앱 화면에는 표시되지 않습니다. 직원 PC에서 접속해도 메인PC의 키로 동작합니다.""")
        with st.expander("앱이 찾아본 위치", expanded=False):
            for cand in ai_chat._key_candidates():
                st.write(("✅ " if os.path.isfile(cand) else "· ") + cand)
        return
    st.caption("키 출처: " + ("환경변수 ANTHROPIC_API_KEY" if os.environ.get("ANTHROPIC_API_KEY") else (ai_chat.key_path() or "")))
    loaders = ai_chat.make_loaders(A, etl_logs, lambda: load_env(ver), lambda: load_paengi(ver), lambda: load_ss(ver),
                                   lambda: load_monthly(ver), lambda d: load_anomalies(ver, d))
    if "ai_msgs" not in st.session_state:
        st.session_state.ai_msgs, st.session_state.ai_view = [], []
    c1, c2 = st.columns([5, 1])
    c1.caption(f"앱의 데이터 표와 일지 파일을 찾아 답합니다 (모델 {ai_chat.MODEL}). 예: \"8월 팽이 병당중량 평균은?\", \"최근 2주 센서 이상 많은 방\", \"전기세 파일 어디 있어?\", \"발이실07 올해 배치 QC 순위\"")
    if c2.button("대화 지우기"):
        st.session_state.ai_msgs, st.session_state.ai_view = [], []
        st.rerun()
    for role, text in st.session_state.ai_view:
        with st.chat_message(role, avatar=(ICON if role == "assistant" and os.path.exists(ICON) else None)):
            st.markdown(text)
    q = st.chat_input("질문을 입력하세요")
    if not q:
        return
    st.session_state.ai_view.append(("user", q))
    with st.chat_message("user"):
        st.markdown(q)
    msgs = st.session_state.ai_msgs + [{"role": "user", "content": q}]
    with st.chat_message("assistant", avatar=ICON if os.path.exists(ICON) else None):
        with st.status("자료를 찾는 중…", expanded=False) as status:
            log = []
            def on_tool(name, args):
                label = {"describe_tables": "표 목록 확인", "run_pandas": "집계 실행", "find_files": "파일 찾기: " + str(args.get("keywords", "")),
                         "read_excel": "엑셀 읽기: " + os.path.basename(str(args.get("path", "")))}.get(name, name)
                log.append(label); status.update(label=label)
                if name == "run_pandas":
                    st.code(args.get("code", ""), language="python")
            try:
                text, new_msgs = ai_chat.ask(msgs, loaders, on_tool=on_tool)
                status.update(label=f"완료 ({len(log)}회 조회)", state="complete")
            except Exception as e:
                text, new_msgs = f"오류가 났습니다: {e}", msgs
                status.update(label="오류", state="error")
        st.markdown(text)
    st.session_state.ai_msgs = new_msgs
    st.session_state.ai_view.append(("assistant", text))

# ───────────────────────── 페이지: 일일 확인 ─────────────────────────
@st.cache_data(show_spinner="일지 기록 확인 중…")
def load_checklist(ver, date, days, today):
    return A.daily_checklist(date, days, today=today)


def page_checklist(ver):
    st.title("일일 확인")
    st.caption("오늘·어제 일지가 들어왔는지, 계획대로 입병·접종·균긁기·권지·살균·수확이 됐는지 한눈에 봅니다. "
               "'빠짐'은 지난 8주 같은 요일에 대개 기록이 있었는데 이번엔 없는 항목이고, 일요일처럼 원래 없는 날은 표시하지 않습니다. "
               "배양센터 기록은 일과 중에 작업이 안 끝나면 하루∼이틀 뒤에 적으므로, 그 사이에는 '입력 대기'로 봅니다.")
    c1, c2 = st.columns([1, 3])
    today = pd.Timestamp.today().normalize()
    d0 = c1.date_input("기준일", value=(today - pd.Timedelta(days=1)).date(), key="ck_date")
    days = c2.slider("표시 일수", 3, 14, 7, key="ck_days")
    T, EX, ST = load_checklist(ver, str(d0), days, str(today.date()))
    if T.empty:
        st.info("확인할 일지 자료가 없습니다."); return
    dates = [c for c in T.columns if not isinstance(c, str)]
    stt = lambda item, d: ST.get(item, {}).get(d, ("none", False))
    # 빠짐·입력 대기 집계
    miss, wait = [], []
    for _, r in T.iterrows():
        for d in dates:
            s0, _c = stt(r["항목"], d)
            if s0 == "miss":
                miss.append({"날짜": d, "구분": r["구분"], "항목": r["항목"]})
            elif s0 == "wait":
                wait.append({"날짜": d, "구분": r["구분"], "항목": r["항목"]})
    M = pd.DataFrame(miss); W = pd.DataFrame(wait)
    k1, k2, k3 = st.columns(3)
    first = dates[0]
    m0 = M[M["날짜"] == first] if not M.empty else pd.DataFrame()
    w0 = W[W["날짜"] == first] if not W.empty else pd.DataFrame()
    k1.metric(f"{first:%m/%d (%a)} 기록 항목", f"{sum(1 for _, r in T.iterrows() if isinstance(r[first], str))} / {len(T)}",
              (f"빠짐 {len(m0)}건" if len(m0) else "빠짐 없음") + (f" · 입력 대기 {len(w0)}건" if len(w0) else ""),
              delta_color="inverse" if len(m0) else "off")
    k2.metric(f"최근 {days}일 빠진 기록", f"{len(M)}건",
              (", ".join(M['항목'].value_counts().head(3).index) if len(M) else "없음") + (f" (입력 대기 {len(W)}건 별도)" if len(W) else ""), delta_color="off")
    lastlog = {}
    for _, r in T.iterrows():
        have = [d for d in dates if isinstance(r[d], str)]
        lastlog[r["항목"]] = max(have) if have else None
    stale = [k for k, v in lastlog.items() if v is None or (first - v).days >= 3]
    k3.metric("3일 이상 기록 없는 항목", f"{len(stale)}개", ", ".join(stale[:3]) + (" 외" if len(stale) > 3 else "") if stale else "없음", delta_color="inverse" if stale else "off")

    # 공정 경고: 접종 배지온도 · 냉각 느림 · 냉각실 과냉 · 살균 지연
    PA = load_process_alerts(ver, str(dates[-1].date()), str(first.date()))
    st.subheader(f"공정 경고 (최근 {days}일)")
    if len(PA):
        p0 = PA[PA["날짜"] == first.date()]
        st.caption(f"{first:%m/%d} {len(p0)}건 · 최근 {days}일 {len(PA)}건 — 솥 단위 기록(접종 배지온도·살균)과 냉각실 1분 곡선에서 자동 판정")
        SEV = {"높음": "🔴", "중간": "🟠", "낮음": "🟡"}
        Vp = PA.copy(); Vp["날짜"] = pd.to_datetime(Vp["날짜"]).dt.strftime("%m/%d"); Vp["심각도"] = Vp["심각도"].map(lambda x: SEV.get(x, "") + " " + x)
        st.dataframe(Vp[["날짜", "심각도", "분류", "방", "내용"]], hide_index=True, **_FULL, height=60 + 35 * min(len(Vp), 12))
    else:
        st.success(f"최근 {days}일 접종 배지온도·냉각 느림·과냉·살균 지연 없음")

    st.subheader(f"{first:%m/%d (%a)} 항목별 기록")
    V = T[["구분", "항목", first]].copy()
    def mark(r):
        v = r[first]
        if isinstance(v, str):
            return "✅ " + v
        s0, corro = stt(r["항목"], first)
        tail = " · 재배관리엔 기록 있음" if corro else ""
        if s0 == "wait":
            return "🕘 입력 대기 (보통 1~2일 뒤에 적습니다)" + tail
        if s0 == "miss":
            return "⚠️ 빠짐 (평소 이 요일엔 기록 있음)" + tail
        return "— 기록 없음 (이 요일엔 보통 없음)"
    V["기록"] = V.apply(mark, axis=1)
    st.dataframe(V[["구분", "항목", "기록"]], hide_index=True, **_FULL, height=60 + 35 * len(V))

    st.subheader(f"최근 {days}일 기록 현황")
    G = T[["구분", "항목"] + dates].copy()
    sym = {"ok": "✅", "wait": "🕘", "miss": "⚠️", "none": "·"}
    for d in dates:
        G[d] = [sym[stt(it, d)[0]] for it in G["항목"]]
    G.columns = ["구분", "항목"] + [d.strftime("%m/%d\n%a") for d in dates]
    st.dataframe(G, hide_index=True, **_FULL, height=60 + 35 * len(G))
    st.caption("✅ 기록 있음 · 🕘 입력 대기(아직 적을 시간이 남음) · ⚠️ 평소엔 있는데 빠짐 · · 원래 없는 날. "
               "셀 내용은 위 표(기준일)에서, 다른 날은 기준일을 바꿔 보세요.")
    with st.expander("항목별 상세 (최근 일수 전체)"):
        W = T.copy(); W.columns = ["구분", "항목"] + [d.strftime("%m/%d") for d in dates]
        st.dataframe(W, hide_index=True, **_FULL, height=60 + 35 * len(W))
    st.caption("자료: 재배일보(입병·살균), 살균솥 기록, 배양소관리(접종·균긁기·권지), 종균 관리 대장, 팽이 생산 상세일지·일일작업일지, 새송이 일일작업일지·상세일지(장부), 유니팜 로그. "
               "일지는 오전·오후 갱신 때 반영되므로 '어제'가 기본입니다.")


# ───────────────────────── 페이지: 배치 이력 ─────────────────────────
VERDICT_MARK = {"나쁨": "🔴 나쁨", "주의": "🟠 주의", "평소와 다름": "🟡 평소와 다름", "약간 다름": "🟡 약간 다름", "좋음": "🟢 좋음", "보통": "⚪ 보통", "자료 없음": "— 자료 없음", "환경 기록 이전": "🕘 환경 기록 이전"}
STAGE_ORDER = ["종균", "배지", "살균", "냉각", "접종", "배양", "발이", "생육", "수확"]


DO_ORDERS = ["최근 순", "좋은 순", "나쁜 순"]
GRADE_MARK = {"좋음": "🟢 좋음", "보통": "⚪ 보통", "미흡": "🔴 미흡"}


@st.cache_data(show_spinner=False)
def load_rank(ver, basis):
    return A.rank_table(load_stage_table(ver), basis)


def _do_step(key, opts, delta):
    """◀ ▶ 버튼: 같은 순서의 앞·뒤 배치로 (버튼 콜백이라 선택 상자를 그리기 전에 값이 바뀐다)"""
    cur = st.session_state.get(key)
    i = opts.index(cur) if cur in opts else 0
    st.session_state[key] = opts[min(max(i + delta, 0), len(opts) - 1)]


RANK_OUTCOME_ITEMS = {"QC 점수", "균긁기 불량률", "권지 불량률", "병당중량"}     # 종합평가 표에 이미 있는 결과 지표


def _num2(v, mu, unit=""):
    """설명 문장용 '값 (평균 …)': 두 숫자를 같은 소수 자리로, 크기에 맞춰 줄인다 (24 (평균 15) · 4.4%p (평균 0.3%p) · 0.29℃ (평균 0.16℃))"""
    v, mu = float(v), float(mu)
    big = max(abs(v), abs(mu))
    nd = 0 if (big >= 100 or (v.is_integer() and abs(mu) >= 10)) else (1 if big >= 1 else 2)
    return f"{v:,.{nd}f}{unit}", f"{mu:,.{nd}f}{unit}"


def _grade_panel(ST, bid, basis, T, RTs, grp_label, grp_n):
    """종합평가 풀이: 비교군·순위, 세 지표(QC·불량률·병당중량)의 값·비교군 중앙값·위치·백분위 점수,
    가장 크게 깎인/좋은 지표, 다른 비교 기준으로 본 위치, QC 절대 판정, 공정에서 평소보다 나빴던 것(원인 후보)"""
    D = A.rank_detail(ST, bid, basis)
    if D is None:
        return
    with st.container(border=True):
        if D["rank"] is None:
            st.markdown(BR._md(f"**종합평가: 평가 없음** — QC·불량률·병당중량 중 빠진 것이 있어(수확 중이거나 기록 없음) 순위를 매기지 않았습니다. 비교군: {D['label']}"))
        else:
            st.markdown(BR._md(f"**{GRADE_MARK.get(D['grade'], D['grade'])}** · {D['label']} 수확 완료 **{D['n']}개 중 {D['rank']}위** "
                               f"({A.pos_text(D['rank'], D['n'])}) · 종합 점수 **{D['score']:.0f}점**"
                               + (" — 공식 종합평가(배치 탐색기·보고서와 같은 등급)" if D.get("basis_used") == "같은 계절" else "")))
        st.dataframe(pd.DataFrame(D["rows"]), hide_index=True, **_FULL,
                     column_config={"백분위 점수": st.column_config.ProgressColumn("백분위 점수", min_value=0, max_value=100, format="%d",
                                                                              help="비교군 안에서 이 지표의 위치 (100 = 가장 좋음, 0 = 가장 나쁨)")})
        lines = []
        if D["worst"]:
            lines.append(f"🔻 **가장 크게 깎인 지표**: {D['worst']}")
        if D["best"]:
            lines.append(f"🔺 **가장 좋은 지표**: {D['best']}")
        others = []
        for b_ in A.RANK_BASES:
            R_ = RTs.get(b_)
            if b_ == basis or R_ is None or bid not in R_.index or pd.isna(R_.at[bid, "순위"]) or R_.at[bid, "기준"] != b_:
                continue          # 같은 달이 적어 같은 계절로 바뀐 것은 같은 계절과 겹치므로 뺀다
            r_ = R_.loc[bid]
            nm = "같은 계절(공식)" if b_ == "같은 계절" else b_
            others.append(f"{nm} {GRADE_MARK.get(r_['등급'], r_['등급'])} {int(r_['배치수'])}개 중 {int(r_['순위'])}위({A.pos_text(r_['순위'], r_['배치수'])})")
        if others:
            lines.append("**다른 비교 기준으로 보면**: " + " · ".join(others))
        if str(D.get("qc_band") or "—") not in ("—", "nan", "None"):
            lines.append(f"**QC 절대 판정**(품질 기준서 앵커 85 양호 / 79 보통): {D['qc_band']}")
        if T is not None and not T.empty:
            x = T[T["판정"].isin(["나쁨", "주의"]) & ~T["항목"].isin(RANK_OUTCOME_ITEMS)]
            x = x.assign(_a=x["z"].abs()).sort_values("_a", ascending=False).head(5)
            items = []
            for _, r in x.iterrows():
                v_, m_ = _num2(r["값"], r["평균"], r["단위"])
                items.append(f"{r['항목'] if r['단계'] in r['항목'] else r['단계'] + ' ' + r['항목']} {v_} (평균 {m_}, {r['판정']})")
            lines.append("**공정에서 평소보다 나빴던 것**(원인 후보): " + (" · ".join(items) if items else "없음"))
        if lines:
            st.markdown("\n".join("- " + BR._md(l) for l in lines))
        notes = []
        if D["rank"] is not None:
            notes.append("종합 점수 = 세 지표 백분위 점수의 평균(100점 = 비교군에서 가장 좋음) — 비교군 상위 25% 안이면 좋음, 하위 25% 안이면 미흡, 그 사이는 보통.")
        if T is not None and not T.empty:
            notes.append(f"원인 후보 = {grp_label} {grp_n}개 배치 평균과 견준 공정 지표 중 '나쁨'·'주의'(결과 지표인 QC·불량률·병당중량은 위 표에) — 전체 목록은 아래 '단계별 지표 비교' 탭.")
        if notes:
            st.caption(BR._md(" ".join(notes)))


def _dossier_tab(ver, ST, K, PE):
    """배치 이력서: 순서·비교 기준 → 배치 고르기(◀ ▶) → 종합평가 풀이 → 단계 카드 → 수리 기록 → 공정 상세 / 단계별 지표 / 환경 곡선"""
    c_o, c_b = st.columns([1, 1.5])
    order = c_o.radio("배치 순서", DO_ORDERS, key="do_ord", horizontal=True,
                      help="좋은 순·나쁜 순 = 오른쪽 '비교 기준' 안에서의 위치(상위·하위 %) 순서. 위치가 같으면 종합 점수 순.")
    basis = c_b.radio("비교 기준", list(A.RANK_BASES), key="do_basis", horizontal=True,
                      help="같은 계절 = 입상 계절이 같은 배치(공식 종합평가와 같은 기준) · 전체 = 기간 전체 · 같은 달 = 입상한 달이 같은 배치 "
                           "(그 달 배치가 8개보다 적으면 같은 계절로 넓힘). 순위는 QC 서식(2026-06-19 A→B)이 같은 배치끼리 매깁니다(서식이 바뀌며 QC 점수 수준이 달라짐). "
                           "바꾸면 순위·종합평가 풀이·단계 카드 색·단계별 지표의 평균·곡선의 비교선·공정 상세의 이슈 판정이 모두 이 기준으로 바뀝니다.")
    RTs = {b_: load_rank(ver, b_) for b_ in A.RANK_BASES}
    done = ST[ST["완결"]].merge(RTs[basis], left_on="bid", right_index=True, how="left")
    done["_top"] = done["순위"] / done["배치수"]             # 정렬은 반올림 전 위치로 (상위 0.5%와 1%를 가르게)
    if order == "좋은 순":
        done = done.sort_values(["_top", "점수", "수확시작"], ascending=[True, False, False], na_position="last")
    elif order == "나쁜 순":
        done = done.sort_values(["_top", "점수", "수확시작"], ascending=[False, True, False], na_position="last")
    else:
        done = done.sort_values("수확시작", ascending=False)
    labels = {}
    for _, r in done.iterrows():
        pos = A.pos_text(r["순위"], r["배치수"])
        if pos and r.get("기준") not in (None, basis) and pd.notna(r.get("기준")):
            pos += f"({r['기준']})"          # 같은 달 배치가 적어 같은 계절로 매긴 순위
        labels[r["bid"]] = (f"{GRADE_MARK.get(r['등급'], '— 평가 없음')}{' ' + pos if pos else ''} · {r['bid']} · 수확 {r['수확시작']:%m/%d} · "
                            f"생육실{int(r['생육실'])} · {f(r['병당중량_표시'], 0, 'g')} · QC {f(r['QC'], 0)}")
    opts = list(labels)
    if not opts:
        st.info("수확이 끝난 배치가 없습니다."); return
    # 순서·비교 기준마다 선택 상자를 따로 둔다(key) — 좋은 순·나쁜 순은 기준을 바꾸면 순서 자체가 바뀌므로 그 기준의 맨 위(가장 좋은·나쁜) 배치부터 다시 보여 준다.
    # 최근 순은 기준과 상관없는 순서라 보던 배치를 그대로 두고 등급·풀이만 바뀐다.
    skey = "do_bid_recent" if order not in ("좋은 순", "나쁜 순") else f"do_bid_{'best' if order == '좋은 순' else 'worst'}_{A.RANK_BASES.index(basis)}"
    if st.session_state.get(skey) not in labels:
        st.session_state.pop(skey, None)
    cs, cp, cn = st.columns([7, 1, 1], vertical_alignment="bottom")
    bid = cs.selectbox(f"배치 — {order} (수확 완료 {len(opts)}개)", opts, format_func=lambda k, L=labels: L[k], key=skey,
                       help="상자를 누르고 배치 번호를 치면 찾아집니다(예: P2605). ◀ ▶ 로 같은 순서의 앞·뒤 배치로 넘어갑니다.")
    i = opts.index(bid)
    cp.button("◀ 이전", key="do_prev", on_click=_do_step, args=(skey, opts, -1), disabled=i == 0, **_FULL)
    cn.button("다음 ▶", key="do_next", on_click=_do_step, args=(skey, opts, 1), disabled=i >= len(opts) - 1, **_FULL)
    st.caption(f"{order} {i + 1}번째 / {len(opts)}개 · 목록 앞의 등급과 상위·하위 %는 '{basis}' 기준"
               + (" — 기준을 바꾸면 그 기준의 맨 위 배치부터 다시 보여 줍니다" if order != "최근 순" else ""))
    st.session_state["_ctx_batch"] = bid
    st.session_state["_ctx_note"] = f"비교 기준 {basis}"
    T, b = A.batch_dossier(bid, ST, basis)
    if b is None:
        st.warning("배치를 찾을 수 없습니다."); return
    grp, grp_label = A.dossier_group(ST, b, basis)
    _grade_panel(ST, bid, basis, T, RTs, grp_label, len(grp))

    RB = A.batch_repairs(b, load_repairs(ver)[0])          # 이 배치가 거친 방·장비의 수리 기록 (v1.23.0)
    # 위쪽(단계 카드 🔧·수리 기록 상자)에는 냉각실·배양실 수리를 빼고 보여 준다 — 그 둘은 아래 '공정 상세'의 4. 냉각·6. 배양에만 (v1.24.5)
    HIDE_TOP = {"냉각", "배양"}
    RBt = RB[~RB["단계"].isin(HIDE_TOP)] if len(RB) else RB
    n_hidden = len(RB) - len(RBt)
    rep_n = RBt["단계"].map(A.REPAIR_STAGE_CARD).value_counts() if len(RBt) else pd.Series(dtype=int)
    d = lambda x: x.strftime("%m/%d") if pd.notna(x) else "—"
    tx = lambda x: "—" if x is None or (isinstance(x, float) and np.isnan(x)) or str(x) in ("nan", "None", "") else str(x)
    cards = [
        ("종균", d(b.get("접종일")), tx(b.get('종균코드')), f"pH {f(b.get('종균pH'), 2)} · {tx(b.get('종균교반'))} · {tx(b.get('종균유래'))[:22]}"),
        ("배지", d(b.get("살균일")), f"pH {f(b.get('배지pH'), 2)} · 수분 {f(b.get('배지수분'), 1, '%')}", f"품온 {f(b.get('배지품온'), 1, '℃')} · 함량 {f(b.get('배지함량'), 0, 'g')} · 살균기 {tx(b.get('살균기'))}"),
        ("살균", d(b.get("살균일")), tx(b.get('살균솥')) if tx(b.get('살균솥')) != "—" else tx(b.get('살균기')), f"F0 {f(b.get('살균F0'), 0)} · 2차 {f(b.get('살균2차유지분'), 0, '분')} · 중단 {f(b.get('살균중단'), 0)}"),
        ("냉각", f"{d(b.get('살균일'))}→{d(b.get('접종일'))}", f"{f(b.get('냉각temp_max'), 0, '℃')} → {f(b.get('냉각temp_min'), 1, '℃')}", f"냉각실 평균 {f(b.get('냉각temp'), 1, '℃')} · 습도 {f(b.get('냉각hum'), 0, '%')} · 접종 시 배지 {f(b.get('접종온도'), 1, '℃')}"),
        ("접종", d(b.get("접종일")), f"{f(b.get('접종량'), 0)}병", f"손실 {f(b.get('접종손실률%'), 2, '%')}"),
        ("배양", f"{d(b.get('접종일'))}→{d(b.get('입상일'))}", f"{f(b.get('배양일수'), 0)}일", f"{f(b.get('배양temp'), 1, '℃')} · 습도 {f(b.get('배양hum'), 0, '%')} · 긁기불량 {f(b.get('긁기불량률%'), 2, '%')}"),
        ("발이", f"{d(b.get('입상일'))}→{d(b.get('권지일'))}", f"발이실{int(b['발이실']) if pd.notna(b.get('발이실')) else '—'} · {f(b.get('발이실체류'), 0)}일", f"{f(b.get('발이temp'), 1, '℃')} · 12~14일차 {f(b.get('억제12_14'), 1, '℃')} · 권지불량 {f(b.get('권지불량률%'), 2, '%')}"),
        ("생육", f"{d(b.get('권지일'))}→{d(b.get('수확시작'))}", f"생육실{int(b['생육실'])} · {f(b.get('생육실체류'), 0)}일", f"{f(b.get('생육temp'), 1, '℃')} · 습도 {f(b.get('생육hum'), 0, '%')} · CO2 {f(b.get('생육co2'), 0)}"),
        ("수확", f"{d(b.get('수확시작'))}~{d(b.get('수확종료'))}", f"{f(b.get('병당중량_표시'), 1, 'g')}", f"QC {f(b.get('QC'), 1)} · {f(b.get('실수확병수'), 0)}병 · 입상 후 {f(b.get('총일수'), 0)}일"),
    ]
    cols = st.columns(len(cards))
    worst = T[T["판정"].isin(["나쁨", "주의", "평소와 다름"])].groupby("단계").size() if not T.empty else pd.Series(dtype=int)
    badn = T[T["판정"] == "나쁨"].groupby("단계").size() if not T.empty else pd.Series(dtype=int)
    for col, (stage, when, main, sub) in zip(cols, cards):
        n = int(worst.get(stage, 0))
        color = "#e0312a" if (n >= 2 or int(badn.get(stage, 0)) >= 1) else ("#e08a00" if n == 1 else "#1baf7a")
        nr = int(rep_n.get(stage, 0))
        rep = f"<div style='font-size:0.72rem;color:#b35c00;font-weight:600;margin-top:2px'>🔧 수리 {nr}건</div>" if nr else ""
        col.markdown(f"<div style='border-top:4px solid {color};padding:6px 2px 0 2px'><div style='font-size:0.74rem;color:#6b7160'>{stage} · {when}</div>"
                     f"<div style='font-size:0.95rem;font-weight:700;margin:2px 0;word-break:break-all'>{main}</div><div style='font-size:0.72rem;color:#555'>{sub}</div>{rep}</div>", unsafe_allow_html=True)
    st.caption(BR._md(f"막대 색 = {grp_label} {len(grp)}개 배치 평균과 견준 단계별 판정: 초록 = 모두 보통 이상 · 주황 = 주의·평소와 다름 1개 · 빨강 = 나쁨이 있거나 2개 이상")
               + (" · 🔧 = 그 단계에서 이 배치가 거친 방·장비의 수리 기록 (아래)" if len(RBt) else ""))
    hid_txt = f" 냉각실·배양실 수리 {n_hidden}건은 아래 '공정 상세'의 4. 냉각·6. 배양에 있습니다." if n_hidden else ""
    if len(RBt):
        inside = RBt["시점"].isin(["머무는 중", "그날"])
        with st.expander(f"🔧 이 배치가 거친 방·장비의 수리 기록 {len(RBt)}건" + (f" — 머무는 동안 {int(inside.sum())}건" if inside.any() else ""), expanded=bool(inside.any())):
            st.dataframe(RBt.assign(날짜=RBt["날짜"].dt.strftime("%Y-%m-%d"))[["단계", "날짜", "시점", "장소", "분류", "내용", "업체", "비고"]],
                         hide_index=True, **_FULL, height=min(360, 38 + 35 * len(RBt)))
            st.caption("방(발이실·생육실)은 이 배치가 머문 기간과 그 앞뒤(들어오기 1\\~3일 전, 나간 뒤 3\\~7일 — 고친 날 전부터 문제가 있었을 수 있음), "
                       "공정 장비(입병·살균·접종·균긁기·권지)는 그날과 다음날의 수리입니다. 자료: 기계수리.xlsx (수리 이력 메뉴)." + hid_txt)
    else:
        st.caption("🔧 이 배치가 거친 발이실·생육실·공정 장비의 수리 기록 없음 (기계수리.xlsx 기준)." + hid_txt)

    t_detail, t_metric, t_curve = st.tabs(["공정 상세", "단계별 지표 비교", "환경 곡선"])
    with t_detail:
        st.caption("원재료 → 입병 → 살균 → 냉각 → 접종 → 배양 → 균긁기 → 발이실 → 권지 → 생육실 → 수확 순서의 원본 기록과 이슈 (배치 탐색기와 같은 화면)")
        BR.render_streamlit(bid, G=load_batch_report(ver, bid, basis))
    with t_metric:
        st.markdown(BR._md(f"**{grp_label} 수확 완료 {len(grp)}개 배치의 평균과 비교** (QC 점수는 같은 QC 서식 배치끼리)"))
        if not T.empty:
            only = st.checkbox("평소와 다른 항목만 보기", value=True, key="do_only",
                               help=f"전체 {len(T)}개 항목 중 '보통'·'자료 없음'을 빼고 봅니다. 끄면 모든 항목.")
            V = T[~T["판정"].isin(["보통", "자료 없음", "환경 기록 이전"])].copy() if only else T.copy()
            if V.empty:
                st.success("이 기준으로는 평소와 다른 항목이 없습니다. 아래는 전체 항목입니다."); V = T.copy()
            else:
                st.caption(f"{len(T)}개 항목 중 {len(V)}개")
            V["판정"] = V["판정"].map(VERDICT_MARK)
            V["값"] = [f"{v:,.2f}{u}" if pd.notna(v) else "—" for v, u in zip(V["값"], V["단위"])]
            V["평균"] = [f"{v:,.2f}{u}" if pd.notna(v) else "—" for v, u in zip(V["평균"], V["단위"])]
            V["차이"] = [f"{v:+,.2f}" if pd.notna(v) else "—" for v in V["차이"]]
            V["z"] = [f"{v:+.1f}" if pd.notna(v) else "—" for v in V["z"]]
            V["비교 배치"] = [f"{int(n_)}개" if pd.notna(n_) else "—" for n_ in V.get("n", pd.Series(np.nan, index=V.index))]
            # 표 안에서 또 스크롤되지 않도록 줄 수에 맞춰 높이를 준다 (자르지 않음)
            st.dataframe(V[["단계", "항목", "값", "평균", "차이", "z", "판정", "비교 배치"]], hide_index=True, **_FULL, height=38 + 35 * len(V) + 3)
            R_ = C.RULES
            st.caption(f"z = (값 − 평균) / 표준편차. 방향이 있는 항목(불량률·중량 등)은 나쁜 쪽으로 z {R_['z_warn']:g} 이상이면 '주의', {R_['z_bad']:g} 이상이면 '나쁨' "
                       f"(좋은 쪽 {R_['z_warn']:g} 이상은 '좋음'). 방향이 없는 항목은 |z| ≥ {R_['z_diff']:g} 일 때만 '평소와 다름'.")
    with t_curve:
        mine = PE[PE.bid == bid]
        if mine.empty:
            st.info("이 배치 기간의 환경 데이터가 없습니다.")
        else:
            cb = grp.bid.tolist()
            rules = {"균긁기·입상": 1, "생육실 입실": (b["권지일"] - b["입상일"]).days + 1}
            if pd.notna(b.get("수확시작")):
                rules["수확"] = (b["수확시작"] - b["입상일"]).days + 1
            if pd.notna(b.get("접종일")):
                rules["접종"] = (b["접종일"] - b["입상일"]).days + 1
            rmk = None
            RBr = RB[RB["단계"].isin(["냉각", "배양", "발이실", "생육실"])] if len(RB) else RB
            if len(RBr):
                rmk = pd.DataFrame({"cday": [(d_ - b["입상일"]).days + 1 for d_ in RBr["날짜"]],
                                    "label": [f"{d_:%m/%d} {s_} {A._short(t_, 30)}" for d_, s_, t_ in zip(RBr["날짜"], RBr["단계"], RBr["내용"])]})
            for col_, title, dom in (("temp", "온도(℃)", None), ("hum", "습도(%)", None), ("co2", "CO2(ppm)", None)):
                ser = [("이 배치", mine.rename(columns={col_: "y"}), PAL["실측"], False)]
                if col_ == "temp":
                    ser.append(("설정", mine.rename(columns={"sp_temp": "y"}), PAL["설정"], True))
                pr = prof_series(PE, cb, f"{re.split(r'[(—]', grp_label)[0].strip()} 평균 (n={len(cb)})", PAL["비교"], col_, dash=False, max_day=32, min_day=-35)
                if pr:
                    ser.append(pr)
                ch = curve_chart(ser, title, dom, rules, height=200, repairs=rmk if col_ == "temp" else None)
                if ch is not None:
                    st.altair_chart(ch, **_FULL)
            st.caption(BR._md(f"주황 선 = {grp_label} {len(cb)}개 배치의 일차별 평균." + (" 온도 그래프의 주황 세로선 🔧 = 이 배치가 머문 방(냉각·배양·발이·생육)을 수리한 날 (마우스를 올리면 내용)." if rmk is not None else "")))
    with st.expander("🖨 인쇄용 A4 PDF가 필요할 때 (위 '공정 상세'와 같은 내용)"):
        batch_report_box(ver, bid, preview=False, where="dossier", basis=basis)


def page_dossier(ver):
    st.title("배치 이력")
    ST = load_stage_table(ver)
    if ST.empty:
        st.info("팽이 배치 사슬이 없습니다. 배양소관리(권지 시트)와 팽이 일지가 필요합니다."); return
    K, PE = load_paengi(ver)
    tab1, tab2 = st.tabs(["배치 이력서", "병목 찾기"])

    with tab1:
        _dossier_tab(ver, ST, K, PE)

    with tab2:
        c1, c2, c3 = st.columns(3)
        side = st.radio("어느 쪽을 찾나", ["나쁜 배치 (병목)", "좋은 배치 (잘된 조건)"], horizontal=True, key="bn_side")
        side = "나쁨" if side.startswith("나쁜") else "좋음"
        G_ = "나쁜 배치" if side == "나쁨" else "좋은 배치"
        outcome = c1.selectbox("기준 지표", list(A.OUTCOMES), format_func=lambda k: A.OUTCOMES[k][0], key="bn_out")
        ev_opts = [f"{e[1]} 이전 ({e[0]})" for e in C.EVENTS] + [f"{e[1]} 이후 ({e[0]})" for e in C.EVENTS]
        period = c2.selectbox("기간", ["전체", "최근 90일", "최근 180일", "최근 365일"] + ev_opts, index=0, key="bn_period")
        frac = c3.slider(f"{G_} 비율({'하위' if side == '나쁨' else '상위'} %)", 10, 50, 25, 5, key="bn_frac") / 100
        pdays = {"최근 90일": 90, "최근 180일": 180, "최근 365일": 365}.get(period)
        STx = ST
        for e in C.EVENTS:
            if period == f"{e[1]} 이전 ({e[0]})":
                STx = ST[ST["접종일"] < pd.Timestamp(e[0])]
            elif period == f"{e[1]} 이후 ({e[0]})":
                STx = ST[ST["접종일"] >= pd.Timestamp(e[0])]
        R, B = A.bottleneck(STx, outcome, frac, pdays, side=side)
        if R.empty:
            st.info("비교할 배치가 부족합니다 (완결 배치 8개 이상 필요)."); return
        name = A.OUTCOMES[outcome][0]
        st.markdown(f"**{name}** 기준 {'하위' if side == '나쁨' else '상위'} {int(frac*100)}% 배치 **{len(B)}개**와 나머지 배치의 단계별 지표 차이. 효과크기 = 차이 ÷ 표준편차 (0.5 이상이면 눈에 띄는 차이, 1 이상이면 큰 차이)."
                    + ("" if side == "나쁨" else " 좋은 배치들이 공통으로 가진 조건 = 따라 할 만한 조건."))
        Rp = R[R["중요도"] >= 0.3].head(18).copy()
        # 한쪽 방향 막대: 길이 = 차이의 크기. 방향(고른 배치가 높았나 낮았나)은 막대 끝 글자로만
        Rp["크기"] = Rp["효과크기"].abs()
        Rp["방향"] = [f"{G_}가 더 높음 ▲" if v > 0 else f"{G_}가 더 낮음 ▼" for v in Rp["효과크기"]]
        Rp["표시"] = [f"{'▲' if v > 0 else '▼'} {b:,.1f}{u} vs {o:,.1f}{u}" for v, b, o, u in zip(Rp["효과크기"], Rp["대상 평균"], Rp["나머지 평균"], Rp["단위"])]
        base = alt.Chart(Rp).encode(y=alt.Y("항목:N", sort=list(Rp["항목"]), title=None))
        bars = base.mark_bar().encode(
            x=alt.X("크기:Q", title=f"차이의 크기 (표준편차 단위 · 방향은 막대 끝 ▲▼: {G_} 쪽이 높음/낮음)", scale=alt.Scale(domain=[0, float(Rp["크기"].max()) * 1.35])),   # 오른쪽에 글자 자리
            color=alt.Color("단계:N", scale=alt.Scale(domain=STAGE_ORDER, range=["#7b61ff", "#c9a227", "#e0312a", "#3bb6d6", "#e08a00", "#1baf7a", "#2a78d6", "#1f9e3a", "#8a8f84"]), legend=alt.Legend(orient="top", title=None)),
            tooltip=["단계:N", "항목:N", "방향:N", alt.Tooltip("대상 평균:Q", title=f"{G_} 평균", format=".2f"), alt.Tooltip("나머지 평균:Q", format=".2f"), alt.Tooltip("크기:Q", title="차이 크기", format=".2f"), "해석:N", "n:N"])
        labels = base.mark_text(align="left", dx=4, fontSize=10.5, color="#4a4f45").encode(x="크기:Q", text="표시:N")
        st.altair_chart((bars + labels).properties(height=28 * len(Rp) + 40), **_FULL)
        st.caption(f"막대는 모두 한쪽으로 뻗고 길수록 {G_}와 나머지의 차이가 큽니다. ▲ {G_} 쪽이 높았음 · ▼ 낮았음 (좋고 나쁨이 아니라 '어느 쪽이었나'). 값은 {G_} 평균 vs 나머지 평균.")
        V = R.copy()
        V = V.rename(columns={"대상 평균": f"{G_} 평균"})
        for c_ in (f"{G_} 평균", "나머지 평균", "차이"):
            V[c_] = [f"{v:,.2f}{u}" for v, u in zip(V[c_], V["단위"])]
        V["효과크기"] = V["효과크기"].map(lambda v: f"{v:+.2f}")
        st.dataframe(V[["단계", "항목", f"{G_} 평균", "나머지 평균", "차이", "효과크기", "해석", "n"]], hide_index=True, **_FULL, height=380)
        top = R[R["해석"].isin(["불리한 방향" if side == "나쁨" else "유리한 방향", "평소와 다름"])].head(3)
        if not top.empty:
            st.success(("가장 크게 갈린 단계: " if side == "나쁨" else "좋은 배치가 공통으로 가진 조건: ") + " → ".join(f"{r.단계}({r.항목})" for _, r in top.iterrows()) +
                       (". 같은 시기에 나빠졌을 뿐인지(계절) 원인인지는 '기간'을 바꿔 가며 반복해서 나오는 항목인지로 판단하세요." if side == "나쁨" else
                        ". 겨울 배치가 몰려 있으면 계절 효과일 수 있으니 '기간'을 좁혀서도 같은 항목이 나오는지 보세요."))
        with st.expander(f"{G_} {len(B)}개 목록 — 행을 클릭하면 아래에 그 배치의 세부 내역(배치 탐색기와 같은 화면)이 열립니다", expanded=True):
            cols_ = list(dict.fromkeys(c for c in ["bid", "입상일", "생육실", "발이실", outcome, "종균코드", "배지품온", "살균F0", "냉각temp_min", "접종온도", "긁기불량률%", "권지불량률%", "배양hum", "억제12_14", "생육temp", "QC", "병당중량_표시"] if c in B))
            Bv = B[cols_].rename(columns={"bid": "배치", "병당중량_표시": "병당중량"}).assign(입상일=B.입상일.dt.strftime("%Y-%m-%d")).round(2).reset_index(drop=True)
            ev = st.dataframe(Bv, hide_index=True, **_FULL, height=min(320, 38 + 35 * len(Bv)), on_select="rerun", selection_mode="single-row", key="bn_pick")
            rows = ev.selection.rows if ev is not None and hasattr(ev, "selection") else []
        st.caption("살균 항목은 살균솥 기록이 있는 2026-03 이후 접종분에만 값이 있습니다. 새송이는 방별 수확 자료가 일부 기간뿐이라 우선 팽이만 제공합니다.")
        if rows:
            bid_ = str(Bv.iloc[rows[0]]["배치"])
            st.session_state["_ctx_batch"] = bid_
            st.divider()
            st.subheader(f"배치 세부 내역 — {bid_}")
            BR.render_streamlit(bid_, G=load_batch_report(ver, bid_))
            st.divider(); st.markdown("**이 배치 보고서를 A4로**"); batch_report_box(ver, bid_, preview=False, where="bottleneck")


# ───────────────────────── 페이지: 종균·액탱크 ─────────────────────────
TANK_REF = [("시작", "1차 상승", "", "20"), ("18분", "", "98.5", "48"), ("29분", "", "100", "68"), ("42분", "1차 유지 시작", "102", "85"),
            ("72분", "1차 유지 종료(30분)", "102", "99"), ("73분", "2차 상승", "105", "100"), ("77분", "2차 유지 시작", "119", "103"),
            ("87분", "", "119", "110"), ("97분", "", "119.2", "113"), ("107분", "", "118.9", "115"), ("117분", "", "118.4", "117"),
            ("135분", "", "119", "118"), ("137분", "2차 유지 60분 경과", "118.8", "118"), ("246분", "169분 경과", "119", "118~119"),
            ("252분", "175분 경과", "119", "119"), ("315분", "2차 유지 종료(210분)", "119", "119")]


def page_spawn(ver):
    st.title("종균·액탱크")
    S = load_tanks(ver)
    if S.empty:
        st.info(f"종균 관리 대장을 아직 읽지 않았습니다. 데이터 관리 → '일지 다시 읽기' 를 누르세요. (원본: `{C.log_dir('cult_extra')}`)")
        return
    c1, c2, c3 = st.columns([1, 1, 2])
    crop = c1.radio("품종", ["전체", "팽이", "새송이"], horizontal=True, key="sp_crop")
    years = sorted(S.일시.dt.year.unique(), reverse=True)
    yr = c2.selectbox("연도", years, index=0, key="sp_year")
    X = S[S.일시.dt.year == yr]
    if crop != "전체":
        X = X[X.품종 == crop]
    origins = X["유래"].dropna().value_counts()
    sel_o = c3.multiselect("계통(유래)", list(origins.index), default=[], key="sp_origin", help="비워 두면 전체")
    if sel_o:
        X = X[X["유래"].isin(sel_o)]
    if X.empty:
        st.warning("해당 조건의 탱크 배치가 없습니다."); return
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("탱크 배치", f"{len(X)}개", f"팽이 {(X.품종=='팽이').sum()} · 새송이 {(X.품종=='새송이').sum()}", delta_color="off")
    k2.metric("계통(유래) 수", f"{X['유래'].nunique()}개")
    k3.metric("탱크 pH 평균", f(X.pH.mean(), 2), f"{X.pH.min():.2f}~{X.pH.max():.2f}" if X.pH.notna().any() else None, delta_color="off")
    k4.metric("액배양 일수", f(X.액배양일.mean(), 1, "일"), f"{X.액배양일.min():.0f}~{X.액배양일.max():.0f}" if X.액배양일.notna().any() else None, delta_color="off")
    k5.metric("균긁기 불량률", f(X["긁기불량률%"].mean(), 2, "%"), f"연결 {X['긁기불량률%'].notna().sum()}개 배치", delta_color="off")
    k6.metric("살균 기록 연결", f"{X.살균솥.notna().sum() if '살균솥' in X else 0}개", "살균 페이지 액탱크 사이클", delta_color="off")

    st.subheader("탱크 배치 목록 (최근 순)")
    ko = {"일시": "탱크 제조일", "품종": "품종", "탱크NO": "탱크", "코드": "종균 코드", "계통": "계통", "세대": "세대", "유래": "유래(조직분리)", "모균": "모균(플라스크)", "교반": "교반",
          "pH": "탱크 pH", "접종일": "접종일", "액배양일": "액배양(일)", "종균pH": "접종 시 pH", "접종배지": "접종 배지", "살균후pH": "배지 살균후 pH",
          "살균솥": "살균솥", "살균시작": "살균 시작", "살균2차유지분": "2차유지(분)", "살균최고온도": "최고온도", "살균F0": "F0", "살균중단": "살균 중단",
          "배양출고": "배양출고(병)", "긁기불량률%": "긁기불량률%", "병당중량": "병당중량(g)", "QC": "QC", "수확배치": "수확배치", "생성일비고": "작업일지 비고"}
    T = X[[c for c in ko if c in X]].rename(columns=ko).copy()
    for c in ("탱크 제조일", "접종일"):
        T[c] = pd.to_datetime(T[c]).dt.strftime("%m/%d")
    if "살균 시작" in T:
        T["살균 시작"] = pd.to_datetime(T["살균 시작"]).dt.strftime("%H:%M")
    st.dataframe(T.round(2), hide_index=True, **_FULL, height=360)

    st.subheader("계통(유래)별 성적")
    G = X.groupby(["품종", "유래"]).agg(배치=("코드", "size"), 세대범위=("세대", lambda s: f"{s.min()}~{s.max()}"), pH=("pH", "mean"), 액배양일=("액배양일", "mean"),
                                    긁기불량률=("긁기불량률%", "mean"), 불량률연결=("긁기불량률%", "count"), 병당중량=("병당중량", "mean"), QC=("QC", "mean"),
                                    첫사용=("일시", "min"), 마지막=("일시", "max")).reset_index().sort_values(["품종", "배치"], ascending=[True, False])
    G["첫사용"] = G.첫사용.dt.strftime("%m/%d"); G["마지막"] = G.마지막.dt.strftime("%m/%d")
    st.dataframe(G.rename(columns={"긁기불량률": "긁기불량률% 평균", "불량률연결": "연결 배치", "병당중량": "병당중량 평균", "QC": "QC 평균"}).round(2), hide_index=True, **_FULL)
    Y = X.dropna(subset=["긁기불량률%"]).copy()
    if len(Y) >= 5:
        top = list(Y["유래"].value_counts().index[:7])
        Y["계통표시"] = Y["유래"].where(Y["유래"].isin(top), "기타").fillna("미기재")
        st.markdown("**탱크 배치별 균긁기 불량률 추이** (색 = 유래 상위 7개, 점 크기 = 세대, 보라 점선 = 사건)")
        st.altair_chart(alt.layer(alt.Chart(Y).mark_circle().encode(
            x=alt.X("일시:T", title="탱크 제조일"), y=alt.Y("긁기불량률%:Q", title="균긁기 불량률(%)", scale=alt.Scale(zero=True)),
            color=alt.Color("계통표시:N", legend=alt.Legend(orient="right", title="유래", labelLimit=220)), size=alt.Size("세대:Q", legend=None, scale=alt.Scale(range=[30, 160])),
            tooltip=["코드:N", "품종:N", alt.Tooltip("탱크NO:Q", title="탱크"), "세대:Q", "유래:N", alt.Tooltip("pH:Q", format=".2f"), alt.Tooltip("긁기불량률%:Q", format=".2f"),
                     alt.Tooltip("병당중량:Q", format=".0f"), alt.Tooltip("살균F0:Q", title="F0", format=".0f")]), *event_layers(d1=Y.일시.min(), d2=Y.일시.max() + pd.Timedelta(days=14))).properties(height=260).interactive(bind_y=False), **_FULL)

    st.subheader("무엇이 불량률과 관계있나")
    cols = st.columns(3)
    for col, (xf, xt) in zip(cols, [("pH", "탱크 pH"), ("액배양일", "액배양 일수"), ("살균F0", "살균 F0")]):
        Z = Y.dropna(subset=[xf]) if xf in Y else pd.DataFrame()
        with col:
            if len(Z) >= 5:
                r = Z[[xf, "긁기불량률%"]].corr().iloc[0, 1]
                st.caption(f"{xt} vs 불량률 · r={r:.2f} (n={len(Z)})")
                st.altair_chart(alt.Chart(Z).mark_circle(size=40).encode(
                    x=alt.X(f"{xf}:Q", title=xt, scale=alt.Scale(zero=False)), y=alt.Y("긁기불량률%:Q", title="불량률(%)"),
                    color=alt.Color("품종:N", legend=None, scale=alt.Scale(domain=["팽이", "새송이"], range=[PAL["실측"], PAL["비교"]])),
                    tooltip=["코드:N", alt.Tooltip(f"{xf}:Q", format=".2f"), alt.Tooltip("긁기불량률%:Q", format=".2f")]).properties(height=200), **_FULL)
            else:
                st.caption(f"{xt}: 자료 부족")
    st.caption("불량률은 그 탱크로 접종한 병의 균긁기 불량률(배양소관리 균긁기 시트, 탱크 번호·접종일로 연결). 여름철 배양실 오염 등 다른 요인이 섞여 있으니 상관은 참고용입니다.")

    st.subheader("배양실 작업 일지 비고 (설비·특이사항)")
    D = etl_logs.load("tank_daily")
    if not D.empty:
        N = D.dropna(subset=["비고"]).sort_values("date", ascending=False)
        N = N[N.date.dt.year == yr]
        kw = st.text_input("검색어 (예: 패킹, 솥, 노즐, 결정, 정전)", "", key="sp_kw")
        if kw:
            N = N[N.비고.str.contains(kw, case=False, na=False)]
        st.dataframe(N[["date", "접종탱크NO", "접종탱크코드", "종균pH", "접종배지", "살균후pH", "입병", "살균전pH", "신규탱크", "비고"]]
                     .assign(date=N.date.dt.strftime("%m/%d (%a)")).rename(columns={"date": "날짜", "접종탱크NO": "접종 탱크", "접종탱크코드": "종균 코드", "종균pH": "종균 pH",
                                                                                "접종배지": "접종 배지", "살균후pH": "배지 살균후 pH", "살균전pH": "입병 살균전 pH", "신규탱크": "새 탱크"}),
                     hide_index=True, **_FULL, height=300)
        st.caption(f"{len(N)}건 · 솥 패킹 교체·재살균 같은 메모는 살균 페이지의 '중단' 표시와 같은 날인지 맞춰 보세요.")
    with st.expander("액탱크 살균 온도 기준표 (배양센터 기록표)"):
        st.dataframe(pd.DataFrame(TANK_REF, columns=["경과", "구간", "살균솥 온도(℃)", "탱크 내부(℃)"]), hide_index=True, **_FULL)
        st.caption("탱크 안의 액체는 솥보다 훨씬 늦게 데워져 2차 유지 시작 후 약 60분이 지나야 118℃에 닿습니다. 그래서 액탱크 살균은 119℃에서 3시간 안팎 유지합니다.")
    st.caption("원본: 배양센터 '종균 관리 대장'(계대 이력 중 T- 행 = 액탱크 배치, 코드 예 PF24/T-260101-1,NO.10 = 팽이 24세대·2026-01-01 1번째·탱크 10), '배양실 작업 일지'(일별 접종 탱크·pH·비고). "
               "모균 F = 플라스크, P = 플레이트. 새송이는 균긁기 시트에 탱크 번호가 없어 접종일로만 연결합니다.")


# ───────────────────────── 페이지: 살균 ─────────────────────────
UNIT_COLOR = {"1호기": "#e0312a", "2호기": "#2a78d6", "3호기": "#1f9e3a", "4호기": "#e0b400"}   # 살균 프로그램 화면과 같은 색


def _ucolor(units):
    return alt.Scale(domain=list(units), range=[UNIT_COLOR.get(u, "#888") for u in units])


def _day_window(W, cyd, d0):
    """하루 전체 그래프의 표시 구간. 시작 = 그날 첫 사이클 상승 시작 30분 전,
    끝 = 마지막 사이클이 100℃ 아래로 내려온 뒤 온도가 한 번 떨어졌다가 다시 오르기 시작한 시점 + 1시간. 사이클이 없으면 하루 전체."""
    day0 = pd.Timestamp(d0); day1 = day0 + pd.Timedelta(days=1) - pd.Timedelta(minutes=1)
    if cyd is None or cyd.empty or W.empty:
        return day0, day1
    x0 = max(day0, cyd.start.min() - pd.Timedelta(minutes=30))
    last = cyd.sort_values("end").iloc[-1]
    x1 = last.end + pd.Timedelta(hours=1)
    after = W[(W.unit == last.unit) & (W.ts >= last.end)].dropna(subset=["온도"]).sort_values("ts")
    if len(after) > 5:
        T = after["온도"].values; ts = after.ts.values
        # 냉각완료 뒤 3시간 안에서: 떨어지다가(최저점) 다시 3℃ 이상 오르는 첫 지점 = 문 열림 뒤 재상승
        lim = np.searchsorted(ts, last.end.to_datetime64() + np.timedelta64(3, "h"))
        for i in range(1, min(lim, len(T)) - 1):
            if T[i] <= T[i - 1] and T[i] < T[0] - 3 and (T[i + 1:min(i + 60, len(T))] >= T[i] + 3).any():
                x1 = pd.Timestamp(ts[i]) + pd.Timedelta(hours=1)
                break
    return x0, min(max(x1, x0 + pd.Timedelta(hours=1)), day1)


def page_retort(ver):
    st.title("살균")
    D = load_retort(ver); CY = load_retort_cycles(ver)
    if D.empty:
        st.info(f"살균솥 기록이 아직 없습니다. 살균솥 PC의 '데이터수집' 폴더가 mirror_nas.bat 로 `{C.log_dir('retort')}` 에 복사된 뒤 "
                "데이터 관리 → '살균솥 기록 읽기' 를 누르세요.")
        return
    units = sorted(D.unit.unique(), key=lambda u: int(re.sub(r"\D", "", u) or 0))
    temp = D[D.tag == "온도"]
    last_day = temp.date.max()

    # ── 솥별 현황 카드 ──
    cols = st.columns(max(len(units), 1))
    for col, u in zip(cols, units):
        with col:
            st.markdown(f"<div style='font-weight:700;font-size:1.05rem;color:{UNIT_COLOR.get(u, '#333')}'>살균 {u}</div>", unsafe_allow_html=True)
            cu = CY[CY.unit == u].sort_values("start") if not CY.empty else pd.DataFrame()
            tl = temp[temp.unit == u].sort_values("last")
            if cu.empty:
                st.caption("사이클 없음"); continue
            r = cu.iloc[-1]
            st.metric(f"최근 살균 ({r.get('구분', '')})", f"{r.start:%m/%d %H:%M}", f"~{r.end:%H:%M} · {int(r.총가동분)}분", delta_color="off")
            m1, m2 = st.columns(2)
            m1.metric("최고온도", f"{r.최고온도:.1f}℃"); m2.metric("2차 유지", f"{int(r['2차유지분'])}분")
            m1.metric("1차 유지", f"{int(r['1차유지분'])}분"); m2.metric("F0", f"{r.F0:.0f}")
            if not tl.empty:
                st.caption(f"마지막 기록 {tl.iloc[-1]['last']:%m/%d %H:%M} · 이달 {int((cu.start >= last_day.replace(day=1)).sum())}회 · 누적 {len(cu)}회")

    # ── 하루 전체 그래프 (원 프로그램의 '전체그래프' 화면) ──
    st.subheader("하루 전체 그래프")
    c1, c2, c3 = st.columns([1.2, 1, 2])
    days = sorted(temp.date.dt.date.unique())
    d0 = c1.selectbox("날짜", days[::-1], index=0, format_func=lambda d: d.strftime("%Y-%m-%d (%a)"), key="rt_day")
    show_p = c2.checkbox("압력도 표시", value=False)
    sel_u = c3.multiselect("솥", units, default=units, key="rt_units")
    st.session_state["_ctx_note"] = f"{d0:%Y-%m-%d} 살균 그래프 (솥 {', '.join(sel_u) if sel_u else '없음'})"
    W = load_retort_1min(ver, tuple(sel_u), str(d0), str(d0))
    if W.empty:
        st.warning("그날 기록이 없습니다.")
    else:
        cyd = CY[(CY.unit.isin(sel_u)) & (CY.start.dt.date <= d0) & (CY.end.dt.date >= d0)] if not CY.empty else pd.DataFrame()
        x0, x1 = _day_window(W, cyd, d0)
        W = W[(W.ts >= x0) & (W.ts <= x1)]
        base = alt.Chart(W.dropna(subset=["온도"])[["unit", "ts", "온도", "압력"]]).mark_line(strokeWidth=1.8).encode(
            x=alt.X("ts:T", title=None, axis=alt.Axis(format="%H:%M"), scale=alt.Scale(domain=[x0.isoformat(), x1.isoformat()])),
            y=alt.Y("온도:Q", title="온도(℃)", scale=alt.Scale(domain=[0, 130])),
            color=alt.Color("unit:N", title=None, scale=_ucolor(units), legend=alt.Legend(orient="top")),
            tooltip=["unit:N", alt.Tooltip("ts:T", format="%H:%M"), alt.Tooltip("온도:Q", format=".1f"), alt.Tooltip("압력:Q", format=".1f")])
        layers = [base]
        if not cyd.empty:   # 사이클 시작·냉각완료 시각을 점선으로
            marks = pd.concat([cyd[["unit", "start"]].rename(columns={"start": "t"}).assign(구분="시작"), cyd[["unit", "end"]].rename(columns={"end": "t"}).assign(구분="냉각완료")])
            layers.append(alt.Chart(marks).mark_rule(strokeDash=[3, 3], opacity=0.6).encode(x="t:T", color=alt.Color("unit:N", scale=_ucolor(units), legend=None),
                                                                                       tooltip=["unit:N", "구분:N", alt.Tooltip("t:T", format="%H:%M")]))
        st.altair_chart(alt.layer(*layers).properties(height=300).interactive(bind_y=False), **_FULL)
        if show_p and W["압력"].notna().any():
            st.altair_chart(alt.Chart(W.dropna(subset=["압력"])[["unit", "ts", "압력"]]).mark_line(strokeWidth=1.4).encode(
                x=alt.X("ts:T", title=None, axis=alt.Axis(format="%H:%M")), y=alt.Y("압력:Q", title="압력(kPa)"),
                color=alt.Color("unit:N", scale=_ucolor(units), legend=None),
                tooltip=["unit:N", alt.Tooltip("ts:T", format="%H:%M"), alt.Tooltip("압력:Q", format=".1f")]).properties(height=160).interactive(bind_y=False), **_FULL)
        if not cyd.empty:
            T = cyd.sort_values("start")[["unit", "구분", "start", "end", "승온분", "1차유지분", "1차유지온도", "2차유지분", "2차유지온도", "최고온도", "냉각분", "총가동분", "F0", "최고압력", "중단횟수"]].copy()
            T["start"] = T.start.dt.strftime("%H:%M"); T["end"] = T.end.dt.strftime("%H:%M")
            st.dataframe(T.rename(columns={"unit": "솥", "start": "시작", "end": "냉각완료"}), hide_index=True, **_FULL)

    # ── 사이클 목록·추이 ──
    st.subheader("살균 사이클 기록")
    if CY.empty:
        st.info("사이클을 찾지 못했습니다.")
    else:
        c1, c2 = st.columns([1, 2])
        span = c1.selectbox("기간", ["최근 30일", "최근 90일", "최근 180일", "전체"], index=1, key="rt_span")
        kinds = c2.multiselect("구분", ["배지", "액탱크", "이상"], default=["배지", "액탱크", "이상"], key="rt_kind",
                               help="배지: 105℃ 1차 유지 + 118℃ 2차 유지 / 액탱크: 1차 유지 없이 119℃ 2시간 이상 / 이상: 중단·미완료")
        nd = {"최근 30일": 30, "최근 90일": 90, "최근 180일": 180}.get(span)
        X = CY[CY.unit.isin(sel_u) & CY["구분"].isin(kinds)]
        if nd and not X.empty:
            X = X[X.start >= CY.start.max() - pd.Timedelta(days=nd)]
        X = X.copy()
        flag = []
        for _, r in X.iterrows():
            f_ = []
            if r["구분"] == "이상":
                f_.append("미완료" if not r.완료 else "유지 이상")
            elif r["구분"] == "배지":
                if r["1차유지분"] < 50: f_.append("1차유지 짧음")
                if r["2차유지분"] < 55: f_.append("2차유지 짧음")
                if r.최고온도 < 116: f_.append("최고온도 낮음")
                if r.최고온도 > 121: f_.append("최고온도 높음")
            else:   # 액탱크
                if r["2차유지분"] < 140: f_.append("유지 짧음")
                if r.최고온도 < 117: f_.append("최고온도 낮음")
            if r.get("중단횟수", 0) > 0: f_.append(f"중단 {int(r.중단횟수)}회")
            flag.append(", ".join(f_))
        X["확인"] = flag
        try:                                   # 2차 유지 구간 압력(중앙값) 열 (v1.24.7)
            H2 = etl_retort.hold2_stats()
            if len(H2):
                X = X.merge(H2[["unit", "start", "2차압력"]], on=["unit", "start"], how="left")
                X["2차압력"] = X["2차압력"].round(1)
        except Exception:
            pass
        # 2차 유지온도가 평소보다 낮은 배지 사이클의 압력 진단 — '임의 설정 변경 의심'은 따로 표시 (v1.24.7)
        try:
            HA = etl_retort.hold2_all()
        except Exception:
            HA = pd.DataFrame()
        if len(HA):
            X = X.merge(HA[["unit", "start", "진단", "설명", "설정의심"]], on=["unit", "start"], how="left")
            X["확인"] = [", ".join([x for x in (("2차 온도 낮음(압력도 낮음 → 설정 의심)" if sus == True else (f"2차 온도 낮음({dg})" if isinstance(dg, str) and dg else "")), fl) if x])
                        for fl, dg, sus in zip(X["확인"], X["진단"], X["설정의심"])]
            SX = X[X["설정의심"] == True].sort_values("start", ascending=False)
            if len(SX):
                st.error(BR._md(f"**⚠ 임의 설정 변경 의심 {len(SX)}회** (고른 기간·솥) — 2차 유지 온도·압력이 같은 솥 평소보다 함께 낮고 스팀밸브는 평소대로 → "
                                "조작판에서 2차 설정을 기록 없이 낮춘 것으로 보입니다. 2차 유지 설정값은 살균솥 기록 파일에 남지 않습니다.  \n"
                                + "  \n".join(f"· {r.unit} {r.start:%m/%d %H:%M} — {r.설명}" for r in SX.head(10).itertuples())))
        if X.empty:
            st.info("해당 조건의 사이클이 없습니다."); return
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("사이클 수", f"{len(X)}회", f"확인 필요 {(X.확인 != '').sum()}회", delta_color="inverse" if (X.확인 != "").any() else "off")
        k2.metric("2차 유지 평균", f(X["2차유지분"].mean(), 0, "분"), f"최소 {X['2차유지분'].min():.0f}분", delta_color="off")
        k3.metric("최고온도 평균", f(X.최고온도.mean(), 1, "℃"), f"{X.최고온도.min():.1f}~{X.최고온도.max():.1f}", delta_color="off")
        k4.metric("총가동 평균", f(X.총가동분.mean(), 0, "분"), f"{X.총가동분.min():.0f}~{X.총가동분.max():.0f}", delta_color="off")
        k5.metric("F0 평균", f(X.F0.mean(), 0), f"최소 {X.F0.min():.0f}", delta_color="off")
        metric = st.radio("추이 항목", ["2차유지분", "최고온도", "총가동분", "F0", "1차유지분", "냉각분", "승온분"], horizontal=True, key="rt_metric")
        ch = alt.Chart(X).mark_point(size=60, filled=True).encode(
            x=alt.X("start:T", title=None), y=alt.Y(f"{metric}:Q", title=metric, scale=alt.Scale(zero=False, padding=14)),
            color=alt.Color("unit:N", title=None, scale=_ucolor(units), legend=alt.Legend(orient="top")),
            shape=alt.Shape("구분:N", scale=alt.Scale(domain=["배지", "액탱크", "이상"], range=["circle", "square", "cross"]), legend=alt.Legend(orient="top", title=None)),
            tooltip=["unit:N", "구분:N", alt.Tooltip("start:T", title="시작", format="%m/%d %H:%M"), alt.Tooltip(f"{metric}:Q", format=".1f"), "확인:N"])
        st.altair_chart(ch.properties(height=220).interactive(bind_y=False), **_FULL)
        T = X.sort_values("start", ascending=False)[["unit", "구분", "start", "end", "승온분", "1차유지분", "1차유지온도", "2차승온분", "2차유지분", "2차유지온도"] + (["2차압력"] if "2차압력" in X else []) + ["최고온도", "냉각분", "총가동분", "F0", "최고압력", "중단횟수", "확인"]].copy()
        T["start"] = T.start.dt.strftime("%m/%d %H:%M"); T["end"] = T.end.dt.strftime("%m/%d %H:%M")
        st.dataframe(T.rename(columns={"unit": "솥", "start": "시작", "end": "냉각완료"}), hide_index=True, **_FULL, height=320)
        with st.expander("월별 요약"):
            M = X.assign(월=X.start.dt.strftime("%Y-%m")).groupby(["월", "unit", "구분"]).agg(횟수=("start", "size"), 이차유지평균=("2차유지분", "mean"), 최고온도평균=("최고온도", "mean"),
                                                                                 총가동평균=("총가동분", "mean"), F0평균=("F0", "mean"), 확인필요=("확인", lambda s: (s != "").sum())).reset_index()
            st.dataframe(M.rename(columns={"unit": "솥", "이차유지평균": "2차유지 평균(분)", "최고온도평균": "최고온도 평균", "총가동평균": "총가동 평균(분)", "F0평균": "F0 평균"}).round(1).iloc[::-1], hide_index=True, **_FULL)

        if st.toggle("사이클 겹쳐 보기", value=False, key="rt_open_overlay",
                     help="고른 사이클의 1분 기록을 겹쳐 그립니다. 켤 때만 불러옵니다."):
            # ── 사이클 겹쳐 보기 ──
            st.subheader("사이클 겹쳐 보기 (시작 시각 기준)")
            opts = X.sort_values("start", ascending=False)
            labels = {i: f"{r.unit} {r.start:%m/%d %H:%M} {r['구분']} ({int(r.총가동분)}분)" for i, r in opts.iterrows()}
            default = [i for i in opts.groupby("unit").head(1).index]
            pick = st.multiselect("사이클", list(labels), default=default, format_func=lambda i: labels[i], key="rt_pick")
            if pick:
                parts = []
                for i in pick:
                    r = X.loc[i]
                    w = load_retort_1min(ver, (r.unit,), str(r.start.date()), str(r.end.date()))
                    w = w[(w.ts >= r.start) & (w.ts <= r.end)].dropna(subset=["온도"]).copy()
                    w["분"] = (w.ts - r.start).dt.total_seconds() / 60; w["사이클"] = labels[i]
                    parts.append(w[["분", "온도", "사이클"]])
                Z = pd.concat(parts)
                st.altair_chart(alt.Chart(Z).mark_line(strokeWidth=1.6).encode(
                    x=alt.X("분:Q", title="시작 후 경과(분)"), y=alt.Y("온도:Q", title="온도(℃)", scale=alt.Scale(domain=[0, 130])),
                    color=alt.Color("사이클:N", legend=alt.Legend(orient="top", columns=2)), detail="사이클:N",
                    tooltip=["사이클:N", alt.Tooltip("분:Q", format=".0f"), alt.Tooltip("온도:Q", format=".1f")]).properties(height=280).interactive(bind_y=False), **_FULL)


    if st.toggle("살균 조건과 배양 결과 비교 보기", value=False, key="rt_open_vs",
                 help="살균일별 균긁기 불량률을 맞춰 계산합니다. 켤 때만 불러옵니다."):
        # ── 살균 ↔ 배양 결과 ──
        st.subheader("살균 조건과 배양 결과 (균긁기 불량률)")
        RS = load_retort_vs_scrape(ver)
        if RS.empty or "긁기불량률%" not in RS:
            st.info("배양소관리 균긁기 시트가 연결되면 살균일별 불량률이 나옵니다.")
        else:
            Z = RS.dropna(subset=["긁기불량률%"]).copy()
            if Z.empty:
                st.info("아직 균긁기까지 끝난 살균 배치가 없습니다 (살균 후 약 4주 뒤 확인 가능).")
            else:
                Z["중단여부"] = np.where(Z["중단"] > 0, "중단 있음", "정상")
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("연결된 살균일", f"{len(Z)}일", f"팽이 {(Z.품목=='팽이').sum()} · 새송이 {(Z.품목=='새송이').sum()}", delta_color="off")
                lo, hi = Z[Z.F0최소 < Z.F0최소.median()], Z[Z.F0최소 >= Z.F0최소.median()]
                k2.metric("F0 낮은 절반 불량률", f(lo["긁기불량률%"].mean(), 2, "%"), f"F0 < {Z.F0최소.median():.0f}", delta_color="off")
                k3.metric("F0 높은 절반 불량률", f(hi["긁기불량률%"].mean(), 2, "%"), f"F0 ≥ {Z.F0최소.median():.0f}", delta_color="off")
                it = Z[Z["중단"] > 0]
                k4.metric("중단 있던 날 불량률", f(it["긁기불량률%"].mean(), 2, "%") if len(it) else "—", f"{len(it)}일 · 정상 {f(Z[Z['중단']==0]['긁기불량률%'].mean(), 2, '%')}", delta_color="off")
                c1, c2 = st.columns(2)
                c1.altair_chart(alt.Chart(Z).mark_circle(size=50).encode(
                    x=alt.X("F0최소:Q", title="그날 살균 F0 (가장 낮은 솥)", scale=alt.Scale(zero=False)), y=alt.Y("긁기불량률%:Q", title="균긁기 불량률(%)"),
                    color=alt.Color("품목:N", scale=alt.Scale(domain=["팽이", "새송이"], range=[PAL["실측"], PAL["비교"]]), legend=alt.Legend(orient="top", title=None)),
                    shape=alt.Shape("중단여부:N", scale=alt.Scale(domain=["정상", "중단 있음"], range=["circle", "cross"]), legend=alt.Legend(orient="top", title=None)),
                    tooltip=[alt.Tooltip("살균일:T", format="%m/%d"), "품목:N", "솥:N", alt.Tooltip("F0최소:Q", format=".0f"), alt.Tooltip("이차유지최소:Q", title="2차유지 최소", format=".0f"), "중단:Q", alt.Tooltip("긁기불량률%:Q", format=".2f")]
                ).properties(height=260).interactive(), **_FULL)
                c2.altair_chart(alt.layer(alt.Chart(Z).mark_circle(size=50).encode(
                    x=alt.X("살균일:T", title=None), y=alt.Y("긁기불량률%:Q", title="균긁기 불량률(%)"),
                    color=alt.Color("F0최소:Q", title="F0", scale=alt.Scale(scheme="viridis")),
                    tooltip=[alt.Tooltip("살균일:T", format="%m/%d"), "품목:N", alt.Tooltip("F0최소:Q", format=".0f"), alt.Tooltip("긁기불량률%:Q", format=".2f")]
                ), *event_layers(d1=Z.살균일.min(), d2=Z.살균일.max() + pd.Timedelta(days=14))).properties(height=260).interactive(bind_y=False), **_FULL)
                r = Z[["F0최소", "긁기불량률%"]].corr().iloc[0, 1] if len(Z) >= 5 else np.nan
                st.caption(f"F0(최소)와 불량률 상관 r = {r:.2f} (n={len(Z)}). 살균 부족이 원인이면 F0 낮은 날의 불량률이 뚜렷이 높아야 합니다. "
                           "여름철 배양실 오염처럼 살균과 무관한 요인이 크면 상관이 약하게 나옵니다.")
                T = Z.sort_values("살균일", ascending=False)[["살균일", "접종일", "품목", "솥", "F0최소", "이차유지최소", "최고온도최소", "중단", "배양출고", "긁기불량률%"]].copy()
                T["살균일"] = T.살균일.dt.strftime("%m/%d"); T["접종일"] = T.접종일.dt.strftime("%m/%d")
                st.dataframe(T.rename(columns={"이차유지최소": "2차유지 최소(분)", "최고온도최소": "최고온도 최소", "배양출고": "배양출고(병)"}).round(1), hide_index=True, **_FULL, height=300)


    # 무거운 섹션은 열었을 때만 계산한다 (펼침 상자는 접혀 있어도 내용을 다 만들어 보내므로 소용이 없다)
    if st.toggle("입병일별 기록 보기 (솥 4개 = 하루)", value=False, key="rt_open_loads",
                 help="정규철 종합관리 시트와 살균 사이클을 짝지어 계산합니다. 켤 때만 불러옵니다."):
        # ── 입병일(하루) 단위: 배지 상태·살균 조건·균긁기·권지 불량 (v1.24.8) ──
        st.subheader("입병일별 — 배지 상태·살균 조건·균긁기·권지 불량 (재배 관리자 종합관리)")
        st.caption("균긁기·권지 불량은 솥별이 아니라 '그날 병을 어느 발이실에 몇 병 넣었나' 단위로 적혀 있어(하루 4솥치가 한 행에 적히기도 함) "
                   "솥 하나로는 불량률을 낼 수 없습니다. 그래서 입병일(하루, 보통 4솥)로 묶어 계산합니다. "
                   "긁기 불량률 = 긁기불량 ÷ (긁기불량 + 입상수량), 권지 불량률 = 권지불량 ÷ (권지불량 + 권지수량), "
                   "합계 = 접종한 병 중 균긁기나 권지에서 빠진 비율 = 1 − (1−긁기)×(1−권지).")
        LD = load_loads(ver)
        if LD.empty:
            st.info("정규철 작업 일지(종합관리 시트)가 연결되면 입병일별 기록이 나옵니다.")
        else:
            DD = A.loads_daily(LD)
            c1, c2 = st.columns([1, 3])
            crop_l = c1.radio("작물", ["팽이", "새송이", "전체"], horizontal=True, key="ld_crop")
            Y0 = DD if crop_l == "전체" else DD[DD.crop == crop_l]
            n_prog = int(Y0["진행중"].sum())
            Y = Y0[~Y0["진행중"]].dropna(subset=["긁기불량률%"]).copy()
            has_kj = Y["권지불량률%"].notna().any()
            wsum = lambda a, b: 100 * Y[a].sum() / Y[b].sum() if Y[b].sum() > 0 else np.nan
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("입병일 (불량률 확인분)", f"{len(Y)}일", f"균긁기 진행 중 {n_prog}일 제외" if n_prog else None, delta_color="off")
            k2.metric("균긁기 불량률", f(100 * Y["긁기불량"].sum() / (Y["긁기불량"].sum() + Y["입상수량"].sum()) if len(Y) else np.nan, 2, "%"),
                      f"하루 중앙값 {Y['긁기불량률%'].median():.2f}% · 최대 {Y['긁기불량률%'].max():.1f}%" if len(Y) else None, delta_color="off")
            k3.metric("권지 불량률", f(wsum("권지불량", "권지대상"), 2, "%") if has_kj else "—",
                      f"하루 중앙값 {Y['권지불량률%'].median():.2f}% · 최대 {Y['권지불량률%'].max():.1f}%" if has_kj else "권지 기록 없음", delta_color="off")
            k4.metric("합계 불량률 (하루 평균)", f(Y["합계불량률%"].mean(), 2, "%") if has_kj else "—",
                      f"최대 {Y['합계불량률%'].max():.1f}%" if has_kj else None, delta_color="off")
            # 긁기불량이 많은 날 → 선별에서 다 못 거른 병이 권지 불량으로도 나온다 (사용자 설명)
            Z2 = Y.dropna(subset=["긁기불량률%", "권지불량률%"])
            if len(Z2) >= 20:
                a_, b_ = Z2["긁기불량률%"].quantile(0.5), Z2["긁기불량률%"].quantile(0.9)
                bands = [("긁기 불량 적은 날 (하위 50%)", Z2["긁기불량률%"] < a_), ("보통 (50~90%)", (Z2["긁기불량률%"] >= a_) & (Z2["긁기불량률%"] < b_)),
                         ("긁기 불량 많은 날 (상위 10%)", Z2["긁기불량률%"] >= b_)]
                BT = pd.DataFrame([{"구간": nm, "긁기 불량률": f"{Z2.loc[m, '긁기불량률%'].min():.2f}~{Z2.loc[m, '긁기불량률%'].max():.2f}%", "입병일": int(m.sum()),
                                    "권지 불량률 평균": round(Z2.loc[m, "권지불량률%"].mean(), 2), "권지 불량률 중앙값": round(Z2.loc[m, "권지불량률%"].median(), 2)} for nm, m in bands])
                hi_, lo_ = BT.iloc[2]["권지 불량률 중앙값"], BT.iloc[0]["권지 불량률 중앙값"]
                st.markdown(BR._md(f"**긁기 불량과 권지 불량** — 긁기 불량이 많은 날(상위 10%, {b_:.2f}% 이상)은 권지 불량률 중앙값이 {hi_:.2f}%로, "
                                   f"적은 날({lo_:.2f}%)의 {hi_ / lo_:.1f}배입니다." if lo_ > 0 else "**긁기 불량과 권지 불량**"))
                st.dataframe(BT, hide_index=True, **_FULL)
            if len(Y) >= 5:
                specs = [("긁기불량률%", "권지불량률%", "균긁기 불량률(%)", "권지 불량률(%)"), ("F0최소", "합계불량률%", "그날 살균 F0 (가장 낮은 솥)", "합계 불량률(%)"),
                         ("수분", "합계불량률%", "배지 수분(%)", "합계 불량률(%)"), ("배지품온", "합계불량률%", "입병 시 배지 품온(℃)", "합계 불량률(%)")]
                cols4 = st.columns(4)
                for col, (xf, yf, xt, yt) in zip(cols4, specs):
                    Z = Y.dropna(subset=[xf, yf]) if xf in Y and yf in Y else pd.DataFrame()
                    with col:
                        if len(Z) >= 5:
                            r = Z[xf].rank().corr(Z[yf].rank())
                            st.caption(f"{xt} vs {yt} · 순위상관 {r:.2f} (n={len(Z)}일)")
                            st.altair_chart(alt.Chart(Z.assign(입병=Z["입병일"].dt.strftime("%Y-%m-%d"))).mark_circle(size=36, opacity=0.6).encode(
                                x=alt.X(f"{xf}:Q", title=xt, scale=alt.Scale(zero=False)), y=alt.Y(f"{yf}:Q", title=yt),
                                color=alt.Color("crop:N", title=None, legend=alt.Legend(orient="top")),
                                tooltip=["입병:N", "crop:N", "살균기:N", alt.Tooltip(f"{xf}:Q", format=".2f"), alt.Tooltip("긁기불량률%:Q", format=".2f"),
                                         alt.Tooltip("권지불량률%:Q", format=".2f"), alt.Tooltip("합계불량률%:Q", format=".2f"), "발이실:N", "비고:N"]).properties(height=210), **_FULL)
                        else:
                            st.caption(f"{xt}: 자료 부족")
                st.caption("점 하나 = 입병일 하루. 불량률이 몇십 %인 몇 날이 그림을 끌어당기지 않게 순위상관(값 대신 순위로 계산)을 씁니다. "
                           "살균 F0는 살균솥 기록이 있는 2026-03 이후만 있습니다.")
                M = Y.assign(월=Y["입병일"].dt.strftime("%Y-%m")).groupby("월").agg(
                    입병일=("입병일", "size"), 긁기불량=("긁기불량", "sum"), 입상수량=("입상수량", "sum"), 권지불량=("권지불량", "sum"), 권지대상=("권지대상", "sum"),
                    합계평균=("합계불량률%", "mean")).reset_index()
                M["긁기 불량률%"] = (100 * M["긁기불량"] / (M["긁기불량"] + M["입상수량"])).round(2)
                M["권지 불량률%"] = (100 * M["권지불량"] / M["권지대상"].where(M["권지대상"] > 0)).round(2)
                with st.expander("월별 요약"):
                    st.dataframe(M[["월", "입병일", "긁기불량", "긁기 불량률%", "권지불량", "권지 불량률%", "합계평균"]].rename(columns={"합계평균": "합계 불량률% (하루 평균)"})
                                 .round(2).iloc[::-1], hide_index=True, **_FULL)
            T = Y0.sort_values("입병일", ascending=False)[[c for c in ["입병일", "crop", "솥수", "살균기", "F0최소", "이차유지최소", "중단", "pH", "수분", "배지품온", "충진시간", "함량",
                                                                      "접종일", "종균", "접종온도", "균긁기일", "발이실", "접종수량", "긁기불량", "긁기불량률%", "권지불량", "권지불량률%",
                                                                      "합계불량률%", "상태", "비고"] if c in Y0]].copy()
            T["입병일"] = T["입병일"].dt.strftime("%Y-%m-%d")
            T["접종일"] = pd.to_datetime(T["접종일"]).dt.strftime("%m/%d")
            st.dataframe(T.rename(columns={"crop": "작물", "살균기": "살균기(순번순)", "F0최소": "F0 최소", "이차유지최소": "2차유지 최소(분)", "중단": "살균 중단",
                                           "비고": "비고(아메바·미숙 등)"}).round(2), hide_index=True, **_FULL, height=340,
                         column_config={"긁기불량률%": st.column_config.NumberColumn("긁기 불량률%", format="%.2f"),
                                        "권지불량률%": st.column_config.NumberColumn("권지 불량률%", format="%.2f"),
                                        "합계불량률%": st.column_config.NumberColumn("합계 불량률%", format="%.2f")})
            st.caption("한 줄 = 입병일 하루(보통 살균솥 4번). 살균기(순번순)는 그날 1~4번 솥의 살균기, F0·2차유지는 그날 가장 낮은 솥, 살균 중단은 합계. "
                       "상태: '균긁기 전'·'균긁기 진행 중'(기록된 병수가 접종수량의 95% 미만)은 위 집계에서 빼고, '권지 전'은 긁기 불량률만 셉니다. "
                       "비고의 '아메-125'는 아메바 오염 병수, '미숙'은 미숙 병수.")
            with st.expander("솥별 원본 행 (살균 조건)"):
                R = LD if crop_l == "전체" else LD[LD.crop == crop_l]
                R = R.sort_values(["입병일", "입병순번"], ascending=[False, True])[[c for c in ["입병일", "crop", "입병순번", "살균기NO", "살균시작", "F0", "2차유지분", "중단횟수",
                                                                                       "pH", "수분", "배지품온", "살균수량", "접종수량", "발이실", "균긁기일", "긁기불량", "입상수량",
                                                                                       "권지일", "권지불량", "권지수량"] if c in LD]].copy()
                for c in ("입병일", "균긁기일", "권지일"):
                    if c in R:
                        R[c] = pd.to_datetime(R[c]).dt.strftime("%Y-%m-%d" if c == "입병일" else "%m/%d")
                if "살균시작" in R:
                    R["살균시작"] = pd.to_datetime(R["살균시작"]).dt.strftime("%H:%M")
                st.dataframe(R.rename(columns={"crop": "작물", "입병순번": "순번", "살균기NO": "살균기"}).round(2), hide_index=True, **_FULL, height=320)
                st.caption("균긁기·권지 칸(발이실·긁기불량·입상수량·권지불량·권지수량)은 그 솥 것이 아니라 그날 병 중 그 발이실로 간 몫입니다. 빈 행은 같은 날 다른 행에 합쳐 적힌 솥입니다.")

    # ── 10초 원본 ──
    with st.expander("10초 원본 곡선 (온도·압력·밸브)"):
        c1, c2, c3 = st.columns([1, 1, 1])
        u = c1.selectbox("솥", units, key="rt_raw_u")
        du = sorted(temp[temp.unit == u].date.dt.date.unique())
        dr = c2.selectbox("날짜", du[::-1], index=0, format_func=lambda d: d.strftime("%Y-%m-%d (%a)"), key="rt_raw_d")
        go = c3.checkbox("원본 불러오기", value=False, key="rt_raw_go", help="체크할 때만 파일을 읽습니다 (하루 약 4만 개 표본)")
        if go:
            R_ = load_retort_day(ver, u, str(dr))
            if R_.empty or "온도" not in R_:
                st.warning("파일을 읽을 수 없습니다.")
            else:
                ok = R_.dropna(subset=["온도"])[["ts", "온도"] + (["압력"] if "압력" in R_ else [])]
                step = max(1, len(ok) // 3000)          # 그림에는 3천 점 정도만 (30초 간격)
                st.altair_chart(alt.Chart(ok.iloc[::step]).mark_line(strokeWidth=1.3, color=UNIT_COLOR.get(u, "#333")).encode(
                    x=alt.X("ts:T", title=None, axis=alt.Axis(format="%H:%M")), y=alt.Y("온도:Q", title="온도(℃)", scale=alt.Scale(zero=False)),
                    tooltip=[alt.Tooltip("ts:T", format="%H:%M:%S"), alt.Tooltip("온도:Q", format=".1f")] + ([alt.Tooltip("압력:Q", format=".1f")] if "압력" in ok else [])
                ).properties(height=240).interactive(bind_y=False), **_FULL)
                vs = [t for t in ("스팀", "상부", "하부") if t in R_ and R_[t].notna().any()]
                segs = []
                for t in vs:   # 열림(1) 구간을 시작~끝으로 묶는다
                    v = R_[["ts", t]].dropna()
                    on = (v[t] > 0.5).values; ts = v.ts.values
                    k = 0
                    while k < len(on):
                        if on[k]:
                            j = k
                            while j + 1 < len(on) and on[j + 1]:
                                j += 1
                            segs.append({"밸브": t, "s": ts[k], "e": ts[j] + np.timedelta64(10, "s")}); k = j + 1
                        else:
                            k += 1
                if segs:
                    S_ = pd.DataFrame(segs)
                    st.altair_chart(alt.Chart(S_).mark_rect().encode(
                        x=alt.X("s:T", title=None, axis=alt.Axis(format="%H:%M"), scale=alt.Scale(domain=[ok.ts.min().isoformat(), ok.ts.max().isoformat()])), x2="e:T",
                        y=alt.Y("밸브:N", title=None, scale=alt.Scale(domain=vs)),
                        color=alt.Color("밸브:N", legend=None, scale=alt.Scale(domain=["스팀", "상부", "하부"], range=["#e0312a", "#2a78d6", "#1f9e3a"])),
                        tooltip=["밸브:N", alt.Tooltip("s:T", title="열림", format="%H:%M:%S"), alt.Tooltip("e:T", title="닫힘", format="%H:%M:%S")]).properties(height=90), **_FULL)
                    st.caption(f"밸브 열림 구간 {len(S_)}개 (스팀 {sum(S_.밸브=='스팀')}, 상부 {sum(S_.밸브=='상부')}, 하부 {sum(S_.밸브=='하부')})")
    st.caption("원본: 살균솥 PC '데이터수집' 폴더의 .cld 파일(10초 간격). 사이클 = 온도 100℃ 이상 구간 기준으로 시작(스팀밸브 열림)·1차 유지(102~109℃)·2차 유지(최고온도−1.5℃ 이상)·냉각완료(100℃ 아래)를 나눕니다. "
               "2차 유지를 못 끝낸 채 100℃ 아래로 떨어졌다가 90분 안에 다시 오르면 같은 사이클의 '중단'(패킹 불량 등)으로 셉니다. 구분 — 배지: 1차+2차 유지 / 액탱크: 1차 유지 없이 119℃ 2시간 이상. "
               "F0 = 121.1℃ 기준 누적 살균값(z=10℃, 분).")


# ───────────────────────── 페이지: 자료실 ─────────────────────────
def _doc_dir(paths):
    for d in paths:
        if os.path.isdir(d):
            return d
    return paths[-1]


def _doc_files(d, depth):
    out = []
    if not os.path.isdir(d):
        return out
    pats = [os.path.join(d, *(["*"] * k), "*") for k in range(depth + 1)]
    for pat in pats:
        for f_ in glob.glob(pat):
            name = os.path.basename(f_)
            if os.path.isfile(f_) and name.lower().endswith(C.DOC_EXT) and not name.startswith("~$") and "#recycle" not in f_:
                out.append(f_)
    return sorted(set(out), key=os.path.getmtime, reverse=True)


def page_docs(ver):
    st.title("자료실")
    st.caption("월말·월 마감 보고 PPT, 포장실 결산, 자동 생성 보고서, 직접 올린 파일을 한곳에서 봅니다. 같은 사내망 어느 PC에서든 내려받을 수 있습니다.")
    up_dir = _doc_dir(C.DOC_SOURCES["앱에서 올린 자료"])
    with st.expander("파일 올리기 (PPT·PDF·엑셀·영상)"):
        ups = st.file_uploader("파일 선택", accept_multiple_files=True, key="doc_up")
        if ups and st.button("올리기", type="primary"):
            os.makedirs(up_dir, exist_ok=True)
            for u in ups:
                with open(os.path.join(up_dir, u.name), "wb") as fh:
                    fh.write(u.getbuffer())
            st.success(f"{len(ups)}개 저장: {up_dir}"); st.rerun()
    tabs = st.tabs(list(C.DOC_SOURCES))
    for tab, (label, paths) in zip(tabs, C.DOC_SOURCES.items()):
        with tab:
            d = _doc_dir(paths)
            files = _doc_files(d, C.DOC_DEPTH)
            if "복사본" in "".join(os.path.basename(f_) for f_ in files):
                files = [f_ for f_ in files if "복사본" not in os.path.basename(f_)]
            st.caption(f"폴더: `{d}` · {len(files)}개")
            if not files:
                st.info("파일이 없습니다. (Z: 드라이브가 연결돼 있지 않으면 사본 폴더를 봅니다)"); continue
            rows = [{"파일": os.path.relpath(f_, d), "크기(MB)": round(os.path.getsize(f_) / 1e6, 1), "수정": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(f_)))} for f_ in files]
            st.dataframe(pd.DataFrame(rows), hide_index=True, **_FULL, height=min(60 + 35 * len(rows), 400))
            c1, c2 = st.columns([3, 1])
            names = [os.path.relpath(f_, d) for f_ in files]
            pick = c1.selectbox("파일", names, key=f"doc_pick_{label}")
            path = os.path.join(d, pick)
            size_mb = os.path.getsize(path) / 1e6
            ext = pick.rsplit(".", 1)[-1].lower()
            mime = {"pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
                    "pdf": "application/pdf", "mp4": "video/mp4", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}.get(ext, "application/octet-stream")
            c2.write("")
            if size_mb > C.DOC_MAX_MB:
                c2.warning(f"{size_mb:.0f}MB — 너무 커서 폴더에서 직접 여세요")
            else:
                with open(path, "rb") as fh:
                    data = fh.read()
                c2.download_button(f"⬇ 내려받기 ({size_mb:.1f}MB)", data, file_name=os.path.basename(pick), mime=mime, **_FULL, key=f"doc_dl_{label}")
                if ext == "pdf":
                    try:
                        st.pdf(data, height=700)
                    except Exception:
                        st.caption("PDF 미리보기 구성 요소가 없어 내려받아 보세요 (pip install streamlit[pdf])")
                elif ext == "mp4":
                    st.video(data)
                elif ext in ("png", "jpg"):
                    st.image(data, **_FULL)
            if label == "앱에서 올린 자료" and st.button("이 파일 삭제", key=f"doc_del_{label}"):
                os.remove(path); st.rerun()
    st.caption("폴더는 config.py DOC_SOURCES 에서 바꿉니다. Z: 폴더에 새 달 파일을 넣으면 앱에 바로 보이고(복사 불필요), PPT는 브라우저 미리보기가 안 되어 내려받아 엽니다. 마감보고 영상(mp4)은 앱 안에서 바로 재생됩니다.")


# ───────────────────────── 페이지: 데이터 관리 ─────────────────────────
def page_data(ver):
    st.title("데이터 관리")
    st.markdown(f"""- 환경 로그 CSV(유니팜 수집): `{C.DATA_DIR}`
- 팽이 일지: `{C.log_dir('paengi')}`
- 새송이 일지: `{C.log_dir('ss')}`
- 배양소관리: `{C.log_dir('cult')}`
- 새송이 방별 포장량: `{C.log_dir('ss_extra')}`
- 살균솥 기록(.cld 사본): `{C.log_dir('retort')}`
- 종균 관리 대장·배양실 작업 일지: `{C.log_dir('cult_extra')}`
- 수리 기록(기계수리.xlsx · 수리 완료 보고서.xls · 가습기 수리내역.xlsx): `{C.log_dir('kwon_ss')}` · `{C.log_dir('kwon_paengi')}`
- 캐시: `{C.CACHE_DIR}`  (일지는 {C.LOG_MIN_YEAR}년 폴더부터 읽고, 백업·결산 폴더는 건너뜁니다)""")
    for kind, label in (("paengi", "팽이 일지"), ("ss", "새송이 일지"), ("cult", "배양소관리"), ("ss_extra", "연우농원 요약파일")):
        if not os.path.isdir(C.LOG_SOURCES[kind][0]):
            st.warning(f"{label} 원본 폴더({C.LOG_SOURCES[kind][0]})에 접근할 수 없어 사본 폴더를 읽습니다. Z: 드라이브 연결을 확인하세요.")
    c1, c2, c3 = st.columns(3)
    if c1.button("환경 데이터 갱신 (새로 받은 CSV 반영)", type="primary"):
        box = st.empty(); log = []
        t0 = time.time()
        n = etl_env.run(progress=lambda m: (log.append(m), box.code("\n".join(log[-12:]))))
        st.success(f"{n}개 파일 처리, {time.time()-t0:.0f}초"); st.cache_data.clear(); st.rerun()
    if c2.button("일지 다시 읽기 (바뀐 엑셀만)"):
        box = st.empty(); log = []
        n = etl_logs.run(progress=lambda m: (log.append(m), box.code("\n".join(log[-12:]))))
        st.success(f"{n}개 파일 읽음"); st.cache_data.clear(); st.rerun()
    if c3.button("일지 전부 다시 읽기"):
        n = etl_logs.run(force=True); st.success(f"{n}개 파일 읽음"); st.cache_data.clear(); st.rerun()
    c4, c5 = st.columns([1, 2])
    if c4.button("살균솥 기록 읽기 (바뀐 .cld만)"):
        box = st.empty(); log = []
        n = etl_retort.run(progress=lambda m: (log.append(m), box.code("\n".join(log[-6:]))))
        st.success(f"살균솥 기록 {n}개 파일 읽음"); st.cache_data.clear(); st.rerun()
    st.subheader("바깥 날씨")
    from mushroom_app import weather as WX
    wst = WX.status()
    st.caption(f"{wst['위치']} · {wst['기간']} ({wst['행']}일) — Open-Meteo 공개 자료(무료·키 불필요). 좌표는 config.py 의 FARM_LATLON.")
    w1, w2 = st.columns([1, 2])
    if w1.button("날씨 갱신"):
        try:
            n_w, last_w = WX.update()
            st.success(f"{n_w}일치 (마지막 {last_w})"); st.cache_data.clear(); st.rerun()
        except Exception as e:
            st.error(f"날씨를 받지 못했습니다: {e}")
    w2.caption("배치 이력·병목 찾기의 '바깥 기온'·'비 온 날' 항목이 이 자료를 씁니다. 매일 갱신(refresh_all.bat) 때 자동으로 받습니다.")

    st.subheader("아침 점검 메일")
    from mushroom_app import notify as NT
    cfg = NT.load_config()
    st.caption(f"설정 파일: `{NT.CONFIG}` " + ("(있음 — 받는 사람: " + ", ".join(cfg.get("to", [])) + ")" if cfg else "(없음 — app 폴더의 mail_config.example.json 을 복사해 unifarm 폴더에 mail_config.json 으로 만들고 아이디·앱 비밀번호·받는 사람을 적으세요)"))
    m1, m2 = st.columns([1, 1])
    if m1.button("내용 미리보기 (보내지 않음)"):
        items, kpi = NT.collect()
        html, text = NT.render(items, kpi, pd.Timestamp.today().normalize())
        st.markdown(html, unsafe_allow_html=True)
    if m2.button("지금 보내기 (시험)", disabled=cfg is None):
        try:
            items, kpi = NT.collect(); today_ = pd.Timestamp.today().normalize()
            html, text = NT.render(items, kpi, today_)
            NT.send(cfg, f"[스마트팜] {today_:%m/%d} 점검 메일 (시험)", html, text); st.success("보냈습니다. 받은편지함을 확인하세요.")
        except Exception as e:
            st.error(f"발송 실패: {e}")
    st.subheader("카톡 아침 알림 (단톡방)")
    from mushroom_app import kakao as KK
    kc = KK.load_config() or {}
    st.caption("메인PC에 로그인된 PC카카오톡의 채팅방 창에 글을 써 넣는 방식입니다. 방 이름은 카톡에 보이는 그대로(창 제목) 적으세요. "
               "갱신(refresh_all.bat)이 끝나면 오전에 한 번 자동으로 보냅니다. 카카오 공식 방식은 아니라서 하루 한두 번 짧은 글만 보냅니다.")
    k1, k2, k3 = st.columns([2, 1, 1])
    room = k1.text_input("단톡방 이름", value=kc.get("room", ""), key="kk_room", placeholder="예: 연우/진성 관리")
    lvl = k2.selectbox("이상 감시 포함 수준", ["참고", "주의", "높음"], index=["참고", "주의", "높음"].index(kc.get("min_level", "참고")), key="kk_lvl")
    mx = k3.number_input("최대 줄 수", 3, 20, int(kc.get("max_items", 8)), key="kk_max")
    b1, b2, b3 = st.columns(3)
    if b1.button("설정 저장", key="kk_save"):
        KK.save_config({"room": room.strip(), "min_level": lvl, "max_items": int(mx), "site": kc.get("site", "https://farm.cod-elnath.ts.net")})
        st.success(f"저장했습니다 → {KK.CONFIG}")
    if b2.button("내용 미리보기", key="kk_prev"):
        st.code(KK.compose(cfg={"room": room, "min_level": lvl, "max_items": int(mx), "site": kc.get("site", "https://farm.cod-elnath.ts.net")}))
    if b3.button("지금 보내기 (시험)", key="kk_send", disabled=not room.strip()):
        try:
            KK.send_pc(room.strip(), f"[스마트팜] 카톡 알림 시험 {pd.Timestamp.now():%m/%d %H:%M} — 이 글이 보이면 연결된 것입니다.")
            st.success(f"'{room}' 방에 보냈습니다. 카톡에서 확인하세요.")
        except Exception as e:
            st.error(f"보내지 못했습니다: {e}")
    if os.path.exists(os.path.join(C.DATA_DIR, "_kakao_log.txt")):
        with st.expander("카톡 발송 기록"):
            st.code("".join(open(os.path.join(C.DATA_DIR, "_kakao_log.txt"), encoding="utf-8").readlines()[-15:]))

    rs = {k: v for k, v in etl_retort.status().items() if isinstance(v, dict)}   # "_schema" 같은 메타 항목 제외
    if rs:
        errs = [k for k, v in rs.items() if v.get("error")]
        c5.caption(f"살균솥 기록: {len(rs):,}개 파일 (읽기 오류 {len(errs)}개)")

    st.subheader("환경 데이터 파일")
    st_env = json.load(open(etl_env.STATE, encoding="utf-8")) if os.path.exists(etl_env.STATE) else {}
    env = load_env(ver)
    rows = []
    for p, farm, code in etl_env.raw_files():
        key = os.path.basename(p); s = st_env.get(key, {})
        room = C.room_name(farm, code)
        e = env[env.room == room] if not env.empty else pd.DataFrame()
        rows.append({"파일": key, "방": room, "크기MB": round(os.path.getsize(p) / 1e6, 1), "읽은 행": s.get("rows_total", 0),
                     "첫 날짜": e.date.min().date() if len(e) else None, "마지막 날짜": e.date.max().date() if len(e) else None,
                     "최근30일 운전%": round(100 * e[e.date > e.date.max() - pd.Timedelta(days=30)].run.mean(), 0) if len(e) else None,
                     "마지막 처리": s.get("updated")})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, **_FULL, height=400)
    else:
        st.info("원본 CSV가 없습니다. 수집 스크립트(unifarm_collect.py)로 받은 파일이 데이터 폴더에 있어야 합니다.")
    upd = os.path.join(C.DATA_DIR, "_update_log.txt")
    if os.path.exists(upd):
        with st.expander("수집 갱신 로그 (_update_log.txt)"):
            st.code("".join(open(upd, encoding="utf-8").readlines()[-30:]))
    st.subheader("작업일지 읽기 상태")
    ls = etl_logs.status()
    if ls:
        rows = [{"파일": k, "읽은 시각": v.get("read"), "행": json.dumps(v.get("rows"), ensure_ascii=False) if v.get("rows") else "", "오류": v.get("error", "")} for k, v in sorted(ls.items())]
        st.dataframe(pd.DataFrame(rows), hide_index=True, **_FULL, height=300)
    else:
        st.info("아직 일지를 읽지 않았습니다.")


# ───────────────────────── 메인 ─────────────────────────
# ───────────────────────── 페이지: 계정 관리 (관리자 전용) ─────────────────────────
def page_users(ver, me):
    st.title("계정 관리")
    if me["role"] != "관리자":
        st.error("관리자만 쓸 수 있습니다."); return
    try:
        if st.context.headers.get("x-forwarded-proto", "") != "https" and "localhost" not in str(st.context.headers.get("host", "")):
            st.caption("⚠ 사내망 주소(http)로 접속 중입니다. 여기서 입력하는 비밀번호는 사내망 안에서 암호화되지 않은 채 오갑니다. "
                       "밖에서 쓰는 https 주소로 들어와 바꾸시는 편이 안전합니다.")
    except Exception:
        pass

    users = auth.load()
    st.subheader("계정 목록")
    rows = []
    for k, v in users.items():
        left = auth.is_locked(k)
        rows.append({"이름": k, "등급": v.get("role", "직원"), "상태": f"잠김 {left//60+1}분" if left else "정상"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, **_FULL, height=min(420, 60 + 36 * max(len(rows), 1)))
    st.caption(f"{len(users)}명 · 관리자 {len(auth.admins(users))}명. 비밀번호는 해시로만 저장되어 관리자도 볼 수 없습니다. "
               f"잊었으면 새로 정해 주세요. 변경 기록: `{auth.LOG}`")

    t1, t2, t3, t4 = st.tabs(["계정 추가", "비밀번호 · 등급", "계정 지우기", "접속 기록"])

    with t1:
        with st.form("u_add", clear_on_submit=True):
            c1, c2 = st.columns([2, 1])
            nid = c1.text_input("이름", placeholder="한글 그대로 (동명이인은 김철수2)")
            nrole = c2.selectbox("등급", auth.ROLES, index=1)
            c3, c4 = st.columns(2)
            p1 = c3.text_input("비밀번호", type="password", placeholder=f"숫자 {auth.PIN_MIN}자리 이상")
            p2 = c4.text_input("한 번 더", type="password")
            ok = st.form_submit_button("만들기", type="primary")
        if ok:
            nid = auth.norm(nid)
            bad = auth.check_pin(p1)
            if not nid:
                st.error("이름을 넣으세요.")
            elif nid in users:
                st.error(f"'{nid}' 는 이미 있습니다. 동명이인이면 뒤에 숫자를 붙이세요 (예: {nid}2).")
            elif bad:
                st.error(bad)
            elif p1 != p2:
                st.error("두 번 입력이 다릅니다.")
            else:
                auth.set_password(nid, p1, None, nrole)
                auth.log_change(me["id"], f"계정 추가 {nid} ({nrole})")
                st.success(f"{nid} ({nrole}) 만들었습니다."); st.rerun()

    with t2:
        if not users:
            st.info("계정이 없습니다.")
        else:
            who = st.selectbox("대상", list(users), key="u_edit")
            cur = users[who].get("role", "직원")
            with st.form("u_pw", clear_on_submit=True):
                c1, c2 = st.columns(2)
                q1 = c1.text_input("새 비밀번호", type="password", placeholder=f"숫자 {auth.PIN_MIN}자리 이상")
                q2 = c2.text_input("한 번 더", type="password")
                go = st.form_submit_button("비밀번호 바꾸기")
            if go:
                bad = auth.check_pin(q1)
                if bad:
                    st.error(bad)
                elif q1 != q2:
                    st.error("두 번 입력이 다릅니다.")
                else:
                    auth.set_password(who, q1)
                    auth.unlock(who)
                    auth.sessions_drop_user(who)     # 비번을 바꿨으면 기존 로그인 유지도 끊는다
                    auth.log_change(me["id"], f"비밀번호 변경 {who}")
                    st.success(f"{who} 비밀번호를 바꿨습니다."); st.rerun()
            c1, c2 = st.columns(2)
            newrole = c1.selectbox("등급", auth.ROLES, index=auth.ROLES.index(cur), key="u_role")
            if c1.button("등급 바꾸기", use_container_width=True):
                err = auth.set_role(who, newrole, me["id"])
                (st.error(err) if err else (st.success(f"{who} → {newrole}"), st.rerun()))
            if auth.is_locked(who):
                if c2.button("잠금 풀기", use_container_width=True, type="primary"):
                    auth.unlock(who); auth.log_change(me["id"], f"잠금 해제 {who}"); st.rerun()
            else:
                c2.caption("잠겨 있지 않습니다.")

    with t4:
        c1, c2 = st.columns([1, 3])
        days = c1.selectbox("기간", [7, 14, 30, 90, 365], index=2, format_func=lambda d: f"최근 {d}일", key="ac_days")
        A_ = auth.read_access(days)
        if A_.empty:
            st.info("아직 기록이 없습니다. 이 기능을 넣은 뒤로 로그인한 사람부터 쌓입니다.")
        else:
            c2.caption(f"{len(A_):,}건 · {A_['시각'].min():%m/%d} ~ {A_['시각'].max():%m/%d %H:%M} · 파일 `{auth.ACCESS}`")
            log_in = A_[A_["구분"] == "로그인"]
            st.subheader("사람별")
            rows = []
            for k, v in users.items():
                mine_ = A_[A_["이름"] == k]
                li = log_in[log_in["이름"] == k]
                pv = mine_[mine_["구분"] == "페이지"]
                rows.append({"이름": k, "등급": v.get("role", "직원"),
                             "마지막 접속": li["시각"].max() if len(li) else pd.NaT,
                             "접속 횟수": len(li), "접속한 날": li["시각"].dt.date.nunique() if len(li) else 0,
                             "페이지 본 횟수": len(pv),
                             "많이 본 화면": pv["내용"].mode().iloc[0] if len(pv) else ""})
            U = pd.DataFrame(rows).sort_values(["접속 횟수", "이름"], ascending=[False, True])
            st.dataframe(U, hide_index=True, **_FULL, height=38 + 35 * len(U) + 3,
                         column_config={"마지막 접속": st.column_config.DatetimeColumn(format="MM-DD HH:mm")})
            never = U[U["접속 횟수"] == 0]["이름"].tolist()
            if never:
                st.warning(f"이 기간에 한 번도 안 들어온 사람: {', '.join(never)}")
            else:
                st.success("계정이 있는 분들은 모두 한 번 이상 들어왔습니다.")

            st.subheader("많이 본 화면")
            pv = A_[A_["구분"] == "페이지"]
            if not pv.empty:
                g = pv.groupby("내용").size().sort_values(ascending=False).reset_index()
                g.columns = ["화면", "횟수"]
                st.dataframe(g.head(20), hide_index=True, **_FULL, height=38 + 35 * min(len(g), 20) + 3)

            with st.expander("기록 그대로 보기"):
                who = st.multiselect("사람", sorted(A_["이름"].unique()), default=[], key="ac_who")
                kind = st.multiselect("구분", sorted(A_["구분"].unique()), default=[], key="ac_kind")
                V = A_
                if who:
                    V = V[V["이름"].isin(who)]
                if kind:
                    V = V[V["구분"].isin(kind)]
                st.dataframe(V.head(500), hide_index=True, **_FULL, height=420,
                             column_config={"시각": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm:ss")})
                st.caption(f"{len(V):,}건 중 최근 500건. 로그인 실패도 남습니다(비밀번호는 안 남김).")
            st.caption("직원분들에게 접속 기록이 남는다는 점은 미리 알려 두시는 게 좋습니다.")

    with t3:
        st.caption("그만둔 분의 계정은 바로 지워 주세요. 되돌릴 수 없습니다.")
        others = [k for k in users if k != me["id"]]
        if not others:
            st.info("지울 수 있는 계정이 없습니다.")
        else:
            who = st.selectbox("대상", others, key="u_del")
            sure = st.checkbox(f"'{who}' 계정을 지웁니다", key="u_del_ok")
            if st.button("지우기", disabled=not sure):
                err = auth.delete_user(who, me["id"])
                (st.error(err) if err else (st.success(f"{who} 지웠습니다."), st.rerun()))



# ───────────────────────── 화면 하단 AI 물어보기 ─────────────────────────
def _ai_ctx(page, crop):
    """지금 보고 있는 화면을 한 줄로. 페이지에서 st.session_state['_ctx_*'] 에 넣어둔 값을 쓴다."""
    bits = [page + (f"({crop})" if crop else "")]
    b = st.session_state.get("_ctx_batch")
    if b and page in ("배치 탐색기", "배치 이력"):
        bits.append(f"선택한 배치 {b}")
    d = st.session_state.get("_ctx_note")
    if d:
        bits.append(d)
    return " · ".join(bits)


def _ai_run(q, ctx, role, ver):
    """질문 한 건 처리. 대화는 AI 질문 페이지와 같은 것을 쓴다."""
    loaders = ai_chat.make_loaders(A, etl_logs, lambda: load_env(ver), lambda: load_paengi(ver), lambda: load_ss(ver),
                                   lambda: load_monthly(ver), lambda d: load_anomalies(ver, d))
    msgs = st.session_state.get("ai_msgs", []) + [{"role": "user", "content": q}]
    try:
        text, new_msgs = ai_chat.ask(msgs, loaders, ctx=ctx, role=role)
    except Exception as e:
        text, new_msgs = f"오류가 났습니다: {e}", msgs
    st.session_state.ai_msgs = new_msgs
    st.session_state.setdefault("ai_view", []).extend([("user", q), ("assistant", text)])
    return text


@st.dialog("AI 물어보기", width="large")
def _ai_dialog(ctx, role, ver):
    st.caption("보는 중: " + ctx)
    for r, t in st.session_state.get("ai_view", [])[-6:]:
        with st.chat_message(r, avatar=(ICON if r == "assistant" and os.path.exists(ICON) else None)):
            st.markdown(t)
    pend = st.session_state.pop("_ai_pending", None)
    if pend:
        with st.chat_message("user"):
            st.markdown(pend)
        with st.chat_message("assistant", avatar=ICON if os.path.exists(ICON) else None):
            with st.spinner("자료를 찾는 중…"):
                st.markdown(_ai_run(pend, ctx, role, ver))
    with st.form("_ai_more", clear_on_submit=True):
        more = st.text_input("이어서 물어보기", placeholder="예: 그럼 같은 달 다른 방은 어땠어?")
        go = st.form_submit_button("보내기", type="primary", use_container_width=True)
    if go and more.strip():
        st.session_state["_ai_pending"] = more.strip()
        st.rerun()
    c1, c2 = st.columns(2)
    if c1.button("대화 지우기", use_container_width=True):
        st.session_state.ai_msgs, st.session_state.ai_view = [], []
        st.session_state.pop("_ai_open", None)
        st.rerun()
    if c2.button("닫기", use_container_width=True):
        st.session_state.pop("_ai_open", None)
        st.rerun()


def ai_dock(ver, me, page, crop):
    """어느 화면에서나 아래쪽에 붙어 있는 질문창. 답은 창을 띄워 보여준다.
    창이 열려 있는 동안에는 그리지 않는다.
    v1.23.1: st.chat_input 을 본문 맨 바닥(스트림릿 bottom 영역)에 두면 스트림릿이 본문 전체를 채팅 화면처럼
    '맨 아래를 따라가는' 모드로 바꾼다. 그래서 느린 페이지(배치 이력 등)가 다 그려지는 동안 화면이 맨 아래로
    끌려 내려갔다. 질문창을 본문 안의 상자(key=ai_dock)에 두고 CSS(position: sticky, main 의 <style>)로 화면 아래에 붙인다."""
    if page == "AI 질문" or "AI 질문" not in auth.can_see(me["role"]):
        return
    if not ai_chat.api_key():
        return
    ctx = _ai_ctx(page, crop)
    if st.session_state.get("_ai_open") or st.session_state.get("_ai_pending"):
        _ai_dialog(ctx, me["role"], ver)
        return
    with st.container(key="ai_dock"):
        q = st.chat_input("궁금한 걸 물어보세요 — 지금 보는 화면 기준으로 답합니다")
    if q and q.strip():
        st.session_state["_ai_pending"] = q.strip()
        st.session_state["_ai_open"] = True
        st.rerun()


def _office_port():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "office_port.txt")
    try:
        if os.path.exists(p):
            return int(open(p).read().strip())
    except Exception:
        pass
    return getattr(C, "OFFICE_PORT", 8502)


def _lan_ip():
    import socket
    try:
        s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s_.connect(("10.255.255.255", 1))            # 실제로 보내지 않음 — 이 PC 의 사내망 주소만 알아낸다
        ip = s_.getsockname()[0]
        s_.close()
        return ip
    except Exception:
        return "localhost"


def _office_running(port):
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _office_page(v, me):
    """경영 자료는 따로 띄운 앱(같은 PC, 다른 포트)으로 보낸다 (v1.29.0). 그 앱이 꺼져 있으면 여기서 그린다."""
    if not getattr(C, "OFFICE_SEPARATE", True):
        from mushroom_app import page_office as PO
        PO.render(v, me); return
    port = _office_port()
    try:
        hdr = st.context.headers or {}
    except Exception:
        hdr = {}
    host = hdr.get("host", "")
    outside = hdr.get("x-forwarded-proto", "") == "https" or (host and ":" not in host)   # 사이트 공개(https 터널)로 들어온 경우
    name = _lan_ip() if (outside or not host) else host.rsplit(":", 1)[0]
    url = f"http://{name}:{port}"
    if not _office_running(port):
        st.warning(f"경영 자료 전용 화면({url})이 아직 안 떠 있습니다 — 1~2분 안에 자동으로 켜집니다. 급하면 아래 버튼으로 여기서 엽니다(조금 느림).")
        if st.button("여기서 경영 자료 열기", key="office_inline"):
            st.session_state["_office_inline"] = True
        if st.session_state.get("_office_inline"):
            from mushroom_app import page_office as PO
            PO.render(v, me)
        return
    st.title("경영 자료")
    if outside:
        st.info(f"경영 자료는 사내망에서만 열립니다: **{url}** (회사 PC·와이파이에서 접속)")
    st.link_button(f"경영 자료 열기 → {url}", url, type="primary")
    st.caption("경영 자료는 본 앱이 느려지지 않도록 따로 띄운 화면에서 엽니다. 계정·로그인은 같습니다. 돌아올 때는 그 화면 왼쪽 위 '← 통합관리 시스템으로'.")
    if not outside and st.session_state.get("_office_jumped") != url:
        st.session_state["_office_jumped"] = url           # 한 번만 자동으로 넘어간다(뒤로 오면 버튼으로)
        components.html(f"""<script>
          try {{ const a = window.parent.document.createElement('a'); a.href = {json.dumps(url)}; a.target = '_self';
                 window.parent.document.body.appendChild(a); a.click(); }} catch (e) {{}}
        </script>""", height=0)


NAV_LAST = "반가공 현황"    # 왼쪽 메뉴 구분선 아래에 따로 두는 메뉴 (v1.21.1). 그 아래에 경영 자료(관리자만, v1.26.7)


def _nav_css(allowed):
    """왼쪽 메뉴: '반가공 현황' 위에 구분선을 그어 스마트팜 메뉴와 나눠 보이게 한다. 관리자는 그 아래 '경영 자료'가 더 있다.
    라디오 하나를 그대로 쓰고 CSS 로 선만 긋는다(선택·등급·접속 기록·맨 위로 올리기 동작은 그대로).
    반가공 현황이 목록 끝(또는 끝에서 둘째)에 있을 때만 적용. 선택자는 스트림릿 버전과 상관없이 '라디오 묶음의 n번째 칸'만 본다."""
    if not allowed or NAV_LAST not in allowed[-2:]:
        return ""
    nth = len(allowed) - allowed.index(NAV_LAST)          # 끝에서 몇 번째인가 (1 = 마지막)
    g = '[role="radiogroup"][aria-label="페이지"]'
    box = f'[data-testid="stSidebar"] :is([data-testid="stElementContainer"], .element-container, [data-testid="stRadio"]):has({g})'
    g = f'[data-testid="stSidebar"] {g}'
    return f"""
    {box}, {g} {{width:100% !important; max-width:100% !important}}
    {g} > :nth-last-child({nth}) {{align-self:stretch; margin-top:.55rem; padding-top:.55rem;
                        border-top:1px solid rgba(128,128,128,.35); border-top-color:color-mix(in srgb, currentColor 20%, transparent)}}
    """


def _scroll_hook():
    """왼쪽 메뉴를 누르는 순간 본문을 맨 위로 올린다.
    화면이 다시 그려진 뒤에 올리면 새 페이지의 중간이 잠깐 보였다가 튀어 올라 어색하다.
    누르는 시점에 미리 올려두면 새 내용이 처음부터 위에서 그려진다.
    주의 1: 이 코드는 부모 창(앱 본체)에 직접 심는다. iframe 안에서 돌면 스트림릿이 그 iframe 을
            지울 때 같이 죽어 버려서, 두 번째 이동부터는 동작하지 않는다.
    주의 2: 예전에는 첫 실행에만 그렸는데, 그러면 두 번째 그림부터 본문 맨 앞 자리의 요소 종류가
            iframe → 다른 것으로 바뀐다. 스트림릿 화면은 자리 번호로 갈아끼우기 때문에 이때
            가끔 removeChild 오류(빨간 상자)가 났다. 매번 같은 자리에 같은 내용을 그려 두면
            브라우저가 iframe 을 그대로 재사용하므로 그 문제가 없다. 아래 __farmNavHook
            표시 덕분에 여러 번 그려도 실제 설치는 한 번만 된다."""
    components.html("""<script>
      const w = window.parent, D = w.document;
      // 브라우저 자동 번역이 글자를 바꿔치기해도 같은 오류가 난다. 번역 대상에서 뺀다.
      D.documentElement.setAttribute('translate', 'no');
      D.documentElement.classList.add('notranslate');
      if (!w.__farmNavHook) {
        w.__farmNavHook = true;
        const s = D.createElement('script');
        // 브라우저 '스크롤 앵커링' 끄기 — 내용이 채워질 때 브라우저가 보던 위치를 지키려고
        // 스크롤을 계속 조정하는데, 그것과 우리 코드가 싸우면서 화면이 오르락내리락했다.
        const css = D.createElement('style');
        css.textContent = 'section.stMain, [data-testid="stMain"], section.main { overflow-anchor: none !important; }';
        D.head.appendChild(css);
        s.textContent = `
          (function () {
            const SEL = 'section.stMain, [data-testid="stMain"], section.main';
            const el = () => document.querySelector('section.stMain') || document.querySelector('[data-testid="stMain"]');
            const up = () => { const e = el(); if (e && e.scrollTop > 0) { e.scrollTop = 0; return true; } 
                               if (window.scrollY > 0) { window.scrollTo(0, 0); return true; } return false; };
            let hold = false, fixes = 0;
            const release = () => { hold = false; };
            document.addEventListener('pointerdown', (e) => {
              const t = e.target;
              if (!t || !t.closest) return;
              if (t.closest('[data-testid="stSidebar"] [role="radiogroup"]')) {
                up(); hold = true; fixes = 0;
                clearTimeout(window.__farmT);
                window.__farmT = setTimeout(release, 30000);
              } else if (!t.closest('[data-testid="stSidebar"]')) {
                release();
              }
            }, true);
            // 앵커링을 껐으므로 평소에는 손댈 일이 없다. 그래도 되돌려지면 고치되,
            // 몇 번 이상 고쳐야 하면(= 계속 싸우는 상황) 아예 손을 뗀다. 화면이 떨리지 않게.
            const tick = () => {
              if (hold && up()) { if (++fixes > 8) release(); }
              requestAnimationFrame(tick);
            };
            requestAnimationFrame(tick);
            ['wheel', 'touchstart', 'touchmove', 'keydown'].forEach(ev =>
              window.addEventListener(ev, release, {capture: true, passive: true}));
            window.__farmState = () => ({hold: hold, fixes: fixes});
            // 아래 질문창(ai_dock) 배경을 앱 배경색에 맞춘다 (테마가 바뀌어도 따라감)
            const syncBg = () => {
              const a = document.querySelector('.stApp') || document.body;
              const c = a ? getComputedStyle(a).backgroundColor : '';
              if (c && c !== 'rgba(0, 0, 0, 0)' && c !== 'transparent') document.documentElement.style.setProperty('--farm-bg', c);
            };
            syncBg(); setInterval(syncBg, 1500);
          })();
        `;
        D.head.appendChild(s);
      }
    </script>""", height=0)


def main():
    C.ensure_dirs()
    me = auth.gate()                      # 계정이 하나도 없으면 로그인 없이 지나간다
    allowed = auth.can_see(me["role"])
    _scroll_hook()                        # 본문 맨 앞 고정 자리
    with st.sidebar:
        if os.path.exists(ICON):
            c_ic, c_ti = st.columns([1, 3])
            c_ic.image(ICON, width=48)
            c_ti.markdown(f"### {C.APP_NAME}")
        else:
            st.markdown(f"### 🍄 {C.APP_NAME}")
        page = st.radio("페이지", allowed, label_visibility="collapsed")
        crop = st.radio("작물", ["팽이", "새송이"], horizontal=True) if page in ("배치 탐색기", "계절·월별 비교") else None
        st.divider()
        v = _ver()
        env_p = os.path.join(C.CACHE_DIR, "env_daily.parquet")
        if os.path.exists(env_p):
            st.caption(f"캐시 갱신: {time.strftime('%m/%d %H:%M', time.localtime(os.path.getmtime(env_p)))}")
        if st.button("화면 새로고침"):
            st.cache_data.clear(); st.rerun()
        # 인쇄: 사이드바·상단 메뉴를 숨기고 본문만 인쇄 (브라우저 인쇄 대화상자 → PDF 저장도 가능)
        # + 메뉴 모양(_nav_css). 요소를 새로 늘리지 않으려고 같은 <style> 안에 넣는다.
        st.markdown("""<style>@media print {
            [data-testid="stSidebar"], header, [data-testid="stToolbar"], [data-testid="stStatusWidget"], [data-testid="stDecoration"] {display:none !important;}
            [data-testid="stMain"] .block-container, .main .block-container {max-width:100% !important; padding:0.5cm !important;}
            [data-testid="stMain"] {overflow:visible !important;}
            .st-key-ai_dock {display:none !important;}
        }
        /* 아래 질문창: 본문 맨 끝 상자를 화면 아래에 붙인다. 스트림릿이 상자를 감싸는 칸(stLayoutWrapper)이 있으면 그 칸을 붙여야 한다 */
        [data-testid="stLayoutWrapper"]:has(> .st-key-ai_dock), .st-key-ai_dock {position:sticky; bottom:0; z-index:99; background:var(--farm-bg, #ffffff);}
        .st-key-ai_dock {margin-top:.4rem; padding:.55rem 0 .85rem 0; box-shadow:0 -12px 12px -12px rgba(0,0,0,.18);}
        @media print {[data-testid="stLayoutWrapper"]:has(> .st-key-ai_dock) {display:none !important;}}
        """ + _nav_css(allowed) + "</style>", unsafe_allow_html=True)
        components.html("""<button onclick="window.parent.print()" style="width:100%;padding:6px 10px;border:1px solid #d0d4cc;border-radius:6px;background:#fff;cursor:pointer;font-size:13px">🖨 이 화면 인쇄 / PDF</button>""", height=40)
        if not me.get("무인증"):
            st.divider(); auth.sidebar_box()
    if page not in allowed:               # 주소를 직접 쳐서 들어오는 경우 막기
        st.error("이 페이지를 볼 수 있는 등급이 아닙니다."); return
    if not me.get("무인증") and st.session_state.get("_seen_page") != page:
        st.session_state["_seen_page"] = page
        auth.log_access(me, "페이지", page + (f" ({crop})" if crop else ""))
    st.session_state["_ctx_note"] = None
    if page == "개요":
        page_overview(v)
    elif page == "배치 탐색기":
        page_batches(v, crop)
    elif page == "일일 확인":
        page_checklist(v)
    elif page == "배치 이력":
        page_dossier(v)
    elif page == "계절·월별 비교":
        page_compare(v, crop)
    elif page == "배양실":
        page_cult(v)
    elif page == "종균·액탱크":
        page_spawn(v)
    elif page == "살균":
        page_retort(v)
    elif page == "원가":
        page_cost(v)
    elif page == "경영 자료":
        _office_page(v, me)
    elif page == "재고 대사":
        page_stock(v)
    elif page == "이상 감시":
        page_anomalies(v)
    elif page == "수리 이력":
        page_repairs(v, me)
    elif page == "보고서":
        page_report(v)
    elif page == "자료실":
        page_docs(v)
    elif page == "AI 질문":
        page_ai(v)
    elif page == "계정 관리":
        page_users(v, me)
    elif page == "반가공 현황":
        PB.render(me)
    else:
        page_data(v)
    if page != "반가공 현황":                # 반가공 현황은 AI 질문 표에 아직 없음
        ai_dock(v, me, page, crop)            # 어느 화면에서나 아래쪽 질문창


if __name__ == "__main__" or True:
    main()
