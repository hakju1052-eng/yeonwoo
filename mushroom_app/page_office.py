# -*- coding: utf-8 -*-
"""'경영 자료' 페이지 — 경리(사무실) 자료를 한곳에 정리 (관리자 전용, v1.25.0).

자료: etl_office (Z:\\사무실\\SynologyDrive 원본, 없으면 Downloads\\SynologyDrive_사본).
탭: 매출 현황 · 거래처별 · 시장 단가 · 매입·매출 · 전기·수도 · 자료 위치
"""
import os, time, json
import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

from . import etl_office as O

try:
    from packaging.version import Version as _V
    _FULL = {"width": "stretch"} if _V(st.__version__) >= _V("1.47") else {"use_container_width": True}
except Exception:
    _FULL = {"use_container_width": True}

CROP_COLOR = alt.Scale(domain=["팽이", "새송이", "기타"], range=["#2a78d6", "#eb6834", "#8a8f84"])
CH_COLOR = alt.Scale(domain=["도매시장", "직거래·납품", "온라인"], range=["#2a78d6", "#1baf7a", "#eb6834"])


EOK = alt.Axis(labelExpr="datum.value == 0 ? '0' : format(datum.value / 1e8, ',.1~f') + '억'")   # 원 → 억 눈금
CAT = alt.Scale(scheme="category10")


def _ym(df, col="연월"):
    """x축용 '26-07' 같은 월 글자 열(정렬도 글자순 = 시간순)"""
    df = df.copy(); df["월표시"] = pd.to_datetime(df[col]).dt.strftime("%y-%m"); return df


def _eok(v):
    """원 → '12.3억' / '4,560만'"""
    if v is None or pd.isna(v):
        return "—"
    return f"{v / 1e8:,.1f}억" if abs(v) >= 1e8 else f"{v / 1e4:,.0f}만"


def _pct(a, b):
    return (a / b - 1) * 100 if b and pd.notna(a) and pd.notna(b) and b != 0 else np.nan


def _delta(p):
    return None if p is None or pd.isna(p) else f"{p:+.1f}%"


@st.cache_data(ttl=600, show_spinner="경리 자료 읽는 중…")
def _load(ver):
    """경영 자료 묶음 — 계산은 office_bundle 이 하고 디스크에 저장해 두므로(서명이 같으면 1초 안) 여기서는 받아오기만 (v1.27.1)"""
    from . import office_bundle
    return office_bundle.get()


def _err(df):
    return isinstance(df, pd.DataFrame) and "_error" in df


def render(ver, me):
    st.title("경영 자료")
    if me.get("role") != "관리자" and not me.get("무인증"):
        st.error("관리자만 볼 수 있습니다."); return
    st.session_state["_office_me"] = me.get("name") or me.get("id") or ""
    src = O.root()
    c1, c2 = st.columns([5, 1])
    c1.caption(f"경리(사무실) 자료를 모아 정리한 화면입니다 — 관리자만 보입니다. 원본 폴더: `{src}`"
               + (" (Z: 드라이브가 안 보여 사본을 읽는 중)" if not src.upper().startswith("Z:") else ""))
    if c2.button("다시 읽기", key="of_reload", help="원본이 바뀌었는지 지금 확인합니다. 바뀐 게 없으면 저장본을 그대로 씁니다(1초). 바뀌었으면 다시 계산합니다(20초쯤)."):
        _load.clear(); st.rerun()
    if not os.path.isdir(src):
        st.warning("원본 폴더를 찾지 못했습니다. Z: 드라이브 연결을 확인하거나 Downloads\\SynologyDrive_문서_복사.bat 을 실행하세요."); return
    D = _load(ver)
    if D.get("made"):
        c1.caption(f"자료 계산 시각 {D['made']} ({D.get('secs', 0):.0f}초 걸림) — 원본이 바뀌면 갱신(09:00·15:00) 때 미리 다시 계산합니다.")
    tabs = st.tabs(["매출 현황", "거래처별", "시장 단가", "출하 대사", "매입·매출", "전기·수도", "자료 위치"])
    with tabs[0]:
        _tab_sales(D)
    with tabs[1]:
        _tab_customers(D)
    with tabs[2]:
        _tab_market(D)
    with tabs[3]:
        _tab_recon(D)
    with tabs[4]:
        _tab_inout(D)
    with tabs[5]:
        _tab_util(D)
    with tabs[6]:
        _tab_catalog(D, src)


# ───────────────────────── 매출 현황 ─────────────────────────
def _tab_sales(D):
    M, S = D["M"], D["S"]
    if M.empty or _err(M):
        st.info("월별 매출,물량.xlsx 를 찾지 못했거나 양식이 달라 읽지 못했습니다." + (f" ({M['_error'].iloc[0]})" if _err(M) else "")); return
    sheet_last = M["연월"].max()
    E = D.get("ME", pd.DataFrame())                     # 월별 표에 아직 없는 달 — 거래명세표 + 포장일지 (v1.26.4)
    M = M.assign(잠정=False)
    if len(E):
        M = pd.concat([M, E[M.columns.intersection(E.columns).tolist() + [c for c in ("마지막날", "미정박스") if c in E]]], ignore_index=True)
    this_m = pd.Timestamp.today().to_period("M").to_timestamp()
    ongoing = M[(M["연월"] >= this_m) & (M["작물"] == "합계")]
    F = M[M["연월"] < this_m]                            # 다 끝난 달만 누적·비교
    Y = int(F["연"].max())
    cur = F[(F["연"] == Y) & (F["작물"] == "합계")]
    last_m = int(cur["월"].max()) if len(cur) else 12
    prev = F[(F["연"] == Y - 1) & (F["월"] <= last_m)]
    prov = sorted(F.loc[F["잠정"] & (F["연"] == Y), "월"].unique())
    src = f"1~{sheet_last.month}월 경리 월별 표" + (f", {'·'.join(str(m) for m in prov)}월은 거래명세표·포장일지 잠정" if prov else "")
    st.markdown(f"**{Y}년 1~{last_m}월 누적** ({src}) · 전년 같은 기간과 비교")
    cols = st.columns(4)
    for col, crop in zip(cols[:3], ["합계", "팽이", "새송이"]):
        a = F[(F["연"] == Y) & (F["작물"] == crop)]["매출"].sum()
        b = prev[prev["작물"] == crop]["매출"].sum()
        col.metric(f"{crop} 매출", _eok(a), _delta(_pct(a, b)))
    a_kg, a_w = cur["물량kg"].sum(), cur["매출"].sum()
    p_t = prev[prev["작물"] == "합계"]
    pr = (a_w / a_kg) if a_kg else np.nan
    pr0 = (p_t["매출"].sum() / p_t["물량kg"].sum()) if p_t["물량kg"].sum() else np.nan
    cols[3].metric("kg당 매출단가 (합계)", f"{pr:,.0f}원" if pd.notna(pr) else "—", _delta(_pct(pr, pr0)))
    if len(ongoing):
        o = ongoing.iloc[-1]
        und = o.get("미정박스", 0) or 0
        st.info(f"{o['연월']:%Y년 %m월} 진행 중 ({pd.Timestamp(o['마지막날']):%m/%d} 출고까지): 매출 {_eok(o['매출'])} · 물량 {o['물량kg']:,.0f}kg · {o['박스']:,.0f}박스"
                + (f" — 시장 경락가가 아직 장부에 없는 {und:,.0f}박스는 0원으로 들어가 있습니다" if und else ""))
    X = M[M["작물"] != "합계"].copy()
    X["연도"] = X["연"].astype(str)
    c1, c2 = st.columns(2)
    for col, crop in zip((c1, c2), ("팽이", "새송이")):
        Z = X[(X["작물"] == crop) & (X["연월"] < this_m)]          # 진행 중인 달은 그림에서 뺌
        base = alt.Chart(Z).encode(x=alt.X("월:O", title=None), color=alt.Color("연도:N", title=None, legend=alt.Legend(orient="top")))
        col.markdown(f"**{crop} 월 매출**")
        col.altair_chart((base.mark_line(point=True).encode(y=alt.Y("매출:Q", title="매출(원)", axis=EOK),
                                                           tooltip=["연도:N", "월:O", alt.Tooltip("매출:Q", format=",.0f"), alt.Tooltip("물량kg:Q", format=",.0f"),
                                                                    alt.Tooltip("kg당단가:Q", format=",.0f", title="kg당 단가")])).properties(height=220), **_FULL)
        col.altair_chart((base.mark_line(point=True, strokeDash=[4, 2]).encode(y=alt.Y("kg당단가:Q", title="kg당 매출단가(원)", scale=alt.Scale(zero=False)),
                                                                               tooltip=["연도:N", "월:O", alt.Tooltip("kg당단가:Q", format=",.0f")])).properties(height=160), **_FULL)
    T = X.pivot_table(index="연월", columns="작물", values=["물량kg", "박스", "매출", "kg당단가"], aggfunc="sum").sort_index(ascending=False)
    kind = X.groupby("연월")["잠정"].any().map({True: "잠정(명세표+일지)", False: "월별 표"})
    kind[kind.index >= this_m] = "진행 중"
    T.columns = [f"{b} {a}".replace("kg당단가", "kg당 단가").replace("물량kg", "물량(kg)") for a, b in T.columns]
    T.index = T.index.strftime("%Y-%m")
    if len(S) and not _err(S):
        chk = S.groupby("연월")["공급가액"].sum(); chk.index = chk.index.strftime("%Y-%m")
        T["거래명세표 합계"] = chk.reindex(T.index)
    kind.index = kind.index.strftime("%Y-%m")
    T["자료"] = kind.reindex(T.index)
    order = [c for c in ["팽이 물량(kg)", "팽이 박스", "팽이 매출", "팽이 kg당 단가", "새송이 물량(kg)", "새송이 박스", "새송이 매출", "새송이 kg당 단가", "거래명세표 합계"] if c in T]
    T[order] = T[order].round(0)
    st.dataframe(T[order + ["자료"]], **_FULL, height=360, column_config={c: st.column_config.NumberColumn(format="localized") for c in order})
    st.caption("자료: 월별 매출,물량.xlsx (물량·박스는 포장 기준, 매출은 경리나라). 월별 표에 아직 없는 달은 잠정 — 물량·박스는 포장실 일지 출고 합"
               "(지난달들은 월별 표와 같은 값), 매출은 경리나라 거래명세표 + 명세표가 아직 없는 날의 포장실 일지 출고 금액(시장 경락가×박스). "
               "'거래명세표 합계'는 거래내역 카톡 폴더의 매출 거래명세표를 달별로 더한 값으로, 두 값이 크게 다르면 어느 한쪽 입력을 확인하세요.")


