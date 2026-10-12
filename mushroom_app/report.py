# -*- coding: utf-8 -*-
"""월간 보고서 자동 생성: PDF(인쇄용, matplotlib) + 엑셀(표).
build_monthly(month, data) → {"pdf": bytes, "xlsx": bytes, "title": str}
data 는 app.py 가 캐시에서 모아 넘긴다: pm, sm, cm, cost, K(팽이 배치), B(새송이 배치), an(이상 항목), env, tank
"""
import io, os, sys, datetime as dt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import font_manager
from . import config as C, etl_logs

BLUE, ORANGE, GREEN, GOLD, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#d6a12a", "#6b7160"


def _font():
    """한글 폰트 설정: Windows 맑은 고딕 → Noto CJK → Nanum 순."""
    cands = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "Noto Sans CJK KR", "Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans KR", "AppleGothic"]
    names = {f.name for f in font_manager.fontManager.ttflist}
    for c in cands:
        if c in names:
            plt.rcParams["font.family"] = c
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 9


def _f(v, nd=1, unit=""):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.{nd}f}{unit}"


def _prev(month, n=1):
    y, m = map(int, month.split("-"))
    m -= n
    while m <= 0:
        m += 12; y -= 1
    return f"{y}-{m:02d}"


def _table(ax, df, col_w=None, font=8, header_color="#eef1ee", max_rows=28):
    ax.axis("off")
    if df is None or df.empty:
        ax.text(0.5, 0.5, "자료 없음", ha="center", va="center", color=GRAY)
        return
    df = df.head(max_rows)
    cells = [[("" if (isinstance(v, float) and np.isnan(v)) or v is None else (f"{v:,.1f}" if isinstance(v, float) else f"{v:,}" if isinstance(v, (int, np.integer)) else str(v))) for v in row] for row in df.values]
    t = ax.table(cellText=cells, colLabels=list(df.columns), loc="upper center", cellLoc="center", colWidths=col_w)
    t.auto_set_font_size(False); t.set_fontsize(font)
    # 행 높이: 평소엔 기본(1.25배)대로, 행이 많아 축을 넘치면 축 안에 맞춰 줄인다
    cs = list(t.get_celld().values())
    h = min(max(c.get_height() for c in cs) * 1.25, 1.0 / (len(cells) + 1))
    for c in cs:
        c.set_height(h)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor("#d7dbd3")
        if r == 0:
            cell.set_facecolor(header_color); cell.set_text_props(weight="bold")


def _kpi(ax, label, value, sub=""):
    ax.axis("off")
    ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor="#f5f7f4", edgecolor="#d7dbd3"))
    ax.text(0.06, 0.72, label, fontsize=8.5, color=GRAY, transform=ax.transAxes)
    ax.text(0.06, 0.36, value, fontsize=15, weight="bold", transform=ax.transAxes)
    ax.text(0.06, 0.12, sub, fontsize=7.5, color=GRAY, transform=ax.transAxes)