# ───────────────────────── 거래처별 ─────────────────────────
def _tab_customers(D):
    S = D["S"]
    if S.empty or _err(S):
        st.info(S["_error"].iloc[0] if _err(S) else "거래내역 카톡 폴더에서 매출 거래명세표를 찾지 못했습니다."); return
    last = S["연월"].max()
    c1, c2, c3 = st.columns([1.3, 1, 2])
    span = c1.selectbox("기간", ["최근 3개월", "최근 12개월", f"{last.year}년", "전체"], key="of_span")
    crop = c2.radio("작물", ["전체", "팽이", "새송이"], horizontal=True, key="of_crop")
    chs = c3.multiselect("판매 경로", ["도매시장", "직거래·납품", "온라인"], default=["도매시장", "직거래·납품", "온라인"], key="of_ch")
    d0 = {"최근 3개월": last - pd.DateOffset(months=2), "최근 12개월": last - pd.DateOffset(months=11), f"{last.year}년": pd.Timestamp(last.year, 1, 1)}.get(span, S["연월"].min())
    X = S[(S["연월"] >= d0) & S["채널"].isin(chs)]
    if crop != "전체":
        X = X[X["작물"] == crop]
    if X.empty:
        st.info("조건에 맞는 거래가 없습니다."); return
    tot = X["공급가액"].sum()
    k = st.columns(5)
    k[0].metric("매출 (공급가액)", _eok(tot)); k[0].caption(f"{X['연월'].min():%Y-%m} ~ {X['연월'].max():%Y-%m}")
    for col, ch in zip(k[1:4], ["도매시장", "직거래·납품", "온라인"]):
        v = X[X["채널"] == ch]["공급가액"].sum()
        col.metric(f"{ch} 비중", f"{100 * v / tot:.1f}%" if tot else "—"); col.caption(_eok(v))
    k[4].metric("거래처 수", f"{X['거래처'].nunique()}곳"); k[4].caption(f"거래 {len(X):,}건")
    G = X.groupby(["거래처", "채널"]).agg(매출=("공급가액", "sum"), 건수=("공급가액", "size"), 첫거래=("거래일", "min"), 마지막=("거래일", "max"),
                                         팽이=("공급가액", lambda s: s[X.loc[s.index, "작물"] == "팽이"].sum()),
                                         새송이=("공급가액", lambda s: s[X.loc[s.index, "작물"] == "새송이"].sum())).reset_index().sort_values("매출", ascending=False)
    G["비중%"] = (100 * G["매출"] / tot).round(1)
    c1, c2 = st.columns([1.1, 1])
    top = G.head(15)
    c1.markdown("**거래처 상위 15곳**")
    c1.altair_chart(alt.Chart(top).mark_bar().encode(
        y=alt.Y("거래처:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=160)), x=alt.X("매출:Q", title="매출(원)", axis=EOK),
        color=alt.Color("채널:N", scale=CH_COLOR, legend=alt.Legend(orient="top", title=None)),
        tooltip=["거래처:N", "채널:N", alt.Tooltip("매출:Q", format=",.0f"), alt.Tooltip("비중%:Q", format=".1f"), "건수:Q"]).properties(height=380), **_FULL)
    MC = X.groupby(["연월", "채널"])["공급가액"].sum().reset_index()
    c2.markdown("**월별 판매 경로**")
    c2.altair_chart(alt.Chart(_ym(MC)).mark_bar().encode(
        x=alt.X("월표시:O", title=None), y=alt.Y("공급가액:Q", title="매출(원)", axis=EOK),
        color=alt.Color("채널:N", scale=CH_COLOR, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("월표시:O", title="월"), "채널:N", alt.Tooltip("공급가액:Q", format=",.0f")]).properties(height=380), **_FULL)
    show = G.assign(첫거래=G["첫거래"].dt.strftime("%Y-%m-%d"), 마지막=G["마지막"].dt.strftime("%Y-%m-%d"))
    st.dataframe(show[["거래처", "채널", "매출", "비중%", "팽이", "새송이", "건수", "첫거래", "마지막"]], hide_index=True, **_FULL, height=320,
                 column_config={c: st.column_config.NumberColumn(format="localized") for c in ("매출", "팽이", "새송이", "건수")})
    st.markdown("**거래처 한 곳 자세히**")
    who = st.selectbox("거래처", G["거래처"].tolist(), key="of_who")
    W = S[S["거래처"] == who]
    c1, c2 = st.columns([1.2, 1])
    WM = W.groupby(["연월", "작물"])["공급가액"].sum().reset_index()
    c1.altair_chart(alt.Chart(_ym(WM)).mark_bar().encode(
        x=alt.X("월표시:O", title=None), y=alt.Y("공급가액:Q", title="매출(원)", axis=EOK),
        color=alt.Color("작물:N", scale=CROP_COLOR, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("월표시:O", title="월"), "작물:N", alt.Tooltip("공급가액:Q", format=",.0f")]).properties(height=240), **_FULL)
    R = W.sort_values("거래일", ascending=False).head(40)
    c2.dataframe(R.assign(거래일=R["거래일"].dt.strftime("%Y-%m-%d"))[["거래일", "품목", "작물", "공급가액"]], hide_index=True, **_FULL, height=240,
                 column_config={"공급가액": st.column_config.NumberColumn(format="localized")})
    st.caption("자료: 거래내역 카톡\\M월 거래명세표 (경리나라 매출 거래명세표, 공급가액 기준). 판매 경로는 거래처 이름으로 나눔 — 청과·공판장·농산물·경인농산 = 도매시장, "
               "11번가·쿠팡·G마켓·옥션·네이버·토스 = 온라인, 나머지 = 직거래·납품(아이쿱·삼진글로벌넷·행복한 농부들 등). '외 N건'처럼 여러 품목이 한 줄로 묶인 거래가 있어 품목별 금액은 나누지 않았습니다.")


# ───────────────────────── 시장 단가 ─────────────────────────
def _tab_market(D):
    crop = st.radio("작물", ["팽이", "새송이"], horizontal=True, key="of_mcrop")
    _market_daily(D, crop)
    with st.expander("월별 정리표 — 경리의 'M월 팽이·새송이 시장단가' 파일 (2024-06~, 경리가 달마다 정리)"):
        _market_monthly(D, crop)


def _market_daily(D, crop):
    """포장실 입출고 장부의 '시장' 출고 = 매일 도매시장 출하량·경락 단가 (v1.25.1)"""
    MD = D.get("MD", pd.DataFrame())
    st.markdown("**일별 시장 출하·단가** (포장실 생산 상세일지 '입출고내역' — 매일 09:00·15:00 갱신)")
    X = MD[MD["작물"] == crop] if len(MD) else MD
    if X.empty:
        st.info("포장실 입출고 장부 자료가 없습니다. 데이터 관리에서 일지를 다시 읽어 주세요."); return
    order = X.groupby("품목")["박스"].sum().sort_values(ascending=False).index.tolist()
    c1, c2 = st.columns([3, 1])
    item = c1.radio("품목", order, horizontal=True, key=f"of_ditem_{crop}")
    span = c2.selectbox("기간", ["최근 30일", "최근 90일", "올해", "전체"], key="of_dspan")
    last = X["날짜"].max()
    d0 = {"최근 30일": last - pd.Timedelta(days=29), "최근 90일": last - pd.Timedelta(days=89), "올해": pd.Timestamp(last.year, 1, 1)}.get(span, X["날짜"].min())
    A = X[X["품목"] == item]
    Y = A[A["날짜"] >= d0].copy()
    if Y.empty:
        st.info("이 기간에 출하 기록이 없습니다."); return
    Y["박스_정산"] = Y["박스"].where(Y["박스단가"].notna())
    Y["kg_정산"] = Y["kg"].where(Y["박스단가"].notna())

    def agg(df, keys):
        g = df.groupby(keys).agg(박스=("박스", "sum"), kg=("kg", "sum"), 금액=("금액", "sum"), 박스_정산=("박스_정산", "sum"), kg_정산=("kg_정산", "sum")).reset_index()
        g["박스단가"] = (g["금액"] / g["박스_정산"].where(g["박스_정산"] > 0)).round(0)
        g["kg단가"] = (g["금액"] / g["kg_정산"].where(g["kg_정산"] > 0)).round(0)
        return g
    G = agg(Y, ["날짜", "시장"])
    T = agg(Y, ["날짜"]).assign(시장="전체 평균")
    # 지표: 마지막 출하일, 최근 7일 vs 그 앞 7일
    w1 = Y[Y["날짜"] > last - pd.Timedelta(days=7)]
    w0 = A[(A["날짜"] <= last - pd.Timedelta(days=7)) & (A["날짜"] > last - pd.Timedelta(days=14))]
    p1 = w1["금액"].sum() / w1["박스"].where(w1["박스단가"].notna()).sum() if w1["박스단가"].notna().any() else np.nan
    p0 = w0["금액"].sum() / w0["박스"].where(w0["박스단가"].notna()).sum() if w0["박스단가"].notna().any() else np.nan
    k = st.columns(4)
    k[0].metric("마지막 출하일", f"{last:%m/%d}"); k[0].caption(f"그날 {X[X['날짜'] == last]['박스'].sum():,.0f}박스 (전 품목)")
    k[1].metric("최근 7일 출하", f"{w1['박스'].sum():,.0f}박스", _delta(_pct(w1["박스"].sum(), w0["박스"].sum())))
    k[2].metric("최근 7일 평균 박스단가", f"{p1:,.0f}원" if pd.notna(p1) else "—", _delta(_pct(p1, p0)))
    unpaid = Y["박스단가"].isna()
    k[3].metric("단가 미정", f"{int(unpaid.sum())}건"); k[3].caption(f"{Y.loc[unpaid, '박스'].sum():,.0f}박스 — 경락가 입력 전" if unpaid.any() else "모두 입력됨")
    c1, c2 = st.columns(2)
    P = pd.concat([G, T], ignore_index=True).dropna(subset=["박스단가"])
    mk = list(G.groupby("시장")["박스"].sum().sort_values(ascending=False).index)
    dom = mk + ["전체 평균"]
    pal = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]   # category10
    rng = (pal * 2)[:len(mk)] + ["#111111"]
    c1.markdown("**일별 박스 단가 (원/박스, 경락가 가중평균)**")
    c1.altair_chart(alt.Chart(P).mark_line(point=alt.OverlayMarkDef(size=18)).encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d")), y=alt.Y("박스단가:Q", title="원/박스", scale=alt.Scale(zero=False)),
        color=alt.Color("시장:N", scale=alt.Scale(domain=dom, range=rng), legend=alt.Legend(orient="top", title=None, columns=5)),
        strokeWidth=alt.condition(alt.datum.시장 == "전체 평균", alt.value(3), alt.value(1.4)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "시장:N", alt.Tooltip("박스단가:Q", format=",.0f"), alt.Tooltip("kg단가:Q", format=",.0f", title="kg당 단가"),
                 alt.Tooltip("박스:Q", format=",.0f")]).properties(height=320), **_FULL)
    c2.markdown("**일별 시장 출하 (박스)**")
    c2.altair_chart(alt.Chart(G).mark_bar().encode(
        x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d")), y=alt.Y("박스:Q", title="박스"),
        color=alt.Color("시장:N", scale=alt.Scale(domain=mk, range=rng[:len(mk)]), legend=alt.Legend(orient="top", title=None, columns=5)),
        tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "시장:N", alt.Tooltip("박스:Q", format=",.0f"), alt.Tooltip("kg:Q", format=",.0f"),
                 alt.Tooltip("박스단가:Q", format=",.0f")]).properties(height=320), **_FULL)
    # 시장별 기간 합계
    S_ = agg(Y, ["시장"])
    S_["출하일수"] = Y.groupby("시장")["날짜"].nunique().reindex(S_["시장"]).values
    S_["박스 비중%"] = (100 * S_["박스"] / S_["박스"].sum()).round(1)
    S_["단가 미정 박스"] = Y[unpaid].groupby("시장")["박스"].sum().reindex(S_["시장"]).fillna(0).values
    S_ = S_.sort_values("박스", ascending=False)
    st.markdown(f"**시장별 합계** ({Y['날짜'].min():%Y-%m-%d} ~ {last:%Y-%m-%d})")
    st.dataframe(S_[["시장", "출하일수", "박스", "박스 비중%", "kg", "금액", "박스단가", "kg단가", "단가 미정 박스"]], hide_index=True, **_FULL,
                 column_config={c: st.column_config.NumberColumn(format="localized") for c in ("박스", "kg", "금액", "박스단가", "kg단가", "단가 미정 박스")})
    # 최근 14 출하일 × 시장 박스단가
    days = sorted(Y["날짜"].unique())[-14:][::-1]
    Q = P[P["날짜"].isin(days)].pivot_table(index="시장", columns="날짜", values="박스단가", aggfunc="first").reindex(dom).dropna(how="all")
    Q = Q[[d for d in days if d in Q.columns]]
    Q.columns = [pd.Timestamp(c).strftime("%m/%d") for c in Q.columns]
    st.markdown("**최근 출하일 시장별 박스 단가 (원)**")
    st.dataframe(Q, **_FULL, column_config={c: st.column_config.NumberColumn(format="localized") for c in Q.columns})
    with st.expander("월별 평균 (포장실 장부 기준, 이 품목 전체 기간)"):
        A2 = A.copy(); A2["박스_정산"] = A2["박스"].where(A2["박스단가"].notna())
        A2["월"] = A2["날짜"].dt.strftime("%Y-%m")
        Mo = A2.groupby(["월", "시장"]).agg(금액=("금액", "sum"), 박스_정산=("박스_정산", "sum"), 박스=("박스", "sum")).reset_index()
        Mo["박스단가"] = (Mo["금액"] / Mo["박스_정산"].where(Mo["박스_정산"] > 0)).round(0)
        tot = A2.groupby("월").agg(금액=("금액", "sum"), 박스_정산=("박스_정산", "sum"), 박스=("박스", "sum")).reset_index().assign(시장="전체 평균")
        tot["박스단가"] = (tot["금액"] / tot["박스_정산"].where(tot["박스_정산"] > 0)).round(0)
        Mo = pd.concat([Mo, tot], ignore_index=True)
        W = Mo.pivot_table(index="시장", columns="월", values="박스단가", aggfunc="first")
        W = W[sorted(W.columns, reverse=True)].reindex([m for m in dom if m in W.index] + [m for m in W.index if m not in dom])
        st.dataframe(W, **_FULL, column_config={c: st.column_config.NumberColumn(format="localized") for c in W.columns})
        B = Mo.pivot_table(index="시장", columns="월", values="박스", aggfunc="sum")
        B = B[sorted(B.columns, reverse=True)].reindex(W.index)
        st.caption("아래: 월 출하 박스 수")
        st.dataframe(B, **_FULL, column_config={c: st.column_config.NumberColumn(format="localized") for c in B.columns})
    st.caption("자료: 포장실 'YYYY년 M월 팽이 생산 상세일지' · 'YYYY년 M월 새송이 상세일지'의 '입출고내역' 시트에서 거래처가 '시장'인 출고 행 (2025-01~). "
               "단가는 박스당 경락가(원) — 경락 결과가 들어오기 전 행은 '단가 미정'으로 두고 평균에서 뺍니다. 박스단가 = 금액 합 ÷ 단가가 있는 박스 수.")