def _bar_trend(ax, df, xcol, ycol, title, color=BLUE, hl=None, fmt="{:,.0f}", ylabel=""):
    ax.set_title(title, fontsize=10, loc="left", weight="bold")
    if df is None or df.empty or ycol not in df:
        ax.text(0.5, 0.5, "자료 없음", ha="center", va="center", color=GRAY, transform=ax.transAxes); ax.axis("off"); return
    x = df[xcol].astype(str).tolist(); y = df[ycol].astype(float).tolist()
    cols = [ORANGE if hl and xv == hl else color for xv in x]
    ax.bar(x, y, color=cols, width=0.7)
    for i, v in enumerate(y):
        if not np.isnan(v):
            ax.text(i, v, fmt.format(v), ha="center", va="bottom", fontsize=6.5)
    ax.set_ylabel(ylabel, fontsize=8); ax.tick_params(axis="x", labelrotation=45, labelsize=7); ax.tick_params(axis="y", labelsize=7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def build_monthly(month, pm, sm, cm, cost, K, B, an, env, tank, cycles=None, tanks=None, stock=None):
    _font()
    prev, py = _prev(month), _prev(month, 12)
    title = f"{C.APP_NAME} 월간 보고서 — {month[:4]}년 {int(month[5:])}월"
    made = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    last12 = [_prev(month, i) for i in range(11, -1, -1)]
    pdf_buf = io.BytesIO()
    A4 = (8.27, 11.69)

    def row(df, col="월", m=month):
        if df is None or df.empty or col not in df:
            return {}
        r = df[df[col].astype(str) == m]
        return r.iloc[0].to_dict() if len(r) else {}

    pmr, pmp, pmy = row(pm), row(pm, m=prev), row(pm, m=py)
    smr, smp, smy = row(sm), row(sm, m=prev), row(sm, m=py)
    cost_p = cost[(cost["월"] == month) & (cost["작물"] == "팽이")] if cost is not None and not cost.empty else pd.DataFrame()
    cost_s = cost[(cost["월"] == month) & (cost["작물"] == "새송이")] if cost is not None and not cost.empty else pd.DataFrame()
    cpp = cost[(cost["월"] == prev) & (cost["작물"] == "팽이")] if cost is not None and not cost.empty else pd.DataFrame()
    cm_m = cm[cm.m == month] if cm is not None and not cm.empty else pd.DataFrame()
    cmv = dict(zip(cm_m["항목"], cm_m["비율%"])) if not cm_m.empty else {}
    an_m = an[pd.to_datetime(an["날짜"]).dt.strftime("%Y-%m") == month] if an is not None and not an.empty else pd.DataFrame()

    with PdfPages(pdf_buf) as pdf:
        # ── 1. 표지 + 요약
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, title, fontsize=17, weight="bold")
        fig.text(0.07, 0.925, f"작성 {made} · 자료: 유니팜 환경 로그, 작업일지, 배양소관리, 원재료입고·전기세·가스 (앱 v{C.VERSION})", fontsize=8, color=GRAY)
        gs = fig.add_gridspec(3, 4, left=0.06, right=0.94, top=0.90, bottom=0.66, hspace=0.35, wspace=0.12)
        def d(a, b, unit="", nd=1):
            return f"전월 {_f(b, nd, unit)} ({'+' if (a or 0) - (b or 0) >= 0 else ''}{_f((a or 0) - (b or 0), nd)})" if a is not None and b is not None and not (isinstance(a, float) and np.isnan(a)) and not (isinstance(b, float) and np.isnan(b)) else "전월 자료 없음"
        _kpi(fig.add_subplot(gs[0, 0]), "팽이 생산량", _f(pmr.get("생산kg"), 0, " kg"), d(pmr.get("생산kg"), pmp.get("생산kg"), " kg", 0))
        _kpi(fig.add_subplot(gs[0, 1]), "팽이 병당중량", _f(pmr.get("병당중량"), 1, " g"), d(pmr.get("병당중량"), pmp.get("병당중량"), " g"))
        qc = pmr.get("QC(서식B)") if pd.notna(pmr.get("QC(서식B)", np.nan)) else pmr.get("QC(서식A)")
        qcp = pmp.get("QC(서식B)") if pd.notna(pmp.get("QC(서식B)", np.nan)) else pmp.get("QC(서식A)")
        _kpi(fig.add_subplot(gs[0, 2]), "팽이 QC 평균", _f(qc, 1, " 점"), d(qc, qcp, " 점"))
        _kpi(fig.add_subplot(gs[0, 3]), "팽이 병당 원가", _f(cost_p["합계"].iloc[0] if len(cost_p) else np.nan, 1, " 원"), d(cost_p["합계"].iloc[0] if len(cost_p) else None, cpp["합계"].iloc[0] if len(cpp) else None, " 원"))
        _kpi(fig.add_subplot(gs[1, 0]), "새송이 생산량", _f(smr.get("생산kg"), 0, " kg"), d(smr.get("생산kg"), smp.get("생산kg"), " kg", 0))
        _kpi(fig.add_subplot(gs[1, 1]), "새송이 병당 수율(월)", _f(smr.get("병당g"), 0, " g"), d(smr.get("병당g"), smp.get("병당g"), " g", 0))
        _kpi(fig.add_subplot(gs[1, 2]), "새송이 미니 비중", _f(smr.get("미니%"), 1, " %"), d(smr.get("미니%"), smp.get("미니%"), " %"))
        _kpi(fig.add_subplot(gs[1, 3]), "새송이 병당 원가", _f(cost_s["합계"].iloc[0] if len(cost_s) else np.nan, 1, " 원"), "재료+연료+전기")
        _kpi(fig.add_subplot(gs[2, 0]), "팽이 균긁기 불량률", _f(cmv.get("균긁기불량(팽이)"), 2, " %"), "배양 중 오염 (기준 0.5%)")
        _kpi(fig.add_subplot(gs[2, 1]), "새송이 균긁기 불량률", _f(cmv.get("균긁기불량(새송이)"), 2, " %"), "기준 0.3%")
        _kpi(fig.add_subplot(gs[2, 2]), "권지 불량률", _f(cmv.get("권지불량(팽이)"), 2, " %"), "기준 0.5%")
        n_hi = int((an_m["심각도"] == "높음").sum()) if not an_m.empty else 0
        _kpi(fig.add_subplot(gs[2, 3]), "이상 항목", f"{len(an_m)} 건", f"높음 {n_hi}건")
        # 12개월 추이 4개
        gs2 = fig.add_gridspec(2, 2, left=0.08, right=0.95, top=0.60, bottom=0.06, hspace=0.75, wspace=0.3)
        pm12 = pm[pm["월"].isin(last12)] if pm is not None and not pm.empty else pd.DataFrame()
        sm12 = sm[sm["월"].isin(last12)] if sm is not None and not sm.empty else pd.DataFrame()
        _bar_trend(fig.add_subplot(gs2[0, 0]), pm12, "월", "생산kg", "팽이 월 생산량 (kg)", hl=month, fmt="{:,.0f}")
        _bar_trend(fig.add_subplot(gs2[0, 1]), pm12, "월", "병당중량", "팽이 병당중량 (g)", hl=month, fmt="{:.0f}", color=GREEN)
        _bar_trend(fig.add_subplot(gs2[1, 0]), sm12, "월", "생산kg", "새송이 월 생산량 (kg)", hl=month, fmt="{:,.0f}")
        _bar_trend(fig.add_subplot(gs2[1, 1]), sm12, "월", "병당g", "새송이 병당 수율 (g/병, 월 기준)", hl=month, fmt="{:.0f}", color=GREEN)
        fig.text(0.5, 0.02, "1", ha="center", fontsize=8, color=GRAY)
        png1 = io.BytesIO(); fig.savefig(png1, format="png", dpi=110)
        pdf.savefig(fig); plt.close(fig)

        # ── 2. 원가
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "2. 병당 원가 (재료 + 연료 + 전기)", fontsize=14, weight="bold")
        if cost is not None and not cost.empty:
            c12 = cost[cost["월"].isin(last12)].dropna(subset=["합계"])
            ax = fig.add_axes([0.08, 0.62, 0.86, 0.27])
            comp = ["원재료비", "연료비", "배양전기", "생육전기"]; colors = [BLUE, ORANGE, GOLD, GREEN]
            xs = sorted(c12["월"].unique()); w = 0.38
            for ci, crop in enumerate(("팽이", "새송이")):
                sub = c12[c12["작물"] == crop].set_index("월").reindex(xs)
                bottom = np.zeros(len(xs))
                for k, col in zip(comp, colors):
                    vals = sub[k].fillna(0).values
                    ax.bar(np.arange(len(xs)) + (ci - 0.5) * w, vals, w, bottom=bottom, color=col, alpha=1 if ci == 0 else 0.55, label=k if ci == 0 else None)
                    bottom += vals
                for i, v in enumerate(bottom):
                    if v > 0:
                        ax.text(i + (ci - 0.5) * w, v, f"{v:.0f}", ha="center", va="bottom", fontsize=6)
            ax.set_xticks(np.arange(len(xs))); ax.set_xticklabels(xs, rotation=45, fontsize=7); ax.tick_params(axis="y", labelsize=7)
            ax.set_ylabel("원/병", fontsize=8); ax.legend(fontsize=7, ncol=4, loc="lower center", bbox_to_anchor=(0.5, 1.0), frameon=False)
            ax.set_title("작물별 병당 원가 구성 (진한 색 팽이, 연한 색 새송이)", fontsize=10, loc="left", weight="bold")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            tb = cost[cost["월"].isin(last12)].sort_values(["월", "작물"], ascending=[False, True])[["월", "작물", "입병수", "원재료비", "연료비", "배양전기", "생육전기", "합계", "보고서_합계"]].rename(columns={"보고서_합계": "월말보고"})
            tb["입병수"] = tb["입병수"].astype("Int64")
            _table(fig.add_axes([0.06, 0.06, 0.88, 0.52]), tb, font=7.5, max_rows=26)
        else:
            fig.text(0.5, 0.5, "원가 자료 없음", ha="center", color=GRAY)
        fig.text(0.5, 0.02, "2", ha="center", fontsize=8, color=GRAY)
        pdf.savefig(fig); plt.close(fig)

        # ── 3. 배양실
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "3. 배양실 — 손실·불량률과 액탱크", fontsize=14, weight="bold")
        if cm is not None and not cm.empty:
            pv = cm[cm.m.isin(last12)].pivot(index="m", columns="항목", values="비율%")
            ax = fig.add_axes([0.08, 0.66, 0.86, 0.24])
            for col, color in zip(pv.columns, [BLUE, ORANGE, GREEN, GOLD, GRAY, "#9c5fd6"]):
                ax.plot(pv.index, pv[col], marker="o", ms=3, label=col, color=color)
            ax.tick_params(axis="x", labelrotation=45, labelsize=7); ax.tick_params(axis="y", labelsize=7); ax.set_ylabel("%", fontsize=8)
            ax.legend(fontsize=7, ncol=3, frameon=False); ax.set_title("월별 손실·불량률", fontsize=10, loc="left", weight="bold")
            for s in ("top", "right"):
                ax.spines[s].set_visible(False)
            _table(fig.add_axes([0.06, 0.36, 0.88, 0.27]), pv.round(2).sort_index(ascending=False).reset_index().rename(columns={"m": "월"}), font=7.5, max_rows=13)
        if tank is not None and not tank.empty:
            fig.text(0.07, 0.32, "팽이 액탱크별 균긁기 불량률 순위 (높은 순)", fontsize=10, weight="bold")
            tk = tank.reset_index().rename(columns={"index": "탱크"}); tk["탱크"] = tk["탱크"].astype(int); tk["배치"] = tk["배치"].astype(int); tk["단독배치"] = tk["단독배치"].fillna(0).astype(int)
            _table(fig.add_axes([0.06, 0.05, 0.88, 0.26]), tk, font=7.5)
        fig.text(0.5, 0.02, "3", ha="center", fontsize=8, color=GRAY)
        pdf.savefig(fig); plt.close(fig)

        # ── 4. 배치 결과 (이달 수확)
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "4. 이달 수확 배치", fontsize=14, weight="bold")
        if K is not None and not K.empty and "수확시작" in K:
            km = K[K["완결"] & (pd.to_datetime(K["수확시작"]).dt.strftime("%Y-%m") == month)].copy()
            fig.text(0.07, 0.925, f"팽이 {len(km)}개 배치 (권지일→생육실, 병당중량은 단일방 값 없으면 같은 날 합산 평균)", fontsize=8, color=GRAY)
            if len(km):
                km["생육실"] = km["생육실"].astype(int); km["발이실"] = km["발이실"].map(lambda v: int(v) if pd.notna(v) else "")
                t = km.sort_values("수확시작")[["bid", "입상일", "발이실", "권지일", "생육실", "수확시작", "총일수", "병당중량_표시", "중량기준", "QC", "긁기불량률%", "권지불량률%"]].copy()
                for c in ("입상일", "권지일", "수확시작"):
                    t[c] = pd.to_datetime(t[c]).dt.strftime("%m/%d")
                t = t.rename(columns={"bid": "배치", "병당중량_표시": "병당중량", "긁기불량률%": "긁기불량%", "권지불량률%": "권지불량%"}).round(2)
                _table(fig.add_axes([0.05, 0.50, 0.9, 0.41]), t, font=6.8, max_rows=30)
        if B is not None and not B.empty:
            bm = B[B["완결"] & (pd.to_datetime(B["입상일"]).dt.strftime("%Y-%m") == month)]
            fig.text(0.07, 0.46, f"새송이 이달 입상 배치 {len(bm)}개 — 뒤집기/솎기/수확 일차 평균: {_f(bm['뒤집기_d'].mean(),1)} / {_f(bm['솎기_d'].mean(),1)} / {_f(bm['수확시작_d'].mean(),1)}일, 재입상 간격 {_f(bm['재입상_d'].mean(),1)}일, 방당 {_f(bm['bottles'].mean(),0)}병", fontsize=8.5)
            if len(bm):
                g = bm.groupby("block").agg(배치=("bid", "count"), 입상병수=("bottles", "mean"), 수확시작일차=("수확시작_d", "mean"), 수확일수=("수확일수", "mean"), 재입상일차=("재입상_d", "mean")).round(1).reset_index().rename(columns={"block": "동"})
                _table(fig.add_axes([0.06, 0.30, 0.88, 0.14]), g, font=7.5)
        fig.text(0.5, 0.02, "4", ha="center", fontsize=8, color=GRAY)
        pdf.savefig(fig); plt.close(fig)

        # ── 5. 이상 항목 + 환경 요약
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "5. 이상 감시 · 환경 요약", fontsize=14, weight="bold")
        if not an_m.empty:
            cnt = an_m.groupby(["심각도", "분류"]).size().reset_index(name="건수").sort_values(["심각도", "건수"], ascending=[True, False])
            _table(fig.add_axes([0.06, 0.70, 0.40, 0.22]), cnt, font=7.5)
            top = an_m[an_m["심각도"].isin(["높음", "중간"])].sort_values("날짜", ascending=False)[["날짜", "분류", "방", "내용"]].head(16).copy()
            top["내용"] = top["내용"].astype(str).str.slice(0, 48)
            fig.text(0.07, 0.655, f"주요 항목 (높음·중간, 최근 16건 / 전체 {int(an_m['심각도'].isin(['높음','중간']).sum())}건)", fontsize=9, weight="bold")
            _table(fig.add_axes([0.05, 0.33, 0.90, 0.31]), top, col_w=[0.12, 0.16, 0.12, 0.60], font=6.8, max_rows=16)
        else:
            fig.text(0.07, 0.90, "이달 이상 항목 없음", fontsize=9, color=GRAY)
        if env is not None and not env.empty:
            e = env[(env.date.dt.strftime("%Y-%m") == month) & (env.run > 0.5)]
            if not e.empty:
                g = e.groupby("crop").agg(방수=("room", "nunique"), 평균온도=("temp", "mean"), 평균습도=("hum", "mean"), 평균CO2=("co2", "mean"), 냉방가동률=("cool", "mean"), 추종오차=("track_err", "mean")).round(2).reset_index().rename(columns={"crop": "작물"})
                g["냉방가동률"] = (g["냉방가동률"] * 100).round(0)
                fig.text(0.07, 0.24, "운전중 방의 이달 평균 환경", fontsize=10, weight="bold")
                _table(fig.add_axes([0.06, 0.10, 0.88, 0.12]), g, font=7.5)
        fig.text(0.5, 0.02, "5", ha="center", fontsize=8, color=GRAY)
        pdf.savefig(fig); plt.close(fig)


        # ── 6. 살균
        if cycles is not None and not cycles.empty:
            cy = cycles[pd.to_datetime(cycles["start"]).dt.strftime("%Y-%m") == month].copy()
            fig = plt.figure(figsize=A4)
            fig.text(0.07, 0.95, "6. 살균솥 — 이달 살균 사이클", fontsize=14, weight="bold")
            if len(cy):
                cy["구분"] = cy.get("구분", "배지")
                g = cy.groupby(["unit", "구분"]).agg(횟수=("start", "size"), 이차유지평균=("2차유지분", "mean"), 이차유지최소=("2차유지분", "min"), 최고온도평균=("최고온도", "mean"),
                                                   총가동평균=("총가동분", "mean"), F0평균=("F0", "mean"), F0최소=("F0", "min"), 중단=("중단횟수", "sum")).round(1).reset_index().rename(
                    columns={"unit": "솥", "이차유지평균": "2차유지 평균(분)", "이차유지최소": "2차유지 최소", "최고온도평균": "최고온도 평균", "총가동평균": "총가동 평균(분)"})
                fig.text(0.07, 0.925, f"사이클 {len(cy)}회 (배지 {(cy['구분']=='배지').sum()} · 액탱크 {(cy['구분']=='액탱크').sum()} · 이상 {(cy['구분']=='이상').sum()}) · 중단 {int(cy['중단횟수'].sum())}회", fontsize=8, color=GRAY)
                _table(fig.add_axes([0.05, 0.72, 0.90, 0.19]), g, font=7.2)
                ax = fig.add_axes([0.08, 0.42, 0.86, 0.24])
                for u, gg in cy[cy["구분"] == "배지"].groupby("unit"):
                    ax.plot(gg["start"], gg["F0"], marker="o", ms=3, lw=0.8, label=u)
                ax.set_title("배지 살균 F0 추이 (사이클별)", fontsize=10, loc="left", weight="bold"); ax.tick_params(labelsize=7); ax.legend(fontsize=7, frameon=False)
                for s_ in ("top", "right"):
                    ax.spines[s_].set_visible(False)
                flag = cy[(cy["구분"] == "이상") | (cy["중단횟수"] > 0) | ((cy["구분"] == "배지") & (cy["2차유지분"] < 55)) | (cy["최고온도"] < 116)]
                fig.text(0.07, 0.37, f"확인 필요 사이클 {len(flag)}건", fontsize=10, weight="bold")
                if len(flag):
                    t = flag.sort_values("start")[["unit", "구분", "start", "end", "1차유지분", "2차유지분", "최고온도", "총가동분", "F0", "중단횟수"]].copy()
                    t["start"] = pd.to_datetime(t["start"]).dt.strftime("%m/%d %H:%M"); t["end"] = pd.to_datetime(t["end"]).dt.strftime("%m/%d %H:%M")
                    _table(fig.add_axes([0.05, 0.06, 0.90, 0.30]), t.rename(columns={"unit": "솥", "start": "시작", "end": "냉각완료"}).round(1), font=7, max_rows=20)
            else:
                fig.text(0.07, 0.90, "이달 살균 기록 없음", fontsize=9, color=GRAY)
            fig.text(0.5, 0.02, "6", ha="center", fontsize=8, color=GRAY)
            pdf.savefig(fig); plt.close(fig)

        # ── 7. 종균·액탱크
        if tanks is not None and not tanks.empty:
            tk = tanks[pd.to_datetime(tanks["일시"]).dt.strftime("%Y-%m") == month].copy()
            fig = plt.figure(figsize=A4)
            fig.text(0.07, 0.95, "7. 종균·액탱크 — 이달 제조 탱크", fontsize=14, weight="bold")
            if len(tk):
                fig.text(0.07, 0.925, f"탱크 {len(tk)}개 (팽이 {(tk['품종']=='팽이').sum()} · 새송이 {(tk['품종']=='새송이').sum()}) · 탱크 pH 평균 {_f(tk['pH'].mean(), 2)} · 액배양 {_f(tk['액배양일'].mean(), 1)}일", fontsize=8, color=GRAY)
                g = tk.groupby(["품종", "유래"]).agg(배치=("코드", "size"), pH=("pH", "mean"), 액배양일=("액배양일", "mean"), 긁기불량률=("긁기불량률%", "mean"), 병당중량=("병당중량", "mean")).round(2).reset_index()
                g["유래"] = g["유래"].astype(str).str.slice(0, 26)
                _table(fig.add_axes([0.05, 0.62, 0.90, 0.29]), g.rename(columns={"긁기불량률": "긁기불량률%"}), font=7.2, max_rows=16)
                t = tk.sort_values("일시")[["일시", "품종", "탱크NO", "코드", "세대", "pH", "접종일", "액배양일", "살균F0", "긁기불량률%", "병당중량"]].copy()
                t["일시"] = pd.to_datetime(t["일시"]).dt.strftime("%m/%d"); t["접종일"] = pd.to_datetime(t["접종일"]).dt.strftime("%m/%d")
                _table(fig.add_axes([0.05, 0.06, 0.90, 0.52]), t.rename(columns={"일시": "제조", "탱크NO": "탱크", "살균F0": "F0"}).round(2), font=6.8, max_rows=32)
            else:
                fig.text(0.07, 0.90, "이달 탱크 기록 없음", fontsize=9, color=GRAY)
            fig.text(0.5, 0.02, "7", ha="center", fontsize=8, color=GRAY)
            pdf.savefig(fig); plt.close(fig)

        # ── 8. 재고 대사
        if stock:
            fig = plt.figure(figsize=A4)
            fig.text(0.07, 0.95, "8. 재고 대사 — 장부 재고와 월초 조정", fontsize=14, weight="bold")
            y = 0.90
            for crop, R in stock.items():
                if R is None or R.empty:
                    continue
                r12 = R[R["월"].isin(last12 + [month])].sort_values("월", ascending=False)[["월", "기초재고", "입고", "출고", "계산기말", "다음달기초", "월초조정", "조정률%", "기말재고일수"]].round(1)
                fig.text(0.07, y, f"{crop} (kg)", fontsize=10, weight="bold")
                _table(fig.add_axes([0.05, y - 0.40, 0.90, 0.38]), r12, font=7.2, max_rows=13)
                y -= 0.44
            fig.text(0.07, 0.06, "월초조정 = 다음 달 기초재고 − 이달 계산 기말(기초+입고−출고). 양(+)이면 장부보다 실재고가 많았던 것.", fontsize=7.5, color=GRAY)
            fig.text(0.5, 0.02, "8", ha="center", fontsize=8, color=GRAY)
            pdf.savefig(fig); plt.close(fig)

    # ── 엑셀
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        pd.DataFrame({"항목": ["보고서", "대상 월", "작성", "앱 버전"], "값": [title, month, made, C.VERSION]}).to_excel(xw, sheet_name="표지", index=False)
        for name, df in (("팽이 월별", pm), ("새송이 월별", sm), ("원가", cost), ("배양 손실률", cm), ("액탱크", tank.reset_index() if tank is not None and not tank.empty else None),
                         ("이상 항목", an_m), ("팽이 배치", K[K["완결"]] if K is not None and not K.empty else None), ("새송이 배치", B[B["완결"]] if B is not None and not B.empty else None),
                         ("살균 사이클", cycles), ("액탱크", tanks), ("재고 팽이", (stock or {}).get("팽이")), ("재고 새송이", (stock or {}).get("새송이"))):
            if df is None or df.empty:
                continue
            d2 = df.copy()
            for c in d2.columns:
                if str(d2[c].dtype).startswith("datetime"):
                    d2[c] = d2[c].dt.strftime("%Y-%m-%d")
            d2 = d2.drop(columns=[c for c in d2.columns if c.startswith("_")], errors="ignore")
            d2.to_excel(xw, sheet_name=name[:31], index=False)
    return {"pdf": pdf_buf.getvalue(), "xlsx": xbuf.getvalue(), "title": title, "png1": png1.getvalue()}

def build_weekly(week_end, pm, K, B, an, env, cycles, ph_daily=None, ss_daily=None):
    """주간 보고서(2쪽): week_end(일요일)까지 7일. 생산·병당중량·불량·살균·이상 요약 + 일별 표."""
    _font()
    end = pd.Timestamp(week_end).normalize(); start = end - pd.Timedelta(days=6)
    title = f"{C.APP_NAME} 주간 보고서 — {start:%Y-%m-%d} ~ {end:%m-%d}"
    made = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    pdf_buf = io.BytesIO(); A4 = (8.27, 11.69)
    pd_ = ph_daily if ph_daily is not None else etl_logs.load("paengi_daily")
    sd_ = ss_daily if ss_daily is not None else etl_logs.load("ss_daily")
    H = etl_logs.load("paengi_harvest")
    if not pd_.empty:
        pd_ = pd_.rename(columns={"total_kg": "생산kg", "ipsang": "입상병수"})
    if not sd_.empty:
        sd_ = sd_.rename(columns={"kg": "생산kg"})
    if not H.empty:
        H = H.rename(columns={"g_per_bottle": "병당중량"})
    def win(df, col="date"):
        if df is None or df.empty or col not in df:
            return pd.DataFrame()
        d = pd.to_datetime(df[col]); return df[(d >= start) & (d <= end)]
    def win_prev(df, col="date"):
        if df is None or df.empty or col not in df:
            return pd.DataFrame()
        d = pd.to_datetime(df[col]); return df[(d >= start - pd.Timedelta(days=7)) & (d < start)]
    pw, pp = win(pd_), win_prev(pd_); sw, sp = win(sd_), win_prev(sd_); hw, hp = win(H), win_prev(H)
    kw = K[K["완결"] & (pd.to_datetime(K["수확시작"]) >= start) & (pd.to_datetime(K["수확시작"]) <= end)] if K is not None and not K.empty else pd.DataFrame()
    cw = cycles[(pd.to_datetime(cycles["start"]) >= start) & (pd.to_datetime(cycles["start"]) < end + pd.Timedelta(days=1))] if cycles is not None and not cycles.empty else pd.DataFrame()
    aw = an[(pd.to_datetime(an["날짜"]) >= start) & (pd.to_datetime(an["날짜"]) <= end)] if an is not None and not an.empty else pd.DataFrame()
    P = etl_logs.load("cult_scrape_paengi"); Pw = win(P, "긁기일"); Pp = win_prev(P, "긁기일")
    rate = lambda d: 100 * d["불량"].sum() / d["배양출고"].sum() if len(d) and d["배양출고"].sum() > 0 else np.nan
    with PdfPages(pdf_buf) as pdf:
        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, title, fontsize=15, weight="bold")
        fig.text(0.07, 0.925, f"작성 {made} · 괄호 안은 전주 대비 (앱 v{C.VERSION})", fontsize=8, color=GRAY)
        gs = fig.add_gridspec(2, 4, left=0.06, right=0.94, top=0.90, bottom=0.72, hspace=0.35, wspace=0.12)
        def dd(a, b, unit="", nd=0):
            return f"전주 {_f(b, nd, unit)} ({'+' if (a - b) >= 0 else ''}{_f(a - b, nd)})" if pd.notna(a) and pd.notna(b) else "전주 자료 없음"
        pk, ppk = (pw["생산kg"].sum() if len(pw) else np.nan), (pp["생산kg"].sum() if len(pp) else np.nan)
        sk, spk = (sw["생산kg"].sum() if "생산kg" in sw and len(sw) else np.nan), (sp["생산kg"].sum() if "생산kg" in sp and len(sp) else np.nan)
        wgt, wgp = (hw["병당중량"].mean() if "병당중량" in hw and len(hw) else np.nan), (hp["병당중량"].mean() if "병당중량" in hp and len(hp) else np.nan)
        _kpi(fig.add_subplot(gs[0, 0]), "팽이 생산량", _f(pk, 0, " kg"), dd(pk, ppk, " kg"))
        _kpi(fig.add_subplot(gs[0, 1]), "팽이 병당중량", _f(wgt, 1, " g"), dd(wgt, wgp, " g", 1))
        _kpi(fig.add_subplot(gs[0, 2]), "새송이 생산량", _f(sk, 0, " kg"), dd(sk, spk, " kg"))
        _kpi(fig.add_subplot(gs[0, 3]), "팽이 균긁기 불량률", _f(rate(Pw), 2, " %"), dd(rate(Pw), rate(Pp), " %", 2))
        n_bad = int(((cw.get("구분", pd.Series(dtype=str)) == "이상") | (cw.get("중단횟수", pd.Series(dtype=float)) > 0)).sum()) if len(cw) else 0
        _kpi(fig.add_subplot(gs[1, 0]), "살균 사이클", f"{len(cw)} 회", f"확인 필요 {n_bad}회")
        _kpi(fig.add_subplot(gs[1, 1]), "수확 배치(팽이)", f"{len(kw)} 개", f"QC 평균 {_f(kw['QC'].mean(), 1) if len(kw) else '—'}")
        _kpi(fig.add_subplot(gs[1, 2]), "이상 항목", f"{len(aw)} 건", f"높음 {int((aw['심각도']=='높음').sum()) if len(aw) else 0}건")
        e = env[(env.date >= start) & (env.date <= end) & (env.run > 0.5)] if env is not None and not env.empty else pd.DataFrame()
        _kpi(fig.add_subplot(gs[1, 3]), "운전 중 방", f"{e.room.nunique() if len(e) else 0} 개", f"추종오차 {_f(e.track_err.mean(), 2, '℃') if len(e) else '—'}")
        # 일별 표
        rows = []
        for d in pd.date_range(start, end):
            r = {"날짜": d.strftime("%m/%d (%a)")}
            x = pw[pd.to_datetime(pw["date"]) == d] if len(pw) else pd.DataFrame()
            r["팽이 생산kg"] = x["생산kg"].sum() if len(x) else np.nan
            r["입상병수"] = x["입상병수"].sum() if len(x) and "입상병수" in x else np.nan
            h = hw[pd.to_datetime(hw["date"]) == d] if len(hw) else pd.DataFrame()
            r["병당중량"] = h["병당중량"].mean() if len(h) and "병당중량" in h else np.nan
            y = sw[pd.to_datetime(sw["date"]) == d] if len(sw) else pd.DataFrame()
            r["새송이 생산kg"] = y["생산kg"].sum() if len(y) and "생산kg" in y else np.nan
            c = cw[pd.to_datetime(cw["start"]).dt.normalize() == d] if len(cw) else pd.DataFrame()
            r["살균 회"] = len(c); r["F0 최소"] = c["F0"].min() if len(c) else np.nan
            a = aw[pd.to_datetime(aw["날짜"]) == d] if len(aw) else pd.DataFrame()
            r["이상 건"] = len(a)
            rows.append(r)
        fig.text(0.07, 0.68, "일별 요약", fontsize=10, weight="bold")
        _table(fig.add_axes([0.05, 0.44, 0.90, 0.23]), pd.DataFrame(rows).round(1), font=7.5)
        if len(kw):
            fig.text(0.07, 0.41, f"이번 주 수확 배치 (팽이 {len(kw)}개)", fontsize=10, weight="bold")
            t = kw.sort_values("수확시작")[["bid", "생육실", "수확시작", "총일수", "병당중량_표시", "QC", "긁기불량률%", "권지불량률%"]].copy()
            t["수확시작"] = pd.to_datetime(t["수확시작"]).dt.strftime("%m/%d"); t["생육실"] = t["생육실"].astype(int)
            _table(fig.add_axes([0.05, 0.06, 0.90, 0.34]), t.rename(columns={"bid": "배치", "병당중량_표시": "병당중량", "긁기불량률%": "긁기불량%", "권지불량률%": "권지불량%"}).round(2), font=7, max_rows=22)
        fig.text(0.5, 0.02, "1", ha="center", fontsize=8, color=GRAY)
        png1 = io.BytesIO(); fig.savefig(png1, format="png", dpi=110)
        pdf.savefig(fig); plt.close(fig)

        fig = plt.figure(figsize=A4)
        fig.text(0.07, 0.95, "살균 · 이상 항목", fontsize=14, weight="bold")
        if len(cw):
            t = cw.sort_values("start")[["unit", "구분", "start", "1차유지분", "2차유지분", "최고온도", "총가동분", "F0", "중단횟수"]].copy() if "구분" in cw else cw.sort_values("start")[["unit", "start", "2차유지분", "최고온도", "총가동분", "F0"]].copy()
            t["start"] = pd.to_datetime(t["start"]).dt.strftime("%m/%d %H:%M")
            fig.text(0.07, 0.92, f"살균 사이클 {len(cw)}회", fontsize=10, weight="bold")
            _table(fig.add_axes([0.05, 0.50, 0.90, 0.41]), t.rename(columns={"unit": "솥", "start": "시작"}).round(1), font=6.8, max_rows=30)
        else:
            fig.text(0.07, 0.90, "살균 기록 없음", fontsize=9, color=GRAY)
        if len(aw):
            sel = aw[aw["심각도"].isin(["높음", "중간"])].sort_values("날짜", ascending=False)
            top = sel[["날짜", "분류", "방", "내용"]].head(18).copy()
            top["내용"] = top["내용"].astype(str).str.slice(0, 48)
            more = f" · 최근 {len(top)}건만 표시, 전체는 엑셀 참조" if len(sel) > len(top) else ""
            fig.text(0.07, 0.46, f"이상 항목 (높음·중간 {len(sel)}건 / 전체 {len(aw)}건{more})", fontsize=10, weight="bold")
            _table(fig.add_axes([0.05, 0.06, 0.90, 0.39]), top, col_w=[0.12, 0.16, 0.12, 0.60], font=6.8, max_rows=18)
        else:
            fig.text(0.07, 0.44, "이상 항목 없음", fontsize=9, color=GRAY)
        fig.text(0.5, 0.02, "2", ha="center", fontsize=8, color=GRAY)
        pdf.savefig(fig); plt.close(fig)
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        pd.DataFrame(rows).to_excel(xw, sheet_name="일별", index=False)
        for name, df in (("수확 배치", kw), ("살균", cw), ("이상 항목", aw)):
            if df is not None and not df.empty:
                d2 = df.copy()
                for c in d2.columns:
                    if str(d2[c].dtype).startswith("datetime"):
                        d2[c] = d2[c].dt.strftime("%Y-%m-%d %H:%M")
                d2.drop(columns=[c for c in d2.columns if c.startswith("_")], errors="ignore").to_excel(xw, sheet_name=name, index=False)
    return {"pdf": pdf_buf.getvalue(), "xlsx": xbuf.getvalue(), "title": title, "png1": png1.getvalue()}