def _market_monthly(D, crop):
    MP = D["MP"]
    if MP.empty:
        st.info("팽이시장단가·새송이시장단가 파일을 찾지 못했습니다."); return
    c2 = st.container()
    X = MP[MP["작물"] == crop]
    if crop == "새송이":
        items = [i for i in ["뿌리", "손질", "미니", "파지"] if i in set(X["품목"])]
        item = c2.radio("품목", items, horizontal=True, key="of_mitem")
        X = X[X["품목"] == item]
    X = X.dropna(subset=["평균단가"]).copy()
    if X.empty:
        st.info("자료가 없습니다."); return
    recent = X[X["연월"] >= X["연월"].max() - pd.DateOffset(months=5)]
    main = recent.groupby("거래처")["출고량kg"].sum().sort_values(ascending=False)
    pick = st.multiselect("거래처", sorted(X["거래처"].unique()), default=main.index[:8].tolist(), key=f"of_mk_{crop}")
    X = X[X["거래처"].isin(pick)]
    c1, c2 = st.columns(2)
    c1.markdown("**월 평균단가 (원/kg)**")
    c1.altair_chart(alt.Chart(_ym(X)).mark_line(point=True).encode(
        x=alt.X("월표시:O", title=None), y=alt.Y("평균단가:Q", title="원/kg", scale=alt.Scale(zero=False)),
        color=alt.Color("거래처:N", scale=CAT, legend=alt.Legend(orient="top", title=None, columns=4)),
        tooltip=["거래처:N", alt.Tooltip("월표시:O", title="월"), alt.Tooltip("평균단가:Q", format=",.0f"), alt.Tooltip("출고량kg:Q", format=",.0f")]).properties(height=300), **_FULL)
    c2.markdown("**월 출고량 (kg)**")
    c2.altair_chart(alt.Chart(_ym(X)).mark_bar().encode(
        x=alt.X("월표시:O", title=None), y=alt.Y("출고량kg:Q", title="출고량", axis=alt.Axis(labelExpr="format(datum.value / 1000, ',.0f') + '톤'")),
        color=alt.Color("거래처:N", scale=CAT, legend=alt.Legend(orient="top", title=None, columns=4)),
        tooltip=["거래처:N", alt.Tooltip("월표시:O", title="월"), alt.Tooltip("출고량kg:Q", format=",.0f")]).properties(height=300), **_FULL)
    # 최근 6개월 거래처별 단가 표 + 출고량 가중 평균
    R = X[X["연월"] >= X["연월"].max() - pd.DateOffset(months=5)]
    P = R.pivot_table(index="거래처", columns="연월", values="평균단가", aggfunc="first")
    P.columns = [c.strftime("%Y-%m") for c in P.columns]
    w = R.assign(w=R["평균단가"] * R["출고량kg"]).groupby("거래처").agg(w=("w", "sum"), kg=("출고량kg", "sum"))
    P["6개월 가중평균"] = (w["w"] / w["kg"]).round(0)
    P["6개월 출고량(kg)"] = w["kg"]
    st.dataframe(P.sort_values("6개월 출고량(kg)", ascending=False), **_FULL,
                 column_config={c: st.column_config.NumberColumn(format="localized") for c in P.columns})
    st.caption("자료: 최근 'M월 팽이시장단가.xlsx'·'M월 새송이시장단가.xlsx'의 '단가&출고량' 시트(그 앞 기간은 '누적' 시트). 평균단가 = 원/kg.")
    DY = D["DY"]
    if len(DY) and not _err(DY):
        if True:                                    # (월별 정리표 펼침 상자 안이라 상자를 겹치지 않는다)
            st.markdown("**대양청과 박스 단가 비교 (일별, 경리 정리)**")
            Z = DY[DY["작물"] == crop]
            if len(Z):
                st.altair_chart(alt.Chart(Z).mark_line(point=True).encode(
                    x=alt.X("날짜:T", title=None, axis=alt.Axis(format="%m/%d")), y=alt.Y("박스단가:Q", title="1박스 단가(원)", scale=alt.Scale(zero=False)),
                    color=alt.Color("시장:N", scale=CAT, legend=alt.Legend(orient="top", title=None)),
                    tooltip=[alt.Tooltip("날짜:T", format="%Y-%m-%d"), "시장:N", alt.Tooltip("박스단가:Q", format=",.0f")]).properties(height=260), **_FULL)
                st.caption("자료: 월별 매출,물량.xlsx '대양청과 단가차액' 시트 (대양청과 경락가와 다른 시장 박스 단가).")
            else:
                st.caption("이 작물의 비교 자료가 없습니다.")


# ───────────────────────── 출하 대사 (v1.25.2) ─────────────────────────
GRADE_ICON = {"확인": "🔴", "참고": "🟡", "대기": "⏳", "정상": "✅", "처리": "✔️"}


# ───────────────────────── 직접 확인·수정 기록 (v1.26.11) ─────────────────────────
def _manual_path():
    return os.path.join(O.C.CACHE_DIR, "recon_manual.json")


def _manual_load():
    p = _manual_path()
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _manual_save(d):
    p = _manual_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _mkey(crop, mk, d):
    return f"{crop}|{mk}|{pd.Timestamp(d):%Y-%m-%d}"


def _apply_manual(U):
    """사람이 적어 둔 확인·수정값을 한 줄 표에 덮어쓴다. 등급 '처리'(✔️) = 사람이 확인함."""
    M = _manual_load()
    if not M or U is None or U.empty:
        return U
    U = U.copy()
    U["확인자"] = ""
    for i, r in U.iterrows():
        m = M.get(_mkey(r["작물"], r["시장"], r["날짜"]))
        if not m:
            continue
        for c in ("경매금액", "지급액", "입금액"):
            if m.get(c) not in (None, "", 0):
                U.at[i, c] = float(m[c])
        if m.get("입금액") not in (None, "", 0):
            U.at[i, "입금상태"] = "입금 확인(직접)"
        if m.get("경매금액") not in (None, "", 0):
            U.at[i, "팩스상태"] = "직접 입력"
        who = m.get("who", "")
        when = str(m.get("when", ""))[:10]
        U.at[i, "확인자"] = f"{who} {when}".strip()
        note = m.get("memo", "")
        if m.get("status") == "확인함":
            U.at[i, "등급"] = "처리"
            U.at[i, "문제"] = ("확인함: " + note) if note else "확인함"
        elif m.get("status") == "보류":
            U.at[i, "등급"] = "대기"
            U.at[i, "문제"] = ("보류: " + note) if note else "보류"
        elif note:
            U.at[i, "문제"] = (str(U.at[i, "문제"]) + " · 메모: " + note) if U.at[i, "문제"] else "메모: " + note
    return U