# ───────────────────────── 자동 생성 (명령줄) ─────────────────────────
OUT_DIR = os.path.join(C.DATA_DIR, "분석결과", "01_리포트")


def _gather():
    from . import analysis as A, etl_retort
    env = A.env_daily(); K = A.paengi_chain(); B = A.ss_batches()
    pm, sm, cm = A.paengi_monthly(), A.ss_monthly(B), A.cult_monthly()
    cost, _ = A.cost_monthly(); an = A.anomalies(env, days=400)
    cycles = etl_retort.load_cycles()
    try:
        tanks = A.tank_batches()
    except Exception:
        tanks = pd.DataFrame()
    stock = {}
    for crop in ("팽이", "새송이"):
        try:
            stock[crop] = A.stock_recon(crop)[0]
        except Exception:
            pass
    return dict(env=env, K=K, B=B, pm=pm, sm=sm, cm=cm, cost=cost, an=an, cycles=cycles, tanks=tanks, stock=stock, tank=A.tank_rank())


def make_monthly(month, g=None):
    g = g or _gather()
    return build_monthly(month, g["pm"], g["sm"], g["cm"], g["cost"], g["K"], g["B"], g["an"], g["env"], g["tank"], cycles=g["cycles"], tanks=g["tanks"], stock=g["stock"])