def _manual_form(crop, mk, d0, row):
    """고른 거래 아래 — 확인함/보류/수정값·메모 저장"""
    key = _mkey(crop, mk, d0)
    M = _manual_load()
    cur = M.get(key, {})
    with st.expander("✏️ 직접 확인·수정 (팩스 종이로 확인했거나 통장에서 입금을 찾았을 때)", expanded=bool(cur)):
        if cur:
            st.caption(f"저장됨: {cur.get('status', '')} · {cur.get('who', '')} {str(cur.get('when', ''))[:16]}")
        with st.form(f"mf_{key}", clear_on_submit=False):
            c1, c2 = st.columns([1, 2])
            status = c1.radio("처리", ["확인함", "보류", "값만 수정"], index=["확인함", "보류", "값만 수정"].index(cur.get("status", "확인함")) if cur.get("status") in ("확인함", "보류", "값만 수정") else 0,
                              help="확인함 = 문제 없음으로 보고 목록에서 ✔️로 내림 · 보류 = 나중에 다시 볼 것(⏳) · 값만 수정 = 등급은 그대로 두고 금액만 채움")
            memo = c2.text_area("메모 (예: 9/5 팩스 종이 확인, 59박스 640,500 · 입금 9/20 통장에서 확인)", value=cur.get("memo", ""), height=80)
            st.caption("아래는 비워 두면 그대로예요. 팩스가 종이로만 있으면 경매 금액·지급액을, 통장에서 찾았으면 입금액·입금일을 적어 주세요.")
            n1, n2, n3, n4 = st.columns(4)
            auc = n1.number_input("경매 금액(원)", min_value=0, value=int(cur.get("경매금액") or 0), step=1000)
            pay = n2.number_input("정산 지급액(원)", min_value=0, value=int(cur.get("지급액") or 0), step=1000)
            dep = n3.number_input("입금액(원)", min_value=0, value=int(cur.get("입금액") or 0), step=1000)
            dday = n4.text_input("입금일 (예 09/20)", value=cur.get("입금일", ""))
            b1, b2, _ = st.columns([1, 1, 4])
            ok = b1.form_submit_button("저장", type="primary")
            rm = b2.form_submit_button("지우기")
        if ok:
            M[key] = {"status": status, "memo": memo.strip(), "경매금액": auc or None, "지급액": pay or None, "입금액": dep or None, "입금일": dday.strip(),
                      "who": st.session_state.get("_office_me", ""), "when": pd.Timestamp.now().isoformat(timespec="minutes"),
                      "작물": crop, "시장": mk, "출하일": f"{pd.Timestamp(d0):%Y-%m-%d}"}
            _manual_save(M); st.success("저장했습니다."); st.rerun()
        if rm and key in M:
            del M[key]; _manual_save(M); st.success("지웠습니다."); st.rerun()


def _tab_recon(D):
    """출하 대사 (v1.26.5 새 구성): ① 이 달 한눈에 ② 확인할 거래 목록(고르면 세부) ③ 시장별 합계 ④ 월별 추이·팩스 읽기(펼침)"""
    st.markdown("**시장 출하 대사** — 출하일마다 포장실 일지 출고 → 시장 경매(팩스) → 정산·입금 → 경리나라 명세표가 맞는지 한 줄로")
    FX = D.get("FX", pd.DataFrame())
    J = O.reconcile(D.get("MD", pd.DataFrame()), D["S"], FX=FX if len(FX) else None)
    if J.empty:
        st.info("포장실 입출고 장부 또는 매출 거래명세표가 없어 대조할 수 없습니다."); return
    TW = D.get("TW") or {}
    U = D.get("UN", pd.DataFrame())
    if U is None or U.empty:
        U = J.assign(등급="정상", 문제="", 팩스상태="", 입금상태="")
    U = _apply_manual(U)
    U = U.copy(); U["월"] = U["날짜"].dt.strftime("%Y-%m")
    months = sorted(U["월"].unique(), reverse=True)
    c1, c2, c3 = st.columns([1.3, 1, 1.7])
    pick = c1.selectbox("달", months, index=0, key="rc_month")
    crop = c2.radio("작물", ["전체", "팽이", "새송이"], horizontal=True, key="rc_crop")
    X = U[U["월"] == pick]
    if crop != "전체":
        X = X[X["작물"] == crop]
    mk_opts = ["전체"] + sorted(X["시장"].unique())
    mk = c3.selectbox("시장", mk_opts, index=0, key="rc_mk")
    if mk != "전체":
        X = X[X["시장"] == mk]
    if X.empty:
        st.caption("자료가 없습니다."); return

    # ① 이 달 한눈에
    n = len(X)
    cnt = X["등급"].value_counts()
    k = st.columns(5)
    k[0].metric("출하 (시장·날짜)", f"{n}건"); k[0].caption(f"{X['출고박스'].sum():,.0f}박스 · 장부 {_eok(X['장부금액'].sum())}")
    k[1].metric("✅ 정상", f"{cnt.get('정상', 0)}건"); k[1].caption(f"✔️ 직접 확인 {cnt.get('처리', 0)}건" if cnt.get("처리", 0) else "")
    k[2].metric("🔴 확인 필요", f"{cnt.get('확인', 0)}건"); k[2].caption("팩스 없음 · 박스 안 맞음 · 금액 다름 · 입금 못 찾음")
    k[3].metric("🟡 참고", f"{cnt.get('참고', 0)}건"); k[3].caption("재정산 · 단위 차이(돈은 맞음)")
    k[4].metric("⏳ 대기", f"{cnt.get('대기', 0)}건"); k[4].caption("명세표·단가·정산서·입금이 아직 안 들어온 것")
    tot = st.columns(4)
    tot[0].metric("장부 금액", _eok(X["장부금액"].sum()))
    tot[1].metric("경매(팩스) 금액", _eok(X["경매금액"].sum()) if X["경매금액"].notna().any() else "—")
    tot[2].metric("정산 지급액 / 입금", (f"{_eok(X['지급액'].sum())} / {_eok(X['입금액'].sum())}") if X["지급액"].notna().any() else "—")
    pre = int((X["입금상태"].astype(str).str.startswith("팩스 전")).sum())
    est = int((X["입금상태"] == "입금 확인(추정)").sum())
    tot[2].caption((f"팩스 전 출하 {pre}건은 건별 대조 불가" if pre else "") + (" · " if pre and est else "") + (f"추정 확인 {est}건(정산서 없음, 경락액×수수료율)" if est else ""))
    tot[3].metric("명세표 금액", _eok(X["명세표"].sum()))

    # ② 확인할 거래 목록
    show = st.radio("목록", ["🔴 확인 필요만", "🔴🟡 확인+참고", "⏳ 대기 포함", "전부"], horizontal=True, key="rc_show", label_visibility="collapsed")
    sel = {"🔴 확인 필요만": ["확인"], "🔴🟡 확인+참고": ["확인", "참고"], "⏳ 대기 포함": ["확인", "참고", "대기"], "전부": ["확인", "참고", "대기", "처리", "정상"]}[show]
    Z = X[X["등급"].isin(sel)].sort_values(["등급", "날짜", "작물", "시장"], key=lambda c: c.map({"확인": 0, "참고": 1, "대기": 2, "처리": 3, "정상": 4}) if c.name == "등급" else c)
    if Z.empty:
        st.success("해당하는 거래가 없습니다 — 이 달 출고는 경매·정산·입금·명세표까지 모두 맞습니다." if show == "🔴 확인 필요만" else "해당하는 거래가 없습니다.")
    else:
        T = pd.DataFrame({"": Z["등급"].map(GRADE_ICON), "출하일": Z["날짜"].dt.strftime("%m/%d"), "작물": Z["작물"], "시장": Z["시장"], "문제": Z["문제"],
                          "출고 박스": Z["출고박스"], "장부 금액": Z["장부금액"], "경매 금액": Z["경매금액"], "경매일": Z["경매일"],
                          "지급액": Z["지급액"], "입금": Z["입금상태"], "명세표 금액": Z["명세표"]})
        numc = ["출고 박스", "장부 금액", "경매 금액", "지급액", "명세표 금액"]
        # 숫자가 없는 칸은 'None' 대신 빈칸(글자로 바꿔서) — 지급액이 비면 '정산서가 없어 모름'이지 '입금 못 찾음'이 아니다
        for c in numc:
            T[c] = pd.to_numeric(T[c], errors="coerce").map(lambda v: "" if pd.isna(v) else f"{v:,.0f}")
        T.loc[T["경매 금액"] == "0", "경매 금액"] = ""          # 팩스가 없으면 경매 금액도 모르는 것 — 0이 아니라 빈칸
        st.dataframe(T, hide_index=True, **_FULL, height=min(60 + 35 * len(T), 480),
                     column_config={**{c: st.column_config.TextColumn(width="small") for c in numc}, "": st.column_config.TextColumn(width="small"),
                                    "문제": st.column_config.TextColumn(width="large"), "입금": st.column_config.TextColumn(width="medium"),
                                    "경매일": st.column_config.TextColumn(width="small")})
        _detail(D, J, Z, pick, crop)

    # ③ 시장별 합계
    with st.expander(f"{pick} 시장별 합계 (출고 → 경매 → 정산·입금 → 명세표)"):
        G = X.groupby(["작물", "시장"]).agg(출하일=("날짜", "size"), 출고박스=("출고박스", "sum"), 장부금액=("장부금액", "sum"), 경매확인박스=("확인박스", "sum"),
                                         경매금액=("경매금액", "sum"), 지급액=("지급액", "sum"), 입금=("입금액", "sum"), 명세표=("명세표", "sum"),
                                         확인필요=("등급", lambda s: int((s == "확인").sum())), 참고=("등급", lambda s: int((s == "참고").sum()))).reset_index()
        G["경매−장부"] = G["경매금액"] - G["장부금액"]
        G["명세표−장부"] = G["명세표"] - G["장부금액"]
        st.dataframe(G.sort_values("장부금액", ascending=False), hide_index=True, **_FULL, column_config=NUMFMT([c for c in G.columns if c not in ("작물", "시장")]),
                     height=min(60 + 35 * len(G), 600))
        st.caption("날짜별로는 하루 출하분 일부가 그다음 경매로 넘어가기도 해서 어긋나 보일 수 있지만, 박스·단가 단위로 맞춘 것이라 달 합계로 보면 됩니다.")

    # ④ 월별 추이
    with st.expander("월별 추이 (장부 ↔ 명세표 ↔ 팩스)"):
        M = U.groupby("월").agg(출고박스=("출고박스", "sum"), 장부금액=("장부금액", "sum"), 명세표=("명세표", "sum"), 경매금액=("경매금액", "sum"),
                              정상=("등급", lambda s: int((s == "정상").sum())), 확인=("등급", lambda s: int((s == "확인").sum())),
                              참고=("등급", lambda s: int((s == "참고").sum())), 대기=("등급", lambda s: int((s == "대기").sum()))).reset_index()
        M["명세표−장부"] = M["명세표"] - M["장부금액"]
        M["차이%"] = (100 * M["명세표−장부"] / M["장부금액"].where(M["장부금액"] > 0)).round(2)
        st.dataframe(M.iloc[::-1], hide_index=True, **_FULL, column_config=NUMFMT(["출고박스", "장부금액", "명세표", "경매금액", "명세표−장부"]), height=min(60 + 35 * len(M), 450))
    _fax_panel(D, pick)
    with st.expander("읽는 법"):
        st.markdown("""
- **한 줄 = 한 시장의 하루 출하.** 그 출하가 ① 다음 날 경매 팩스(박스·단가) ② 정산서 지급액 → 통장 입금 ③ 경리나라 거래명세표까지 맞는지 한꺼번에 봅니다.
- 🔴 **확인 필요**: 경매 팩스가 스캔 폴더에 없음 · 경매에서 박스가 확인 안 됨 · 명세표 금액이 다름 · 정산 지급액의 입금을 못 찾음 — 사람이 봐야 하는 것.
- 🟡 **참고**: 재정산(장부 비고에 적힘, 팩스는 첫 경락가) · 시장이 kg 단가나 작은 상자로 적어 박스 수만 다른 것(돈은 명세표와 같음).
- ⏳ **대기**: 명세표가 아직 안 온 달 · 장부에 경락가가 아직 없는 출하 · 입금 기한 전 · 정산서 없이 판매원표만 온 날 — 기다리면(또는 정산서를 스캔하면) 채워집니다.
- **지급액** 칸이 비어 있으면 '입금을 못 찾았다'가 아니라 '그날 정산서(팩스)가 없어 지급액을 모른다'는 뜻입니다. 입금을 못 찾은 건은 입금 열에 '입금 못 찾음'이라고 따로 적힙니다.
- **입금** 열: 입금 확인 = 정산서 지급액과 원 단위 일치 · 입금 확인(추정) = 정산서는 없지만 경락액×그 시장 평소 수수료율과 ±1.2% 안에서 맞는 입금 있음 · 입금 확인(추정·장부) = 경매 팩스가 아예 없어 출고장 금액(=명세표)×수수료율로 맞춘 것(8/12 이전 팩스 전 기간도 이렇게) · 팩스 전 = 그래도 못 맞춘 8/13 이전 출하.
- 경매는 보통 출하 다음 날이고, 하루 출하분 일부가 그다음 경매로 넘어가면 '경매일'에 두 날짜가 적힙니다(박스·단가는 똑같이 맞춘 것).
  포장실 일지 비고에 '9/5 정산'처럼 적힌 줄은 그날 경매로 넘긴 것 — 그 날짜 팩스와 맞추고, 그날 팩스가 없으면 "N박스는 M/D 경매로 넘김 — 그날 팩스 없음"으로 알려줍니다.
- 거래를 고르면 아래 세부내역에 일지 줄·팩스 줄·팩스 문서·정산·입금·명세표가 나오고, 맨 아래 **✏️ 직접 확인·수정**에서 확인함(✔️)/보류/금액 입력·메모를 저장할 수 있습니다(app_cache\\recon_manual.json, 누가 언제 적었는지 같이).
- 팩스가 빠진 날은 그 팩스를 스캔 폴더에 'MMDD 팽서부.pdf'처럼 이름 붙여 넣으면 다음 갱신(09:00·15:00) 때 앱이 읽어 저절로 맞춥니다. 종이로만 확인했으면 위 폼에 금액을 적어 두세요.
""")