def make_weekly(week_end, g=None):
    g = g or _gather()
    return build_weekly(week_end, g["pm"], g["K"], g["B"], g["an"], g["env"], g["cycles"])


def save(out, stem):
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    for ext in ("pdf", "xlsx"):
        p = os.path.join(OUT_DIR, f"{stem}.{ext}")
        with open(p, "wb") as fh:
            fh.write(out[ext])
        paths.append(p)
    return paths


def main(argv=None):
    """python -m mushroom_app.report auto | monthly YYYY-MM | weekly YYYY-MM-DD(일요일)"""
    argv = sys.argv[1:] if argv is None else argv
    today = pd.Timestamp.today().normalize()
    cmd = argv[0] if argv else "auto"
    made = []
    if cmd == "monthly":
        m = argv[1] if len(argv) > 1 else _prev(today.strftime("%Y-%m"))
        made += save(make_monthly(m), f"월간보고서_{m}")
    elif cmd == "weekly":
        we = pd.Timestamp(argv[1]) if len(argv) > 1 else today - pd.Timedelta(days=today.weekday() + 1)
        made += save(make_weekly(we), f"주간보고서_{(we - pd.Timedelta(days=6)):%Y-%m-%d}_{we:%m-%d}")
    else:   # auto: 월요일 → 지난주, 1일 → 지난달 (이미 있으면 건너뜀)
        g = None
        if today.weekday() == 0:
            we = today - pd.Timedelta(days=1)
            stem = f"주간보고서_{(we - pd.Timedelta(days=6)):%Y-%m-%d}_{we:%m-%d}"
            if not os.path.exists(os.path.join(OUT_DIR, stem + ".pdf")):
                g = g or _gather(); made += save(make_weekly(we, g), stem)
        if today.day == 1:
            m = _prev(today.strftime("%Y-%m")); stem = f"월간보고서_{m}"
            if not os.path.exists(os.path.join(OUT_DIR, stem + ".pdf")):
                g = g or _gather(); made += save(make_monthly(m, g), stem)
    print("만든 보고서: " + (", ".join(made) if made else "없음 (월요일·1일이 아니거나 이미 있음)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