NUMFMT = lambda cols: {c: st.column_config.NumberColumn(format="localized") for c in cols}


def _detail(D, X, Z, pick, crop):
    """고른 거래의 세부 — 일지 출고 줄 · 경매(팩스) 줄 · 팩스 문서 · 정산·입금 · 거래명세표 (v1.26.3, v1.26.5 정리)"""
    TW = D.get("TW") or {}
    STL = D.get("ST", pd.DataFrame())
    labels, opts = [], {}
    for r in Z.itertuples():
        lab = f"{GRADE_ICON.get(r.등급, '')} {r.날짜:%m/%d} {r.작물} {r.시장} — {r.문제 or '정상'}"
        labels.append(lab); opts[lab] = (r.작물, r.시장, r.날짜)
    if not labels:
        return
    with st.container(border=True):
        lab = st.selectbox("세부내역 볼 거래", labels, key="rc_detail")
        crop1, mk, d0 = opts[lab]
        d0 = pd.Timestamp(d0)
        win = lambda c: (c > d0) & (c <= d0 + pd.Timedelta(days=5))
        st.markdown(f"**{mk} {crop1} · {d0:%Y-%m-%d} 출하**")
        if "lots" in TW:
            G = TW["lots"]
            G = G[(G["작물"] == crop1) & (G["시장"] == mk) & (G["날짜"] == d0)].copy()
            if len(G):
                st.caption("① 포장실 일지 출고 — 줄마다 어느 경매에서 팔렸고 팩스로 확인됐는지")
                def _line_state(r):
                    ok = r["경매박스"] + r["이월박스"] + r["합계확인박스"]
                    plan = f"{r['예정경매일']:%m/%d}" if pd.notna(r.get("예정경매일")) else ""
                    if r["미확인박스"] < 0.5:
                        how = "합계로" if r["합계확인박스"] > 0 else "단가·박스"
                        return f"{r['경매일']} 팩스와 {how} 일치"
                    if plan and r.get("넘긴경매팩스없음"):
                        return f"{plan} 경매분(장부 비고) — 그날 팩스 없음"
                    if ok > 0:
                        return f"{ok:,.0f}박스만 {r['경매일']} 팩스와 일치, {r['미확인박스']:,.0f}박스 못 찾음"
                    return "팩스에서 못 찾음" + (f" (비고 {plan} 경매)" if plan else "")
                T1 = pd.DataFrame({"품목": G["품목"], "박스": G["박스"], "박스단가": G["박스단가"], "금액": G["금액"],
                                   "경매일": [f"{d:%m/%d}" if pd.notna(d) else (e or "") for d, e in zip(G.get("예정경매일", pd.Series([pd.NaT] * len(G), index=G.index)), G["경매일"])],
                                   "팩스 확인 박스": G["경매박스"] + G["이월박스"] + G["합계확인박스"],
                                   "상태": [_line_state(r) for _, r in G.iterrows()], "비고": G["비고"]})
                st.dataframe(T1, hide_index=True, **_FULL, column_config={**NUMFMT(["박스", "박스단가", "금액", "팩스 확인 박스"]), "상태": st.column_config.TextColumn(width="large")})
        else:
            MD = D.get("MD", pd.DataFrame())
            G = MD[(MD["작물"] == crop1) & (MD["시장"] == mk) & (MD["날짜"] == d0)]
            if len(G):
                st.caption("① 포장실 일지 출고")
                st.dataframe(G[["품목", "박스", "박스단가", "금액", "비고"]], hide_index=True, **_FULL, column_config=NUMFMT(["박스", "박스단가", "금액"]))
        if "fax" in TW:
            F = TW["fax"]
            F = F[(F["작물"] == crop1) & (F["시장"] == mk) & win(F["판매일"])].copy()
            if len(F):
                F["판매일"] = F["판매일"].dt.strftime("%m/%d")
                st.caption("② 경매(팩스 판매원표·정산서 줄) — 짝: 장부 출고 줄과 맞춘 결과")
                st.dataframe(F[["판매일", "출하자", "단가", "박스", "확인박스", "금액", "짝", "종류", "파일"]], hide_index=True, **_FULL,
                             column_config=NUMFMT(["단가", "박스", "확인박스", "금액"]))
            else:
                st.warning("② 이 출하분의 경매 팩스가 스캔 폴더에 없습니다(그 뒤 5일 안).")
        FD = D.get("FXD", pd.DataFrame())
        if len(FD):
            FD = FD[(FD["시장"] == mk) & (FD["작물"] == crop1) & win(FD["날짜"])].copy()
            if len(FD):
                FD["날짜"] = FD["날짜"].dt.strftime("%m/%d")
                with st.expander(f"팩스 문서 {len(FD)}장 (합계·수수료·지급액·검산)"):
                    st.dataframe(FD[[c for c in ["날짜", "문서", "출하자", "수량", "금액", "수수료", "운임", "기타공제", "지급액", "검산", "비고", "파일"] if c in FD]],
                                 hide_index=True, **_FULL, column_config=NUMFMT(["수량", "금액", "수수료", "운임", "기타공제", "지급액"]))
        if len(STL):
            Q = STL[(STL["시장"] == mk) & (STL["작물"] == crop1) & win(STL["날짜"])].copy()
            if len(Q):
                Q["날짜"] = Q["날짜"].dt.strftime("%m/%d")
                Q["입금일"] = pd.to_datetime(Q["입금일"]).dt.strftime("%m/%d %H:%M").fillna("")
                st.caption("③ 정산 지급액 → 통장 입금")
                st.dataframe(Q[["날짜", "출하자", "판매대금", "수수료", "운임", "기타공제", "지급액", "입금액", "입금일", "입금대조"]].rename(columns={"날짜": "판매일"}),
                             hide_index=True, **_FULL, column_config=NUMFMT(["판매대금", "수수료", "운임", "기타공제", "지급액", "입금액"]))
        DM = D.get("DEPM", pd.DataFrame())
        if len(DM):
            DM = DM[(DM["시장"] == mk) & (DM["일시"] >= d0) & (DM["일시"] <= d0 + pd.Timedelta(days=10))]
            if len(DM):
                with st.expander(f"{mk} 입금 {len(DM)}건 (출하일부터 10일)"):
                    st.dataframe(DM.assign(일시=DM["일시"].dt.strftime("%m/%d %H:%M"))[["일시", "구분", "금액", "짝"]], hide_index=True, **_FULL,
                                 column_config=NUMFMT(["금액"]))
        Jr = X[(X["작물"] == crop1) & (X["시장"] == mk) & (X["날짜"] == d0)]
        if len(Jr):
            r = Jr.iloc[0]
            st.caption(f"④ 거래명세표(경리나라) {d0:%m/%d}: {r['명세표']:,.0f}원 · 장부 {r['장부금액']:,.0f}원 · 차이 {r['차이']:+,.0f}원 · {r['구분']}")
        _manual_form(crop1, mk, d0, None)


def _fax_panel(D, pick):
    """팩스 판매원표 읽기 상태 + 지금 읽기 버튼 + 이 달 문서 목록"""
    try:
        from . import etl_fax, ai_chat
    except Exception:
        return
    stt = D.get("FXS") or {}
    with st.expander(f"팩스 판매원표 — 읽은 {stt.get('done', 0)} / 대상 {stt.get('files', 0)}개" + (f" · 남은 {stt.get('left', 0)}개" if stt.get("left") else "")):
        auto_api = getattr(etl_fax.C, "FAX_AUTO_API", False)
        st.caption(f"폴더: `{stt.get('root') or '없음'}` · 대상: 'MMDD 팽서울.pdf'처럼 이름을 붙인 팩스와 팩스기 원본 이름(R25C-…) {getattr(etl_fax.C, 'FAX_SINCE', '')} 이후 · "
                   + (f"매일 09:00·15:00 갱신 때 새 파일만 최대 {getattr(etl_fax.C, 'FAX_MAX_PER_RUN', 60)}개씩 API 로 읽습니다." if auto_api
                      else "새 팩스는 Claude 예약 작업('팩스 직접 판독')이 매일 08:20·14:20에 API 없이 직접 읽어 넣습니다(검산 안 맞는 건 비고에 '검산 불일치'). 아래 버튼은 급할 때 API 로 읽는 용도.")
                   + f" 마지막으로 읽은 시각 {stt.get('last') or '—'}"
                   + (f" · 직접판독 {stt.get('direct', 0):,}개 · 앱(API) {stt.get('api', 0):,}개" if stt.get("direct") else ""))
        if stt.get("error"):
            st.warning(f"읽기 실패 {stt['error']}개 — 다음 갱신 때 다시 시도합니다(3번까지).")
        has_key = bool(ai_chat.api_key())
        busy = etl_fax.running()
        if busy:
            tot, done = busy.get("total") or 0, busy.get("done") or 0
            st.info(f"뒤에서 팩스를 읽는 중입니다 — {done} / {tot}개 (시작 {str(busy.get('start', ''))[11:16]}). "
                    "이 화면을 닫거나 다른 메뉴로 가도 계속 읽습니다. 다 읽으면 표에 저절로 들어갑니다(10분 안에 반영, 바로 보려면 '새로 보기').")
            if tot:
                st.progress(min(1.0, done / tot))
            if st.button("새로 보기", key="fx_refresh"):
                _load.clear(); st.rerun()
        c1, c2, c3 = st.columns([1.3, 1.1, 2])
        left = int(stt.get("left") or 0)
        if c1.button(f"남은 팩스 모두 읽기 ({left}개, 뒤에서)", key="fx_bg", type="primary", disabled=not has_key or bool(busy) or left == 0):
            ok = etl_fax.start_background(workers=4)
            st.success("시작했습니다. 4장씩 동시에 읽어 1,000장에 1시간쯤 걸립니다.") if ok else st.warning("이미 읽는 중입니다.")
            time.sleep(2); st.rerun()
        if c2.button("새 팩스 지금 읽기 (20개)", key="fx_run", disabled=not has_key or bool(busy)):
            with st.spinner("팩스 읽는 중… (20개에 1분쯤)"):
                n, left2 = etl_fax.run(limit=20, workers=4)
            _load.clear(); st.success(f"{n}개 읽음 · 남은 {left2}개"); st.rerun()
        if not has_key:
            c3.caption("AI 질문창용 API 키 파일이 없어 팩스를 읽을 수 없습니다.")
        elif left:
            c3.caption(f"AI 사용료 어림: 한 장 20~30원 → 남은 {left:,}개 약 {left * 25 / 10000:,.1f}만 원 (한 번 읽은 파일은 다시 읽지 않음)")
        Dc = D.get("FXD", pd.DataFrame())
        if len(Dc):
            Q = Dc[Dc["날짜"].dt.strftime("%Y-%m") == pick].sort_values(["날짜", "시장", "파일"])
            if len(Q):
                Q = Q.assign(날짜=Q["날짜"].dt.strftime("%m/%d"))
                qc = [c for c in ["날짜", "작물", "시장", "출하자", "문서", "줄수", "수량", "금액", "수수료", "운임", "기타공제", "지급액", "검산", "비고", "읽은곳", "파일"] if c in Q]
                st.dataframe(Q[qc], hide_index=True, **_FULL,
                             height=min(60 + 35 * len(Q), 420), column_config={c: st.column_config.NumberColumn(format="localized") for c in ("수량", "금액", "수수료", "운임", "지급액")})
                st.caption("검산 = 줄마다 수량×단가=금액, 줄 합 = 문서에 적힌 합계, 정산서는 판매대금 − 수수료 − 운임 − 하차비 = 지급액. "
                           "'확인 필요'는 AI 가 잘못 읽었거나 문서 자체가 맞지 않는 것 — 원본 팩스를 확인하세요. "
                           "읽은곳: 직접판독 = 2026-10 Claude 가 대화에서 직접 읽은 8/13~10/08 팩스(1,053개, 20%를 따로 한 번 더 읽어 대조), API = 앱이 자동으로 읽은 것(검산이 안 맞으면 한 번 더 읽음).")


# ───────────────────────── 매입·매출 (사업장별) ─────────────────────────
def _tab_inout(D):
    I = D["I"]
    if I.empty or _err(I):
        st.info("YYYY년 매입매출.xlsx 를 찾지 못했거나 읽지 못했습니다."); return
    I = I.copy()
    I["구분2"] = np.where(I["구분"].str.contains("매출"), "매출", np.where(I["구분"].str.contains("면세"), "매입(면세)", "매입(과세)"))
    P = I.pivot_table(index=["사업장", "연월"], columns="구분2", values="공급가액", aggfunc="sum").reset_index()
    for c in ("매출", "매입(과세)", "매입(면세)"):
        if c not in P:
            P[c] = np.nan
    P["매입 합계"] = P[["매입(과세)", "매입(면세)"]].sum(axis=1, min_count=1)
    P["매출−매입"] = P["매출"] - P["매입 합계"]
    P = P[P[["매출", "매입 합계"]].notna().any(axis=1)]
    ents = list(dict.fromkeys(P["사업장"]))
    ym0, ym1 = P["연월"].min(), P["연월"].max()
    st.markdown(f"**{ym0:%Y-%m} ~ {ym1:%Y-%m} 누적 (공급가액)**")
    cols = st.columns(len(ents) + 1)
    for col, e in zip(cols, ents):
        q = P[P["사업장"] == e]
        col.metric(f"{e} 매출", _eok(q["매출"].sum()))
        col.caption(f"매입 {_eok(q['매입 합계'].sum())} (과세 {_eok(q['매입(과세)'].sum())} · 면세 {_eok(q['매입(면세)'].sum())})")
    cols[-1].metric("합계 매출", _eok(P["매출"].sum())); cols[-1].caption(f"매입 {_eok(P['매입 합계'].sum())}")
    L = P.melt(id_vars=["사업장", "연월"], value_vars=["매출", "매입(과세)", "매입(면세)"], var_name="구분", value_name="공급가액").dropna()
    st.altair_chart(alt.Chart(_ym(L)).mark_bar().encode(
        x=alt.X("월표시:O", title=None), xOffset="구분:N", y=alt.Y("공급가액:Q", title="원", axis=EOK),
        color=alt.Color("구분:N", scale=alt.Scale(domain=["매출", "매입(과세)", "매입(면세)"], range=["#2a78d6", "#eb6834", "#eda100"]), legend=alt.Legend(orient="top", title=None)),
        column=alt.Column("사업장:N", title=None, sort=ents),
        tooltip=["사업장:N", alt.Tooltip("월표시:O", title="월"), "구분:N", alt.Tooltip("공급가액:Q", format=",.0f")]).properties(height=260, width=260))
    T = P.assign(연월=P["연월"].dt.strftime("%Y-%m"))[["사업장", "연월", "매출", "매입(과세)", "매입(면세)", "매입 합계", "매출−매입"]]
    st.dataframe(T, hide_index=True, **_FULL, height=330, column_config={c: st.column_config.NumberColumn(format="localized") for c in T.columns[2:]})
    st.caption("자료: YYYY년 매입매출.xlsx '매입매출' 시트 (세금계산서·계산서 공급가액 합계). 인건비·감가상각 등이 빠져 있어 '매출−매입'은 손익이 아니라 거래 흐름 확인용입니다. "
               "경리가 아직 채우지 않은 달은 나오지 않습니다.")


# ───────────────────────── 전기·수도 ─────────────────────────
def _tab_util(D):
    U = D["U"]
    if U.empty or _err(U):
        st.info("수도전기 사용내역 파일을 찾지 못했습니다."); return
    kind = st.radio("종류", ["전기", "수도"], horizontal=True, key="of_ukind")
    X = U[U["종류"] == kind].dropna(subset=["금액"])
    if X.empty:
        st.info("자료가 없습니다."); return
    unit = "kWh" if kind == "전기" else "㎥"
    last = X["고지월"].max()
    a = X[X["고지월"] == last]
    b = X[X["고지월"] == last - pd.DateOffset(years=1)]
    k = st.columns(3)
    k[0].metric(f"{last:%Y-%m} 고지 금액", f"{a['금액'].sum():,.0f}원", _delta(_pct(a["금액"].sum(), b["금액"].sum())) if len(b) else None, delta_color="inverse")
    k[1].metric(f"{last:%Y-%m} 사용량", f"{a['사용량'].sum():,.0f}{unit}", _delta(_pct(a["사용량"].sum(), b["사용량"].sum())) if len(b) else None, delta_color="inverse")
    yr = X[X["고지월"] > last - pd.DateOffset(months=12)]
    k[2].metric("최근 12개월 금액", f"{yr['금액'].sum():,.0f}원"); k[2].caption(f"{yr['고지월'].min():%Y-%m} ~ {last:%Y-%m} 고지 · 증감은 전년 같은 달 대비")
    st.altair_chart(alt.Chart(_ym(X, "고지월")).mark_bar().encode(
        x=alt.X("월표시:O", title="고지월"), y=alt.Y("금액:Q", title="원", axis=alt.Axis(labelExpr="format(datum.value / 1e4, ',.0f') + '만'")),
        color=alt.Color("장소:N", scale=alt.Scale(scheme="tableau20"), legend=alt.Legend(orient="right", title=None)),
        tooltip=["장소:N", alt.Tooltip("월표시:O", title="고지월"), alt.Tooltip("사용량:Q", format=",.0f", title=f"사용량({unit})"), alt.Tooltip("금액:Q", format=",.0f")]).properties(height=320), **_FULL)
    P = X.pivot_table(index="장소", columns="고지월", values="금액", aggfunc="sum")
    P = P[sorted(P.columns, reverse=True)]
    P.columns = [c.strftime("%Y-%m") for c in P.columns]
    st.dataframe(P, **_FULL, column_config={c: st.column_config.NumberColumn(format="localized") for c in P.columns})
    st.caption(f"자료: YYYY)수도전기 사용내역.xlsx '{kind}' 시트 (고지월 기준 — 사용은 그 전달). 장소별 금액(원), 표는 최근 달이 왼쪽.")


# ───────────────────────── 자료 위치 ─────────────────────────
def _tab_catalog(D, src):
    st.markdown("**이 화면이 읽는 파일**")
    rows = []
    for p in D["used"]:
        try:
            rows.append({"파일": os.path.relpath(p, src), "수정": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))})
        except Exception:
            rows.append({"파일": p, "수정": "—"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, **_FULL, height=min(60 + 35 * len(rows), 360))
    st.markdown("**경리 폴더 자료 정리** (SynologyDrive 안에서 운영·매출 관련 자료가 있는 곳)")
    C = pd.DataFrame(O.CATALOG, columns=["분류", "위치", "내용", "이 화면"])
    st.dataframe(C, hide_index=True, **_FULL, height=min(60 + 35 * len(C), 600))
    st.caption("아이디·비밀번호·계정정보·시리얼키 파일은 복사·표시하지 않고, 계좌 잔액·개인 입출금·급여·직원 개인정보 자료도 이 화면에 넣지 않았습니다. "
               "새 자료가 같은 이름 규칙으로 저장되면(예: '8월 팽이시장단가.xlsx', '10월 거래명세표_….xls') 자동으로 최신 파일을 읽습니다.")
