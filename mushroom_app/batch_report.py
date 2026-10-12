# -*- coding: utf-8 -*-
"""배치 보고서 (팽이): 배치 하나를 골라 공정 순서대로 — 원재료 → 입병 → 살균 → 냉각 → 접종 → 배양 → 균긁기 → 발이실 → 권지 → 생육실 → 수확 —
흩어진 원본 기록과 그래프, 이슈를 A4 PDF 한 벌로 묶는다. 배치 이력(평균 대비 판정표)과 달리 '그때 실제로 뭐가 어땠나'를 보여주는 문서.

build(bid) → {"pdf": bytes, "title": str, "issues": [...]}

레이아웃 원칙: 표나 그래프는 절대 페이지에 걸쳐 쪼개지 않는다. 남은 자리에 안 들어가면 통째로 다음 장으로.
소제목은 다음 블록과 붙어 다닌다(제목만 페이지 끝에 남지 않게)."""
import io, os, re, textwrap, datetime as dt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.dates as md
from matplotlib.backends.backend_pdf import PdfPages
from . import config as C, etl_logs, etl_retort, analysis as A
from .report import _font

# ── 색 ─────────────────────────────────────────────────────────
INK, INK2, LINE = "#0b0b0b", "#52514e", "#d7dbd3"
BLUE, ORANGE, AQUA, YELLOW, RED, GRAY = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e34948", "#6b7160"
FILL_HDR, FILL_ISSUE, FILL_OK, FILL_NOTE = "#eef1ee", "#fdecea", "#e9f6f0", "#f5f7f4"

A4 = (8.27, 11.69)
MARGIN_X, MARGIN_TOP, MARGIN_BOT = 0.55, 0.55, 0.6
W = A4[0] - 2 * MARGIN_X


def _f(v, nd=1, unit=""):
    if v is None or (isinstance(v, float) and np.isnan(v)) or (isinstance(v, str) and v in ("nan", "None", "")):
        return "—"
    try:
        return f"{float(v):,.{nd}f}{unit}"
    except (TypeError, ValueError):
        return str(v)


def _d(x, fmt="%m/%d"):
    return x.strftime(fmt) if isinstance(x, (pd.Timestamp, dt.date, dt.datetime)) and pd.notna(x) else "—"


def _tx(x, n=None):
    s = "—" if x is None or (isinstance(x, float) and np.isnan(x)) or str(x) in ("nan", "None", "NaT", "") else str(x)
    return s if n is None else s[:n]


def _hm2(t):
    return t.strftime('%H:%M') if pd.notna(t) else '—'


def _hm(m):
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}" if m is not None and pd.notna(m) else "—"


# ── 페이지 조판기 ─────────────────────────────────────────────
class Doc:
    """A4 세로. y 는 위에서부터 인치. 블록 하나가 남은 자리에 안 들어가면 새 장을 연다."""
    LINE_H = 0.185          # 본문 한 줄 높이(인치)
    ROW_H = 0.2             # 표 한 행

    def __init__(self, title, subtitle):
        self.buf = io.BytesIO()
        self.pdf = PdfPages(self.buf)
        self.title, self.subtitle = title, subtitle
        self.page = 0
        self.fig = None
        self.pngs = []          # 쪽별 미리보기(PNG) — 화면 표시용. 브라우저 PDF 뷰어에 기대지 않는다
        self.y = MARGIN_TOP
        self._new_page()

    # 좌표: 인치 → figure 비율
    def _fx(self, x):
        return x / A4[0]

    def _fy(self, y_top, h):
        """위에서 y_top 인치, 높이 h 인치인 상자의 (bottom, height) figure 비율"""
        return (A4[1] - y_top - h) / A4[1], h / A4[1]

    def _flush(self):
        self._footer()
        png = io.BytesIO(); self.fig.savefig(png, format="png", dpi=100, facecolor="white"); self.pngs.append(png.getvalue())
        self.pdf.savefig(self.fig); plt.close(self.fig)

    def _new_page(self):
        if self.fig is not None:
            self._flush()
        self.page += 1
        self.fig = plt.figure(figsize=A4)
        self.fig.patch.set_facecolor("white")
        self.y = MARGIN_TOP
        if self.page > 1:
            self.fig.text(self._fx(MARGIN_X), 1 - self._fx(0.3) * A4[0] / A4[1], self.title, fontsize=8, color=GRAY)
            self.fig.add_artist(plt.Line2D([self._fx(MARGIN_X), self._fx(A4[0] - MARGIN_X)], [1 - 0.42 / A4[1]] * 2, color=LINE, lw=0.6))
            self.y = 0.55

    def _footer(self):
        self.fig.text(0.5, 0.25 / A4[1], f"{self.page}", ha="center", fontsize=8, color=GRAY)
        self.fig.text(1 - self._fx(MARGIN_X), 0.25 / A4[1], self.subtitle, ha="right", fontsize=7, color=GRAY)

    def need(self, h):
        if self.y + h > A4[1] - MARGIN_BOT:
            self._new_page()

    def remaining(self):
        return A4[1] - MARGIN_BOT - self.y

    def space(self, h=0.12):
        self.y += h

    # 텍스트 폭 → 글자 수 (한글 기준 대략)
    def _chars(self, w, fs):
        return max(8, int(w * 72 / (fs * 0.95)))

    def text(self, s, fs=9, color=INK, bold=False, x=0.0, w=None, indent=0.0):
        w = W if w is None else w
        lines = []
        for para in str(s).split("\n"):
            lines += textwrap.wrap(para, self._chars(w - indent, fs)) or [""]
        h = len(lines) * (fs / 72) * 1.5
        self.need(h)
        for i, ln in enumerate(lines):
            self.fig.text(self._fx(MARGIN_X + x + indent), 1 - (self.y + (i + 0.85) * (fs / 72) * 1.5) / A4[1], ln,
                          fontsize=fs, color=color, fontweight="bold" if bold else "normal", va="baseline")
        self.y += h
        return h

    def h1(self, s, sub=None):
        self.need(0.55)
        self.fig.text(self._fx(MARGIN_X), 1 - (self.y + 0.3) / A4[1], s, fontsize=17, fontweight="bold", color=INK)
        self.y += 0.42
        if sub:
            self.text(sub, fs=8.5, color=INK2)
        self.space(0.08)

    def h2(self, n, s, right=None, keep=0.9):
        """단계 제목. keep 만큼의 다음 내용이 같은 장에 들어가야 한다."""
        self.need(0.36 + keep)
        self.space(0.1)
        y0, hh = self._fy(self.y, 0.28)
        ax = self.fig.add_axes([self._fx(MARGIN_X), y0, self._fx(W), hh]); ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor=FILL_HDR, edgecolor="none"))
        ax.add_patch(plt.Rectangle((0, 0), 0.006, 1, transform=ax.transAxes, facecolor=BLUE, edgecolor="none"))
        ax.text(0.014, 0.5, f"{n}  {s}", fontsize=10.5, fontweight="bold", va="center", color=INK, transform=ax.transAxes)
        if right:
            ax.text(0.992, 0.5, right, fontsize=8.5, va="center", ha="right", color=INK2, transform=ax.transAxes)
        self.y += 0.28 + 0.06

    def kv(self, pairs, cols=3, fs=8.5):
        """(라벨, 값) 목록을 cols 열 격자로. 값이 길면 접는다."""
        pairs = [(k, v) for k, v in pairs if v not in (None, "", "—")] or pairs
        cw = W / cols
        rows = [pairs[i:i + cols] for i in range(0, len(pairs), cols)]
        # 각 행 높이 = 가장 긴 값의 줄 수
        heights = []
        wrapped = []
        for row in rows:
            wr = []
            mx = 1
            for k, v in row:
                ls = textwrap.wrap(str(v), self._chars(cw - 0.12, fs)) or ["—"]
                wr.append((k, ls)); mx = max(mx, len(ls))
            wrapped.append(wr); heights.append(0.2 + mx * (fs / 72) * 1.5)
        self.need(sum(heights))
        for row, hh in zip(wrapped, heights):
            for j, (k, ls) in enumerate(row):
                x = MARGIN_X + j * cw
                self.fig.text(self._fx(x), 1 - (self.y + 0.11) / A4[1], k, fontsize=fs - 1.5, color=GRAY, va="baseline")
                for i, ln in enumerate(ls):
                    self.fig.text(self._fx(x), 1 - (self.y + 0.2 + (i + 0.85) * (fs / 72) * 1.5) / A4[1], ln, fontsize=fs, color=INK, va="baseline")
            self.y += hh
        self.space(0.06)

    def table(self, df, col_w=None, fs=7.5, max_rows=40, title=None, zebra=True, red_rows=None):
        """표는 쪼개지 않는다. 40행 넘으면 자른다(배치 보고서에서 그럴 일은 거의 없다). red_rows: 빨간 글씨로 쓸 행 번호(0부터)"""
        if df is None or len(df) == 0:
            return
        df = df.head(max_rows)
        n = len(df) + 1
        h = n * self.ROW_H + (0.22 if title else 0)
        self.need(h)
        if title:
            self.text(title, fs=8.5, bold=True); self.space(0.02)
        y0, hh = self._fy(self.y, n * self.ROW_H)
        ax = self.fig.add_axes([self._fx(MARGIN_X), y0, self._fx(W), hh]); ax.axis("off")
        cells = [["" if (isinstance(v, float) and np.isnan(v)) or v is None else str(v) for v in r] for r in df.values]
        t = ax.table(cellText=cells, colLabels=list(df.columns), loc="upper center", cellLoc="center", colWidths=col_w, bbox=[0, 0, 1, 1])
        t.auto_set_font_size(False); t.set_fontsize(fs)
        for (r, c), cell in t.get_celld().items():
            cell.set_edgecolor(LINE); cell.set_linewidth(0.5)
            if r == 0:
                cell.set_facecolor(FILL_HDR); cell.set_text_props(weight="bold", color=INK)
            elif zebra and r % 2 == 0:
                cell.set_facecolor("#fafbf9")
            if r > 0 and red_rows and (r - 1) in red_rows:
                cell.set_text_props(color=RED, weight="bold"); cell.set_facecolor("#fdf0ef")
        self.y += n * self.ROW_H
        self.space(0.08)

    def chart(self, draw, h=2.1, title=None, alt=None):
        """draw(ax_list) 를 호출. 그래프는 쪼개지 않는다. alt(화면용 Altair)는 PDF 에서는 쓰지 않는다."""
        tot = h + (0.22 if title else 0)
        self.need(tot)
        if title:
            self.text(title, fs=8.5, bold=True); self.space(0.02)
        y0, hh = self._fy(self.y, h)
        axs = draw(self.fig, [self._fx(MARGIN_X), y0, self._fx(W), hh])
        self.y += h
        self.space(0.1)
        return axs

    def issues(self, items, ok_text="특이사항 없음"):
        """이슈 목록 상자. items: [(등급, 문장)] 등급: '이슈' | '참고'"""
        if not items:
            self.note(ok_text, kind="ok"); return
        fs = 8.5
        lines = []
        for lvl, s in items:
            for i, ln in enumerate(textwrap.wrap(s, self._chars(W - 0.35, fs))):
                lines.append((lvl if i == 0 else None, ln))
        h = 0.1 + len(lines) * (fs / 72) * 1.5 + 0.08
        self.need(h)
        y0, hh = self._fy(self.y, h)
        ax = self.fig.add_axes([self._fx(MARGIN_X), y0, self._fx(W), hh]); ax.axis("off")
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor=FILL_ISSUE, edgecolor="none"))
        for i, (lvl, ln) in enumerate(lines):
            yy = 1 - (0.1 + (i + 0.8) * (fs / 72) * 1.5) / h
            if lvl:
                ax.text(0.012, yy, "▲" if lvl == "이슈" else "•", fontsize=fs, color=RED if lvl == "이슈" else ORANGE, va="baseline", transform=ax.transAxes, fontweight="bold")
            ax.text(0.04, yy, ln, fontsize=fs, color=INK, va="baseline", transform=ax.transAxes)
        self.y += h
        self.space(0.08)

    def note(self, s, kind="note", fs=8.5):
        lines = []
        for para in str(s).split("\n"):
            lines += textwrap.wrap(para, self._chars(W - 0.25, fs)) or [""]
        h = 0.08 + len(lines) * (fs / 72) * 1.5 + 0.06
        self.need(h)
        y0, hh = self._fy(self.y, h)
        ax = self.fig.add_axes([self._fx(MARGIN_X), y0, self._fx(W), hh]); ax.axis("off")
        alert = kind == "alert"                    # 눈에 띄게 따로 알릴 것 (빨간 테두리·첫 줄 빨간 굵은 글씨, v1.24.7)
        ax.add_patch(plt.Rectangle((0, 0), 1, 1, transform=ax.transAxes, facecolor=FILL_OK if kind == "ok" else (FILL_ISSUE if alert else FILL_NOTE),
                                   edgecolor=RED if alert else "none", lw=1.6 if alert else 0))
        for i, ln in enumerate(lines):
            ax.text(0.015, 1 - (0.08 + (i + 0.8) * (fs / 72) * 1.5) / h, ("○ " if kind == "ok" and i == 0 else "") + ln, fontsize=fs,
                    color=(RED if i == 0 else INK) if alert else (INK if kind == "ok" else INK2), fontweight="bold" if alert and i == 0 else "normal",
                    va="baseline", transform=ax.transAxes)
        self.y += h
        self.space(0.08)

    def close(self):
        self._flush()
        self.pdf.close()
        return self.buf.getvalue()


# ── 그래프 그리기 ────────────────────────────────────────────
def _style(ax, ylabel=None):
    ax.grid(alpha=0.25, lw=0.5); ax.tick_params(labelsize=7, colors=INK2)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(LINE)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=7.5, color=INK2)


def _drop_marker():
    """물방울 모양 마커 (matplotlib Path)"""
    from matplotlib.path import Path as MPath
    verts = [(0, 1), (0.45, 0.45), (0.62, 0.1), (0.62, -0.3),
             (0.62, -0.64), (0.34, -0.92), (0, -0.92), (-0.34, -0.92), (-0.62, -0.64), (-0.62, -0.3),
             (-0.62, 0.1), (-0.45, 0.45), (0, 1), (0, 1)]
    codes = [MPath.MOVETO, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
             MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CURVE4, MPath.CLOSEPOLY]
    return MPath(verts, codes)


def _rain_days(P, rain):
    """P(date, cday) 와 비 온 날 표 → [(cday, mm)]"""
    if rain is None or len(rain) == 0 or P is None or len(P) == 0 or "date" not in P:
        return []
    m = P[["cday", "date"]].merge(rain, on="date", how="inner")
    return list(zip(m.cday, m["강수mm"]))


def _env_triplet(fig, box, P, marks=None, xlab="일차", rain=None):
    """온도(실측·설정) / 습도 / CO2 세 칸 나란히. P: cday, temp, sp_temp, hum, co2, temp_min, temp_max. rain: 비 온 날 표 → 습도 칸 아래 ○"""
    x0, y0, w, h = box
    gap = 0.012
    cw = (w - 2 * gap) / 3
    axs = []
    for i, (col, sp, lbl, color) in enumerate((("temp", "sp_temp", "온도(℃)", BLUE), ("hum", None, "습도(%)", AQUA), ("co2", None, "CO2(ppm)", ORANGE))):
        ax = fig.add_axes([x0 + i * (cw + gap), y0 + 0.06 * h, cw, h * 0.86])
        if col in P and P[col].notna().any():
            if col == "temp" and "temp_min" in P:
                ax.fill_between(P.cday, P.temp_min, P.temp_max, color=color, alpha=0.15, lw=0)
            ax.plot(P.cday, P[col], color=color, lw=1.6)
            if sp and sp in P and P[sp].notna().any():
                ax.plot(P.cday, P[sp], color=INK2, lw=1, ls="--")
        else:
            ax.text(0.5, 0.5, "자료 없음", ha="center", va="center", fontsize=8, color=GRAY, transform=ax.transAxes)
        if marks:
            for m, name in marks:
                ax.axvline(m, color=YELLOW, lw=0.8, ls=":")
        if col == "hum":
            rd = _rain_days(P, rain)
            if rd:
                ax.scatter([d for d, _ in rd], [0.05] * len(rd), transform=ax.get_xaxis_transform(), s=42, marker=_drop_marker(), color=BLUE, alpha=0.85, linewidths=0, zorder=5, clip_on=False)
                ax.scatter([0.955], [0.06], transform=ax.transAxes, s=30, marker=_drop_marker(), color=BLUE, alpha=0.85, linewidths=0, zorder=5, clip_on=False)
                ax.text(0.99, 0.02, f"비 {C.RAIN_MARK_MM:.0f}mm↑", fontsize=6, color=BLUE, ha="right", va="bottom", transform=ax.transAxes)
        _style(ax, lbl)
        ax.set_xlabel(xlab, fontsize=7.5, color=INK2)
        axs.append(ax)
    return axs


def _retort_curves(fig, box, curves, sp1=None, sp2=None, ylab="온도(℃)", base=100, lines=None):
    """살균 곡선: curves = [(라벨, ts 시리즈, 값 시리즈, 시작시각)] — 시작 기준 분으로 겹쳐 그린다.
    lines = [(값, 글자)] 를 주면 설정선 대신 그 기준선을 긋는다 (압력 곡선용, v1.24.6)."""
    x0, y0, w, h = box
    ax = fig.add_axes([x0 + 0.03 * w, y0 + 0.1 * h, w * 0.95, h * 0.82])
    pal = [BLUE, ORANGE, AQUA, YELLOW, RED, "#4a3aa7"]
    for i, (lbl, ts, temp, start) in enumerate(curves):
        m = (ts - start).dt.total_seconds() / 60
        ax.plot(m, temp, color=pal[i % len(pal)], lw=1.5, label=lbl)
    if lines is None:
        lines = [(v, f"{name} {v:.0f}℃") for v, name in ((sp1, "1차 설정"), (sp2, "2차 설정")) if v is not None and pd.notna(v)]
    for v, txt in lines:
        ax.axhline(v, color=INK2, lw=0.8, ls="--")
        ax.text(0.01, v, txt, fontsize=7, color=INK2, va="bottom", ha="left", transform=ax.get_yaxis_transform(),
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    if base is not None:
        ax.axhline(base, color=LINE, lw=0.6)
    _style(ax, ylab); ax.set_xlabel("살균 시작 후 분", fontsize=7.5, color=INK2)
    if len(curves) > 1:
        ax.legend(fontsize=7, frameon=False, loc="lower center", ncol=len(curves))
    return [ax]


def _cool_curve(fig, box, W, ev, pots=None, morning=None, rain=None):
    """냉각실 두 방의 1분 온도 (살균일 낮 → 접종일 아침). pots = [(살균완료 시각, 라벨)] 를 세로선으로, 사건의 최고점→20℃ 를 표시."""
    x0, y0, w, h = box
    ax = fig.add_axes([x0 + 0.03 * w, y0 + 0.12 * h, w * 0.95, h * 0.8])
    for room, col in (("냉각1실", BLUE), ("냉각실2", ORANGE)):
        g = W[W.room == room]
        if len(g):
            ax.plot(g.ts, g.temp, color=col, lw=1.4, label=room)
            sp = g.sp_temp.dropna()
            if len(sp):
                ax.axhline(float(sp.median()), color=col, lw=0.6, ls="--", alpha=0.6)
    ax.axhline(A.COOL_TARGET, color=INK2, lw=0.7, ls=":")
    ax.text(0.005, A.COOL_TARGET, f"{A.COOL_TARGET:.0f}℃", fontsize=6.5, color=INK2, va="bottom", transform=ax.get_yaxis_transform())
    for t, lbl in (pots or []):
        if pd.notna(t):
            ax.axvline(t, color=YELLOW, lw=0.8, ls=":")
            ax.text(t, 0.98, lbl, fontsize=6, color=INK2, rotation=90, va="top", ha="right", transform=ax.get_xaxis_transform())
    for _, r in (ev if ev is not None else pd.DataFrame()).iterrows():
        col = RED if r.get("느림") else INK2
        tp, vp = r.get("마지막최고시각", r["최고시각"]), r.get("마지막최고", r["최고온도"])
        ax.annotate("", xy=(r["도달"], A.COOL_TARGET), xytext=(tp, vp), arrowprops=dict(arrowstyle="->", color=col, lw=0.8, ls="-"))
        ax.text(tp, vp + 1.5, f"{vp:.0f}℃ → 20℃ {r['소요분'] // 60}시간 {r['소요분'] % 60:02d}분" + (f" (평소 {r['평소분'] / 60:.1f}시간)" if pd.notna(r.get("평소분")) else ""),
                fontsize=6.5, color=col, ha="left", va="bottom", fontweight="bold" if r.get("느림") else "normal")
    if rain is not None and len(rain) and len(W):
        for _, r in rain[(rain.date >= W.ts.min().normalize()) & (rain.date <= W.ts.max().normalize())].iterrows():
            a0, a1 = max(r.date, W.ts.min()), min(r.date + pd.Timedelta(days=1), W.ts.max())
            ax.axvspan(a0, a1, color=BLUE, alpha=0.06, lw=0)
            ax.scatter([a0 + (a1 - a0) / 2], [0.93], transform=ax.get_xaxis_transform(), s=40, marker=_drop_marker(), color=BLUE, alpha=0.85, linewidths=0, zorder=5)
            ax.text(a0 + (a1 - a0) / 2, 0.88, f"{r['강수mm']:.0f}mm", fontsize=6.5, color=BLUE, ha="center", va="top", transform=ax.get_xaxis_transform())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d일 %H시"))
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
    if len(W):
        ax.set_xlim(W.ts.min(), W.ts.max())
    _style(ax, "온도(℃)")
    ax.set_ylim(0, max(60, float(W.temp.max()) + 8) if len(W) else 60)
    ax.legend(fontsize=7, frameon=False, loc="upper right", ncol=2)
    return [ax]



# ── 화면용 그래프(Altair, 마우스를 올리면 값이 보임) — PDF 는 위의 matplotlib 그림을 그대로 쓴다 (v1.24.4) ──
def _alt_env_triplet(P, marks=None, xlab="일차", rain=None):
    """_env_triplet 의 화면판: 온도(띠: 최저~최고, 점선: 설정) / 습도(💧 비 온 날) / CO2 — 차트 3개 목록"""
    import altair as alt
    P = P.copy()
    if "date" in P:
        P["날짜"] = pd.to_datetime(P["date"]).dt.strftime("%m/%d")
    else:
        P["날짜"] = ""
    xmin, xmax = float(P["cday"].min()), float(P["cday"].max())
    xenc = alt.X("cday:Q", title=xlab, scale=alt.Scale(domain=[xmin, xmax], nice=False), axis=alt.Axis(tickMinStep=1))
    out = []
    for col, sp, lbl, color in (("temp", "sp_temp", "온도(℃)", BLUE), ("hum", None, "습도(%)", AQUA), ("co2", None, "CO2(ppm)", ORANGE)):
        if col not in P or not P[col].notna().any():
            out.append(alt.Chart(pd.DataFrame({"t": ["자료 없음"]})).mark_text(color=GRAY, fontSize=13).encode(text="t:N").properties(height=200, title=lbl))
            continue
        tip = [alt.Tooltip("cday:Q", title="일차"), alt.Tooltip("날짜:N"), alt.Tooltip(f"{col}:Q", title=lbl, format=",.1f")]
        if sp and sp in P:
            tip.append(alt.Tooltip(f"{sp}:Q", title="설정", format=".1f"))
        if col == "temp" and "temp_min" in P:
            tip += [alt.Tooltip("temp_min:Q", title="최저", format=".1f"), alt.Tooltip("temp_max:Q", title="최고", format=".1f")]
        base = alt.Chart(P).encode(x=xenc)
        layers = []
        if col == "temp" and "temp_min" in P and P["temp_min"].notna().any():
            layers.append(base.mark_area(color=color, opacity=0.15).encode(y=alt.Y("temp_min:Q", title=lbl, scale=alt.Scale(zero=False)), y2="temp_max:Q"))
        layers.append(base.mark_line(color=color, strokeWidth=2, point=alt.OverlayMarkDef(size=18, color=color)).encode(
            y=alt.Y(f"{col}:Q", title=lbl, scale=alt.Scale(zero=False)), tooltip=tip))
        if sp and sp in P and P[sp].notna().any():
            layers.append(base.mark_line(color=INK2, strokeDash=[5, 4], strokeWidth=1.2).encode(y=f"{sp}:Q", tooltip=tip))
        for m, name in (marks or []):
            layers.append(alt.Chart(pd.DataFrame({"cday": [m], "이름": [name]})).mark_rule(color=YELLOW, strokeDash=[2, 3], strokeWidth=1.5).encode(
                x="cday:Q", tooltip=[alt.Tooltip("이름:N", title="표시"), alt.Tooltip("cday:Q", title="일차")]))
        if col == "hum":
            rd = _rain_days(P, rain)
            if rd:
                R = pd.DataFrame({"cday": [d for d, _ in rd], "mm": [m for _, m in rd]})
                layers.append(alt.Chart(R).mark_text(text="💧", fontSize=13, baseline="bottom").encode(
                    x="cday:Q", y=alt.value(196), tooltip=[alt.Tooltip("cday:Q", title="일차"), alt.Tooltip("mm:Q", title="강수(mm)", format=".0f")]))
        out.append(alt.layer(*layers).properties(height=200, title=alt.TitleParams(lbl, fontSize=12, anchor="start")))
    return out


def _alt_retort(curves, sp1=None, sp2=None, ylab="온도(℃)", base=100, lines=None):
    """_retort_curves 의 화면판: 솥별 온도(또는 압력) 곡선을 살균 시작 후 분으로 겹쳐서"""
    import altair as alt
    rows = []
    for lbl, ts, temp, start in curves:
        m = (pd.to_datetime(ts) - pd.Timestamp(start)).dt.total_seconds() / 60
        rows.append(pd.DataFrame({"분": m.values, "온도": pd.to_numeric(temp, errors="coerce").values, "솥": lbl,
                                  "시각": pd.to_datetime(ts).dt.strftime("%H:%M").values}))
    D = pd.concat(rows, ignore_index=True).dropna(subset=["분", "온도"])
    if len(D) > 4000:
        k = int(np.ceil(len(D) / 4000))
        D = D[D.groupby("솥").cumcount() % k == 0]
    pal = [BLUE, ORANGE, AQUA, YELLOW, RED, "#4a3aa7"]
    names = list(dict.fromkeys(D["솥"]))
    line = alt.Chart(D).mark_line(strokeWidth=2).encode(
        x=alt.X("분:Q", title="살균 시작 후 분"), y=alt.Y("온도:Q", title=ylab, scale=alt.Scale(zero=False)),
        color=alt.Color("솥:N", scale=alt.Scale(domain=names, range=pal[:len(names)]), legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["솥:N", alt.Tooltip("시각:N"), alt.Tooltip("분:Q", format=".0f"), alt.Tooltip("온도:Q", format=".1f", title=ylab)])
    layers = ([alt.Chart(pd.DataFrame({"y": [base]})).mark_rule(color=LINE).encode(y="y:Q")] if base is not None else []) + [line]
    if lines is None:
        lines = [(v, f"{n} {v:.0f}℃") for v, n in ((sp1, "1차 설정"), (sp2, "2차 설정")) if v is not None and pd.notna(v)]
    S = pd.DataFrame([{"y": v, "t": t_} for v, t_ in lines])
    if len(S):
        layers.append(alt.Chart(S).mark_rule(color=INK2, strokeDash=[5, 4]).encode(y="y:Q", tooltip=[alt.Tooltip("t:N", title="기준")]))
        layers.append(alt.Chart(S).mark_text(align="left", dx=4, dy=-6, color=INK2, fontSize=11).encode(x=alt.value(0), y="y:Q", text="t:N"))
    return [alt.layer(*layers).properties(height=260)]


def _alt_cool(W, ev, pots=None, rain=None):
    """_cool_curve 의 화면판: 냉각실 두 방 1분 온도, 살균 완료 시각(세로 점선), 마지막 솥 최고점 → 20℃ 화살표, 비 온 날"""
    import altair as alt
    W = W.copy()
    if len(W) > 3000:                          # 1분 기록이 길면 방마다 고르게 솎아서(모양은 같게) 그림을 가볍게
        k = int(np.ceil(len(W) / 3000))
        W = W[W.groupby("room").cumcount() % k == 0]
    W["시각"] = pd.to_datetime(W["ts"]).dt.strftime("%d일 %H:%M")
    rooms = [r for r in ("냉각1실", "냉각실2") if (W.room == r).any()]
    cols = {"냉각1실": BLUE, "냉각실2": ORANGE}
    t0, t1 = W.ts.min(), W.ts.max()
    xenc = alt.X("ts:T", title=None, scale=alt.Scale(domain=[t0.isoformat(), t1.isoformat()]), axis=alt.Axis(format="%d일 %H시", labelAngle=0, tickCount=8))
    ymax = max(60.0, float(W.temp.max()) + 8) if len(W) else 60.0
    yenc = alt.Y("temp:Q", title="온도(℃)", scale=alt.Scale(domain=[0, ymax]))
    layers = []
    if rain is not None and len(rain):
        rr = rain[(rain.date >= t0.normalize()) & (rain.date <= t1.normalize())]
        if len(rr):
            Rr = pd.DataFrame({"a": [max(d, t0) for d in rr.date], "b": [min(d + pd.Timedelta(days=1), t1) for d in rr.date], "mm": rr["강수mm"].values})
            layers.append(alt.Chart(Rr).mark_rect(color=BLUE, opacity=0.06).encode(x="a:T", x2="b:T", tooltip=[alt.Tooltip("mm:Q", title="강수(mm)", format=".0f")]))
    layers.append(alt.Chart(W[W.room.isin(rooms)]).mark_line(strokeWidth=1.8).encode(
        x=xenc, y=yenc, color=alt.Color("room:N", scale=alt.Scale(domain=rooms, range=[cols[r] for r in rooms]), legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("room:N", title="냉각실"), alt.Tooltip("시각:N"), alt.Tooltip("temp:Q", title="온도(℃)", format=".1f")]))
    spr = [{"room": r, "y": float(W[W.room == r].sp_temp.dropna().median())} for r in rooms if W[W.room == r].sp_temp.notna().any()]
    if spr:
        layers.append(alt.Chart(pd.DataFrame(spr)).mark_rule(strokeDash=[5, 4], opacity=0.6).encode(
            y="y:Q", color=alt.Color("room:N", scale=alt.Scale(domain=rooms, range=[cols[r] for r in rooms]), legend=None),
            tooltip=[alt.Tooltip("room:N", title="냉각실"), alt.Tooltip("y:Q", title="설정(℃)", format=".1f")]))
    layers.append(alt.Chart(pd.DataFrame({"y": [A.COOL_TARGET]})).mark_rule(color=INK2, strokeDash=[2, 3]).encode(y="y:Q"))
    pp = [(t, l) for t, l in (pots or []) if pd.notna(t)]
    if pp:
        Pp = pd.DataFrame({"ts": [t for t, _ in pp], "솥": [l for _, l in pp]})
        Pp["시각"] = pd.to_datetime(Pp["ts"]).dt.strftime("%d일 %H:%M")
        layers.append(alt.Chart(Pp).mark_rule(color=YELLOW, strokeDash=[2, 3], strokeWidth=1.5).encode(x="ts:T", tooltip=["솥:N", alt.Tooltip("시각:N", title="살균 완료")]))
    if ev is not None and len(ev):
        E = []
        for _, r in ev.iterrows():
            tp, vp = r.get("마지막최고시각", r["최고시각"]), r.get("마지막최고", r["최고온도"])
            E.append({"x": tp, "y": float(vp), "x2": r["도달"], "y2": float(A.COOL_TARGET), "느림": bool(r.get("느림")), "방": r["room"],
                      "글": f"{vp:.0f}℃ → 20℃ {int(r['소요분']) // 60}시간 {int(r['소요분']) % 60:02d}분" + (f" (평소 {r['평소분'] / 60:.1f}시간)" if pd.notna(r.get("평소분")) else "")})
        E = pd.DataFrame(E)
        ccol = alt.condition("datum.느림", alt.value(RED), alt.value(INK2))
        layers.append(alt.Chart(E).mark_rule(strokeWidth=1.5).encode(x="x:T", y="y:Q", x2="x2:T", y2="y2:Q", color=ccol, tooltip=["방:N", alt.Tooltip("글:N", title="최고점 → 20℃")]))
        layers.append(alt.Chart(E).mark_point(shape="triangle-down", size=60, filled=True).encode(x="x2:T", y="y2:Q", color=ccol))
        layers.append(alt.Chart(E).mark_text(align="left", dx=4, dy=-8, fontSize=11).encode(x="x:T", y="y:Q", text="글:N", color=ccol))
    return [alt.layer(*layers).properties(height=300)]

# ── 자료 모으기 ──────────────────────────────────────────────
def _season_ref(K, b):
    """같은 계절 완결 배치 (이슈 판정용 기준)"""
    if K is None or K.empty:
        return pd.DataFrame()
    d = K[K["완결"]] if "완결" in K else K
    if "season" in d and pd.notna(b.get("season")):
        s = d[d.season == b.season]
        if len(s) >= 8:
            return s
    return d


def _pct_rank(series, v, higher_is_bad=True):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 5 or v is None or pd.isna(v):
        return np.nan
    p = (s < v).mean() * 100
    return p if higher_is_bad else 100 - p


# 살균 2차 유지 압력 점검은 etl_retort 로 옮김 (v1.24.7)
_tsat, hold2_stats, hold2_ref, hold2_diag, SUSPECT = (etl_retort._tsat, etl_retort.hold2_stats, etl_retort.hold2_ref,
                                                       etl_retort.hold2_diag, etl_retort.SUSPECT)


def setting_suspects(G):
    """배치의 살균 사이클 중 '임의 설정 변경 의심'(재배관리 2차 설정보다 1.5℃ 넘게 낮고 압력도 함께 낮음, 스팀은 평소대로) 행 목록 (v1.24.7)"""
    cyc, P = G.get("cycles", pd.DataFrame()), G.get("loads", pd.DataFrame())
    if cyc is None or cyc.empty or P is None or P.empty or "설정2차온도" not in P or not P["설정2차온도"].notna().any():
        return []
    sp2 = P["설정2차온도"].dropna().iloc[0]
    return [r for _, r in cyc.iterrows() if pd.notna(r.get("2차유지온도")) and r["2차유지온도"] < sp2 - 1.5 and hold2_diag(r)[0] == SUSPECT]


def gather(bid, K=None, ST=None, env=None, PE=None, loads=None, basis="같은 계절"):
    """배치 하나의 단계별 원본 기록을 모은다. 반환 dict — build() 가 그린다.
    basis: 이슈 판정의 비교 기준 '같은 계절'(기본) | '전체' | '같은 달' (배치 이력서의 '비교 기준'과 같은 뜻, v1.23.2)"""
    K = A.paengi_chain() if K is None else K
    ST = A.paengi_stage_table(K, None, env) if ST is None else ST
    env = A.env_daily() if env is None else env
    if K.empty or not (K.bid == bid).any():
        return None
    b = K[K.bid == bid].iloc[0]
    sb = ST[ST.bid == bid].iloc[0] if not ST.empty and (ST.bid == bid).any() else b
    g = lambda k: sb.get(k) if k in sb.index else b.get(k)
    # 비교 배치 (수확 완료): 배치가 적으면 한 단계 넓힌다 (같은 달 → 같은 계절 → 전체)
    try:
        ref, basis_label = A.dossier_group(ST if not ST.empty else K, sb, basis)
    except Exception:
        ref, basis_label = _season_ref(K, b), "같은 계절"
    basis_short = re.split(r"[(—]", basis_label)[0].strip()
    out = {"bid": bid, "b": b, "sb": sb, "g": g, "ref": ref, "basis": basis_short, "basis_label": basis_label}

    # 이력 판정표 (평소와 다른 항목)
    try:
        T, _ = A.batch_dossier(bid, ST, basis)
    except Exception:
        T = pd.DataFrame()
    out["T"] = T
    # 솥 단위 기록 (정규철) — 이 배치(균긁기일 + 발이실)에 해당하는 솥
    try:
        L = A.loads_with_cycles() if loads is None else loads
    except Exception:
        L = pd.DataFrame()
    # 배치(방·입상일) ← 균긁기 기록 행(events) ← 기여한 입병일의 솥 전부(pots)
    P, EV = A.paengi_batch_pots(L, b["입상일"], b["발이실"]) if not L.empty and pd.notna(b.get("발이실")) else (pd.DataFrame(), pd.DataFrame())
    out["loads"], out["events"] = P, EV
    # 살균 사이클 + 1분 곡선
    CY = etl_retort.load_cycles()
    cyc, curves, pcurves = pd.DataFrame(), [], []
    if not CY.empty and not P.empty and "살균시작" in P:
        keys = P.dropna(subset=["살균시작"])[["unit", "살균시작", "입병일", "입병순번"]].drop_duplicates(["unit", "살균시작"])
        cyc = CY.merge(keys, left_on=["unit", "start"], right_on=["unit", "살균시작"], how="inner").drop(columns=["살균시작"]).sort_values(["입병일", "입병순번"])
    if cyc.empty and not CY.empty and pd.notna(g("살균일")):
        d0 = pd.Timestamp(g("살균일")).normalize()
        units = [f"{int(u)}호기" for u in pd.to_numeric(P["살균기NO"], errors="coerce").dropna().unique()] if not P.empty else []
        cyc = CY[(CY.date == d0) & (CY["구분"] == "배지") & (CY.unit.isin(units) if units else True)]
    if not cyc.empty:
        for _, r in cyc.iterrows():
            try:
                W1 = etl_retort.load_1min(units=[r.unit], d1=r.start.normalize(), d2=r.end.normalize())
                W1 = W1[(W1.ts >= r.start - pd.Timedelta(minutes=5)) & (W1.ts <= r.end + pd.Timedelta(minutes=5))]
                if len(W1):
                    curves.append((f"{r.unit} {r.start:%m/%d %H:%M}", W1.ts, W1["온도"], r.start))
                    if "압력" in W1:
                        pcurves.append((f"{r.unit} {r.start:%m/%d %H:%M}", W1.ts, W1["압력"], r.start))
            except Exception:
                pass
    if not cyc.empty:
        try:                                         # 2차 유지 압력·스팀밸브 + 같은 솥 평소값 (v1.24.6)
            H = hold2_stats()
            cyc = cyc.drop(columns=[c for c in ("2차압력", "2차스팀") if c in cyc]).merge(H[["unit", "start", "2차압력", "2차스팀"]], on=["unit", "start"], how="left")
            refs = [hold2_ref(H, r.unit, r.start) or (np.nan,) * 4 for _, r in cyc.iterrows()]
            cyc["평소2차압력"], cyc["평소2차온도"], cyc["평소2차스팀"], cyc["평소n"] = zip(*refs)
        except Exception:
            pass
    out["cycles"], out["curves"], out["pcurves"] = cyc, curves, pcurves
    # 냉각실 (일별) — 이 배치 솥의 살균일(저녁 입고)과 접종일(아침 출고)만. 입병일이 둘이면 두 쌍 모두
    cool = pd.DataFrame()
    pairs = []
    if not P.empty and "접종일" in P:
        for d_in, grp in P.dropna(subset=["입병일"]).groupby("입병일"):
            d_in = pd.Timestamp(d_in).normalize()
            d_out = grp["접종일"].dropna()
            d_out = pd.Timestamp(d_out.iloc[0]).normalize() if len(d_out) else d_in + pd.Timedelta(days=1)
            pairs.append((d_in, d_out))
    elif pd.notna(g("살균일")):
        d0 = pd.Timestamp(g("살균일")).normalize()
        pairs.append((d0, pd.Timestamp(b["접종일"]).normalize() if pd.notna(b.get("접종일")) else d0 + pd.Timedelta(days=1)))
    out["cool_pairs"] = pairs
    if not env.empty and pairs:
        ins = {a for a, _ in pairs}; outs = {c for _, c in pairs}
        days = sorted(ins | outs)
        cool = env[env.room.isin(["냉각1실", "냉각실2"]) & env.date.isin(days)][["room", "date", "temp_min", "temp", "temp_max", "hum"]].sort_values(["room", "date"]).copy()
        cool["구분"] = cool["date"].map(lambda d: "접종 아침 → 저녁 재입고" if (d in ins and d in outs) else ("저녁 입고" if d in ins else "아침 접종 출고"))
    out["cool"] = cool
    try:
        wx = A.weather_daily()
        out["rain"] = wx[wx["강수mm"] >= C.RAIN_MARK_MM][["date", "강수mm"]] if not wx.empty else pd.DataFrame(columns=["date", "강수mm"])
    except Exception:
        out["rain"] = pd.DataFrame(columns=["date", "강수mm"])
    try:
        out["cooling"] = A.batch_cooling(pairs)          # [(쌍, 1분표, 사건표, 아침온도)]
    except Exception:
        out["cooling"] = []
    try:
        out["delays"] = A.sterilization_delays(P, CY=CY) if not P.empty else pd.DataFrame()
    except Exception:
        out["delays"] = pd.DataFrame()
    # 종균 (액탱크)
    S = pd.DataFrame()
    try:
        S0 = A.tank_batches()
        codes = set()
        for c in (g("종균코드"), g("종균_솥")):
            if c and str(c) not in ("nan", "None"):
                codes |= {x.strip() for x in str(c).split(",")}
        if not S0.empty and codes:
            S = S0[S0["코드"].astype(str).isin(codes)]
    except Exception:
        pass
    out["spawn"] = S
    # 접종 (그날)
    J = etl_logs.load("cult_inoc")
    out["inoc"] = J[(J["품목"] == "팽이") & (pd.to_datetime(J["일자"]) == b["접종일"])] if not J.empty and pd.notna(b.get("접종일")) else pd.DataFrame()
    # 균긁기·권지 원본
    SC = etl_logs.load("cult_scrape_paengi")
    out["scrape"] = SC[(pd.to_datetime(SC["긁기일"]) == b["입상일"]) & (pd.to_numeric(SC["방"], errors="coerce") == b["발이실"])] if not SC.empty else pd.DataFrame()
    KW = etl_logs.load("cult_kwonji")
    out["kwonji"] = KW[(pd.to_datetime(KW["권지일"]) == b["권지일"]) & (pd.to_numeric(KW["생육실"], errors="coerce") == b["생육실"])] if not KW.empty else pd.DataFrame()
    # 환경 (배치×일차)
    try:
        PE = A.paengi_batch_env(K, env) if PE is None else PE
        out["PE"] = PE[PE.bid == bid]
        # 냉각기 가동률: 이 배치의 단계 평균을 비교 배치들(기본 같은 계절)과 견준다 (단계별 배치 평균의 분포)
        cref = {}
        if "cool" in PE:
            same = PE[PE.bid.isin(set(ref.bid) | {bid})] if len(ref) and "bid" in ref else PE
            for ph in ("발이실", "생육실"):
                mine = out["PE"][out["PE"].phase == ph]["cool"].dropna()
                others = same[(same.phase == ph) & (same.bid != bid)].groupby("bid")["cool"].mean().dropna()
                daily = same[same.phase == ph]["cool"].dropna()
                cref[ph] = {"mine": float(mine.mean()) if len(mine) else np.nan, "days": int(len(mine)),
                            "ref_med": float(others.median()) if len(others) >= 5 else np.nan, "ref_p90": float(others.quantile(0.9)) if len(others) >= 5 else np.nan,
                            "rank": float((others < mine.mean()).mean() * 100) if len(others) >= 5 and len(mine) else np.nan, "n": int(len(others)),
                            "hi_days": int((mine > max(0.9, float(daily.quantile(0.95)) if len(daily) >= 20 else 0.9)).sum()) if len(mine) else 0}
        out["cool_ref"] = cref
    except Exception:
        out["PE"] = pd.DataFrame(); out["cool_ref"] = {}
    # 수확·QC
    H = etl_logs.load("paengi_harvest"); Q = etl_logs.load("paengi_qc"); D = etl_logs.load("paengi_daily")
    hv = pd.DataFrame()
    if not H.empty and pd.notna(g("수확시작")):
        s, e = pd.Timestamp(g("수확시작")), pd.Timestamp(g("수확종료")) if pd.notna(g("수확종료")) else pd.Timestamp(g("수확시작"))
        hv = H[(H.date >= s) & (H.date <= e)].copy()
        room = int(b["생육실"])
        hv = hv[hv["harvest_rooms"].astype(str).str.split(",").apply(lambda xs: any(x.strip().lstrip("0") == str(room) for x in xs if x.strip()))]
        if not D.empty:
            hv = hv.merge(D[["date", "total_kg"]], on="date", how="left")
    out["harvest"] = hv
    qc = pd.DataFrame()
    if not Q.empty and pd.notna(g("수확시작")):
        s = pd.Timestamp(g("수확시작")) - pd.Timedelta(days=3); e = (pd.Timestamp(g("수확종료")) if pd.notna(g("수확종료")) else pd.Timestamp(g("수확시작"))) + pd.Timedelta(days=1)
        qc = Q[(Q.date >= s) & (Q.date <= e) & (pd.to_numeric(Q["room"], errors="coerce") == b["생육실"])].sort_values("date")
    out["qc"] = qc
    # 원재료 (입병 달 vs 앞뒤 6개월)
    ipb = g("입병일") if pd.notna(g("입병일")) else g("살균일")
    month = pd.Timestamp(ipb).strftime("%Y-%m") if pd.notna(ipb) else None
    out["month"] = month
    out["materials"] = _materials_check(month) if month else None
    # 이 배치가 거친 방·장비의 수리 기록 (v1.23.0, 기계수리.xlsx)
    try:
        keys = ("살균일", "접종일", "입상일", "권지일", "수확시작", "수확종료", "발이실", "생육실", "살균솥", "살균기", "입병일")
        out["repairs"] = A.batch_repairs({k: g(k) for k in keys})
    except Exception:
        out["repairs"] = pd.DataFrame()
    return out


def _materials_check(month):
    """입병 달의 팽이 배지 구성이 앞뒤 6개월과 다른지. 반환 {"txt", "per", "n", "unusual": [(품목, 이달%, 평소%)], "new": [...], "gone": [...]}"""
    M = etl_logs.load("materials")
    if M.empty or "month" not in M:
        return None
    kg = "팽이_kg"
    M = M[pd.to_numeric(M[kg], errors="coerce") > 0].copy(); M[kg] = pd.to_numeric(M[kg], errors="coerce")
    if month not in set(M["month"]):
        return None
    tot = M.groupby("month")[kg].transform("sum"); M["pct"] = 100 * M[kg] / tot
    this = M[M["month"] == month].set_index("item")["pct"]
    ms = sorted(M["month"].unique()); i = ms.index(month)
    around = [m for m in ms[max(0, i - 6):i] + ms[i + 1:i + 4] if m != month]
    ref = M[M["month"].isin(around)].groupby("item")["pct"].median() if around else pd.Series(dtype=float)
    unusual, new, gone = [], [], []
    for it, p in this.items():
        if it not in ref.index:
            new.append(it)
        elif abs(p - ref[it]) >= 3:
            unusual.append((it, p, ref[it]))
    for it in ref.index:
        if it not in this.index and ref[it] >= 1:
            gone.append(it)
    txt, per, n = A.batch_materials(month, "팽이")
    return {"txt": txt, "per": per, "n": n, "unusual": unusual, "new": new, "gone": gone, "ref_n": len(around)}


# ── 이슈 뽑기 ─────────────────────────────────────────────
def _cool_issue(G, ph, room):
    """냉각기 가동률이 같은 계절 같은 단계 배치들보다 유독 높으면 이슈 문장. (상위 10% 안이면서 중앙값의 1.3배 초과 / 참고: 거의 쉬지 않고 돈 날 3일 이상)"""
    c = (G.get("cool_ref") or {}).get(ph)
    if not c or pd.isna(c.get("mine")):
        return []
    out = []
    pct = f"{100 * c['mine']:.0f}%"
    ref = f" ({G.get('basis', '같은 계절')} {ph} 배치 중앙값 {100 * c['ref_med']:.0f}%, 상위 {max(1, round(100 - c['rank'])):.0f}%)" if pd.notna(c.get("ref_med")) else ""
    if pd.notna(c.get("ref_p90")) and c["mine"] > c["ref_p90"] and c["mine"] > 1.3 * c["ref_med"]:
        out.append(("이슈", f"{room} 냉각기 가동률 {pct}{ref}. 유독 높음."))
    elif c.get("hi_days", 0) >= 3:
        out.append(("참고", f"{room} 냉각기가 거의 쉬지 않고 돈 날 {c['hi_days']}일 (체류 {c['days']}일 중, 가동률 평균 {pct}{ref})"))
    return out


def _dossier_issues(T, stages, label="같은 계절"):
    """배치 이력 판정표에서 해당 단계의 '평소와 다름' 항목을 문장으로. label: 비교 기준 이름"""
    if T is None or T.empty:
        return []
    x = T[T["단계"].isin(stages) & T["판정"].isin(["나쁨", "주의", "평소와 다름"])]
    out = []
    for _, r in x.iterrows():
        u = r.get("단위") or ""
        lvl = "이슈" if r["판정"] in ("나쁨", "주의") else "참고"
        out.append((lvl, f"{r['항목']} {_f(r['값'], 2)}{u} — {label} 평균 {_f(r['평균'], 2)}{u} ({'+' if r['차이'] >= 0 else ''}{_f(r['차이'], 2)}, z {r['z']:+.1f}) → {r['판정']}"))
    return out


def _repair_notes(G, stage):
    """그 단계에서 이 배치가 거친 방·장비의 수리 기록 → [('참고', 문장)]. 배치의 이슈 개수에는 넣지 않는다(참고용)."""
    R = G.get("repairs")
    if R is None or len(R) == 0:
        return []
    return [("참고", f"수리 기록 {r['날짜']:%m/%d}({r['시점']}) {r['장소']} — {r['내용']}" + (f" · {r['업체']}" if r["업체"] else ""))
            for _, r in R[R["단계"] == stage].iterrows()]


# ── 문서 만들기 ──────────────────────────────────────────
def compose(doc, G):
    """모아 둔 자료(G)를 doc(Doc=PDF, StDoc=화면)에 공정 순서대로 쓴다. 반환: 이슈 목록"""
    bid = G["bid"]
    b, g, T, ref = G["b"], G["g"], G["T"], G["ref"]
    lab = G.get("basis", "같은 계절")          # 이슈 판정 비교 기준 (배치 이력서의 '비교 기준')
    made = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    all_issues = []

    # ── 머리 ──
    grade = _tx(g("종합평가")); score = g("종합점수"); top = g("계절상위%"); qcb = _tx(g("QC판정"))
    ipb = g("입병일") if pd.notna(g("입병일")) else g("살균일")
    doc.h1(f"팽이 배치 {bid}",
           f"{_d(ipb if pd.notna(ipb) else b['입상일'], '%Y년')} · 발이실{int(b['발이실']) if pd.notna(b.get('발이실')) else '—'} → 생육실{int(b['생육실'])} · {_tx(b.get('season'))} · 종합평가 {grade}"
           + (f" (같은 계절 상위 {top:.0f}% — {_tx(g('평가근거'))})" if pd.notna(top) else "")
           + (f" · QC 절대 판정 {qcb}" if qcb != "—" else "") + f" · 작성 {made}")
    doc.text(f"이슈 판정의 비교 기준: {G.get('basis_label', '같은 계절')} 수확 완료 배치 {len(ref)}개 — 평균·순위는 이 배치들과 견준 값입니다.", fs=7.5, color=GRAY)
    Pd = G["loads"]
    def _dates(xs):
        xs = [pd.Timestamp(x) for x in sorted(pd.Series(xs).dropna().unique())]
        if not xs:
            return "—"
        out = [xs[0].strftime("%m/%d")]
        for x in xs[1:]:
            out.append(x.strftime("%d") if x.month == xs[0].month else x.strftime("%m/%d"))
        return "·".join(out)
    ipb_txt = _dates(Pd["입병일"]) if len(Pd) else _d(ipb)
    ino_txt = _dates(Pd["접종일"]) if len(Pd) else _d(b.get("접종일"))
    tl = [("입병", ipb_txt), ("살균", ipb_txt), ("접종", ino_txt), ("균긁기·입상", _d(b["입상일"])), ("권지", _d(b["권지일"])),
          ("수확", _d(g("수확시작"))), ("수확 종료", _d(g("수확종료")))]
    doc.kv(tl, cols=7, fs=9)
    doc.kv([("입상 후 수확까지", _f(g("총일수"), 0, "일")), ("배양", _f(g("배양일수"), 0, "일")), ("발이실 체류", _f(g("발이실체류"), 0, "일")),
            ("생육실 체류", _f(g("생육실체류"), 0, "일")), ("병당중량", _f(g("병당중량_표시"), 1, "g")), ("QC", _f(g("QC"), 1, "점")),
            ("긁기 불량", _f(g("긁기불량률%"), 2, "%")), ("권지 불량", _f(g("권지불량률%"), 2, "%")), ("실수확", _f(g("실수확병수"), 0, "병"))], cols=5)

    # ── 1. 원재료 ──
    mt = G["materials"]
    doc.h2("1", "원재료", f"입병 달 {G['month'] or '—'} 기준 (배치 단위 기록 없음)")
    if mt is None:
        doc.note("원재료입고.xlsx에 이 달 팽이 자료가 없습니다.")
    else:
        iss = [("이슈", f"{it}: 이달 {p:.0f}% vs 평소 {q:.0f}% ({p - q:+.0f}%p)") for it, p, q in mt["unusual"]]
        iss += [("이슈", f"평소에 없던 재료 사용: {', '.join(mt['new'])}")] if mt["new"] else []
        iss += [("이슈", f"평소 쓰던 재료가 빠짐: {', '.join(mt['gone'])}")] if mt["gone"] else []
        if iss:
            doc.issues(iss); all_issues += [("원재료", s_) for _, s_ in iss]
            doc.text(f"이달 구성: {mt['txt']}", fs=8, color=INK2)
        else:
            doc.note(f"평소와 같은 구성 (앞뒤 {mt['ref_n']}개월과 비교) — {mt['txt']}" + (f" · 병당 재료비 {mt['per']:,.0f}원" if pd.notna(mt['per']) else ""), kind="ok")

    # ── 2. 입병 ──
    P, EV = G["loads"], G["events"]
    ROOM = f"발이실{int(b['발이실'])}" if pd.notna(b.get("발이실")) else "발이실"
    days = sorted(P["입병일"].dropna().unique()) if len(P) else []
    doc.h2("2", "입병", f"{' · '.join(_d(d) for d in days)} · 솥 {len(P)}개" if len(P) else _d(ipb, "%Y-%m-%d"))
    if len(P):
        # 이 발이실의 병이 어느 날 입병분에서 왔는지 — 날짜별 한 줄
        lines = []
        for d in days:
            x = P[P["입병일"] == d]
            lines.append(f"{_d(d)} 입병 {len(x)}솥 {x['살균수량'].sum():,.0f}병 → 접종 {x['접종수량'].sum():,.0f}병(LOSS {x['접종LOSS'].sum():,.0f}) → "
                         f"{ROOM} 입상 {x['기여병수'].iloc[0]:,.0f}병 (그날 입병분의 {100 * x['기여비율'].iloc[0]:.0f}%)")
        doc.note("\n".join(lines) + f"\n{ROOM} 입상 합계 {EV['입상수량'].sum():,.0f}병 (배양소관리 입상량 {_f(g('입상량'), 0)}병)", kind="note")
        t = pd.DataFrame({
            "입병일": P["입병일"].map(lambda x: _d(x)), "순번": P["입병순번"].map(lambda x: _f(x, 0)) if "입병순번" in P else "",
            "혼합기": P["혼합기"].map(lambda x: _f(x, 0)), "살균기": P["살균기NO"].map(lambda x: _f(x, 0)),
            "시작~종료": [f"{_hm(a)}~{_hm(c)}" for a, c in zip(P["입병시작"], P["입병종료"])],
            "병수": P["살균수량"].map(lambda x: _f(x, 0)), "pH": [f"{_f(a, 2)} ({_f(lo, 1)}~{_f(hi, 1)})" for a, lo, hi in zip(P["pH"], P["pH_min"], P["pH_max"])],
            "수분%": P["수분"].map(lambda x: _f(x, 1)), "중량/상자": P["중량상자"].map(lambda x: _f(x, 2)), "충진(h)": P["충진시간"].map(lambda x: _f(x, 2)),
            "품온℃": P["배지품온"].map(lambda x: _f(x, 1)), "함량g": P["함량"].map(lambda x: _f(x, 0)),
        })
        doc.table(t, col_w=[0.07, 0.05, 0.06, 0.06, 0.12, 0.08, 0.14, 0.07, 0.08, 0.08, 0.08, 0.07], fs=7.2)
        iss = _dossier_issues(T, ["배지"], lab)
        # 솥 사이 편차: pH·수분이 솥마다 크게 다르면 짚는다
        for col, lbl, tol, nd in (("pH", "배지 pH", 0.25, 2), ("수분", "배지 수분", 1.5, 1), ("배지품온", "입병 시 품온", 4, 1)):
            v = pd.to_numeric(P[col], errors="coerce").dropna()
            if len(v) >= 2 and v.max() - v.min() > tol:
                iss.append(("참고", f"{lbl}가 솥마다 다름: {_f(v.min(), nd)} ~ {_f(v.max(), nd)}"))
        doc.issues(iss + _repair_notes(G, "입병")); all_issues += [("입병", s_) for _, s_ in iss]
    else:
        doc.note("이 배치에 해당하는 솥 단위 입병 기록(정규철 재배관리)을 찾지 못했습니다.")
        if _repair_notes(G, "입병"):
            doc.issues(_repair_notes(G, "입병"))

    # ── 3. 살균 ──
    cyc, curves, pcurves = G["cycles"], G["curves"], G.get("pcurves") or []
    sp1, sp2 = (P["설정1차온도"].dropna().iloc[0] if len(P) and P["설정1차온도"].notna().any() else None,
                P["설정2차온도"].dropna().iloc[0] if len(P) and P["설정2차온도"].notna().any() else None)
    doc.h2("3", "살균", f"{' · '.join(_d(d) for d in days)} · 사이클 {len(cyc)}개" if len(cyc) else _d(g("살균일"), "%Y-%m-%d"), keep=1.2)
    if len(P) and "설정1차온도" in P:
        doc.kv([("설정 1차", f"{_f(sp1, 0, '℃')} · {_f(P['설정1차분'].dropna().iloc[0] if P['설정1차분'].notna().any() else np.nan, 0, '분')}"),
                ("설정 2차", f"{_f(sp2, 0, '℃')} · {_f(P['설정2차분'].dropna().iloc[0] if P['설정2차분'].notna().any() else np.nan, 0, '분')}"),
                ("살균기", ", ".join(sorted({_f(x, 0) for x in P['살균기NO'].dropna()})) + "호기"),
                ("살균솥 기록과 짝지어진 솥", f"{len(cyc)} / {len(P)}")], cols=4)
    if len(cyc):
        t = pd.DataFrame({
            "입병일": cyc["입병일"].map(lambda x: _d(x)) if "입병일" in cyc else "", "순번": cyc["입병순번"].map(lambda x: _f(x, 0)) if "입병순번" in cyc else "",
            "살균기": cyc["unit"], "시작": cyc["start"].dt.strftime("%H:%M"), "종료": cyc["end"].dt.strftime("%H:%M"),
            "승온": cyc["승온분"].map(lambda x: _f(x, 0, "분")), "1차 유지": [f"{_f(a, 1)}℃ {_f(c, 0)}분" for a, c in zip(cyc["1차유지온도"], cyc["1차유지분"])],
            "2차 유지": [f"{_f(a, 1)}℃ {_f(c, 0)}분" for a, c in zip(cyc["2차유지온도"], cyc["2차유지분"])],
            "2차 압력": cyc["2차압력"].map(lambda x: _f(x, 0, "kPa")) if "2차압력" in cyc else "",
            "최고": cyc["최고온도"].map(lambda x: _f(x, 1, "℃")), "F0": cyc["F0"].map(lambda x: _f(x, 1)), "냉각": cyc["냉각분"].map(lambda x: _f(x, 0, "분")),
            "총": cyc["총가동분"].map(lambda x: _f(x, 0, "분")), "중단": cyc["중단횟수"].map(lambda x: _f(x, 0)),
        })
        # 패킹·압력 이상이 의심되는 사이클 → '이상' 열 + 빨간 글씨 행
        sp2m = P["설정2차분"].dropna().iloc[0] if len(P) and "설정2차분" in P and P["설정2차분"].notna().any() else None
        DL = G.get("delays", pd.DataFrame())
        tags, red = [], set()
        for i, (_, r) in enumerate(cyc.reset_index(drop=True).iterrows()):
            tg = []
            if pd.notna(r.get("중단횟수")) and r["중단횟수"] > 0: tg.append("중단·재가열")
            if r.get("완료") is False: tg.append("미완료")
            if sp2 is not None and pd.notna(r.get("2차유지온도")) and r["2차유지온도"] < sp2 - 1.5:
                short = hold2_diag(r)[0]
                tg.append("2차 온도 낮음" + (f"({'압력도 낮음 → 설정 의심' if short == SUSPECT else short})" if short else ""))   # 이상 칸은 v1.24.6 그대로
            if sp2m is not None and pd.notna(r.get("2차유지분")) and r["2차유지분"] < sp2m - 5: tg.append("2차 유지 짧음")
            if len(DL):
                d_ = DL[(DL["입병일"] == r.get("입병일")) & (DL["입병순번"] == r.get("입병순번"))]
                if len(d_):
                    if d_.iloc[0]["승온이슈"]: tg.append("승온 지연")
                    if d_.iloc[0]["대기이슈"]: tg.append("살균 대기")
            tags.append(" · ".join(tg) if tg else "")
            if tg: red.add(i)
        t["이상"] = tags
        doc.table(t, col_w=[0.05, 0.035, 0.05, 0.045, 0.045, 0.04, 0.10, 0.10, 0.06, 0.06, 0.045, 0.045, 0.05, 0.035, 0.24], fs=7.0, red_rows=red)
        iss = []
        for _, r in cyc.iterrows():
            tag = f"{_d(r.get('입병일'))} {r.unit} {r.start:%H:%M}"
            if pd.notna(r.get("중단횟수")) and r["중단횟수"] > 0:
                iss.append(("이슈", f"{tag} 사이클에서 중단 {int(r['중단횟수'])}회"))
            if sp2 is not None and pd.notna(r.get("2차유지온도")) and r["2차유지온도"] < sp2 - 1.5:
                dg = hold2_diag(r)
                iss.append(("이슈", f"{tag} 2차 유지온도 {r['2차유지온도']:.1f}℃ — 설정 {sp2:.0f}℃보다 낮음. "
                                  + (f"압력도 {r['2차압력']:.0f}kPa로 함께 낮음 → 위 '임의 설정 변경 의심' 상자 참고" if dg[0] == SUSPECT else dg[1])))
            if sp2m is not None and pd.notna(r.get("2차유지분")) and r["2차유지분"] < sp2m - 5:
                iss.append(("이슈", f"{tag} 2차 유지 {r['2차유지분']:.0f}분 — 설정 {sp2m:.0f}분보다 짧음"))
            if r.get("완료") is False:
                iss.append(("이슈", f"{tag} 사이클이 정상 완료로 기록되지 않음"))
        DL = G.get("delays", pd.DataFrame())
        for _, r in (DL.iterrows() if len(DL) else []):
            tag = f"{_d(r['입병일'])} 순번{_f(r['입병순번'], 0)} {r['unit'] or ''}"
            if r["대기이슈"]:
                iss.append(("이슈", f"{tag}: 입병이 {_hm2(r['입병종료'])}에 완료 후 살균 {_hm2(r['살균시작'])} 시작 — {r['대기분'] / 60:.1f}시간 대기 (평소 10분 안). 살균 대기 지연."))
            if r["승온이슈"]:
                iss.append(("이슈", f"{tag}: 100℃까지 {r['승온분']:.0f}분 — 평소 {r['승온평소']:.0f}분보다 {r['승온분'] - r['승온평소']:.0f}분 늦음. 승온 지연(밀폐·압력 의심)."))
        refF = ref["살균F0"] if "살균F0" in ref else pd.Series(dtype=float)
        for _, r in cyc.iterrows():
            pr = _pct_rank(refF, r.get("F0"), higher_is_bad=False)
            if pd.notna(pr) and pr >= 90:
                iss.append(("참고", f"{_d(r.get('입병일'))} {r.unit} {r.start:%H:%M} F0 {r['F0']:.1f} — {lab} 배치 중 하위 {max(1, round(100 - pr))}%"))
        if len(P) and len(cyc) < len(P):
            iss.append(("참고", f"솥 {len(P)}개 중 {len(P) - len(cyc)}개는 살균솥 기록과 짝을 못 지음 (살균솥 PC 공유가 꺼져 있었거나 시각이 안 맞음)"))
        iss += _dossier_issues(T, ["살균"], lab)
        # 임의 설정 변경 의심은 이슈 목록과 따로 빨간 상자로 (v1.24.7)
        sus = setting_suspects(G)
        if sus:
            doc.note("⚠ 임의 설정 변경 의심 — 2차 유지 설정을 기록 없이 낮춘 것으로 보입니다\n" + "\n".join(
                f"· {_d(r.get('입병일'))} 순번{_f(r.get('입병순번'), 0)} {r.unit} {r.start:%H:%M}: 재배관리 기록 2차 {sp2:.0f}℃인데 솥은 "
                f"{r['2차유지온도']:.1f}℃·{r['2차압력']:.0f}kPa(평소 {r['평소2차온도']:.1f}℃·{r['평소2차압력']:.0f}kPa)에 맞춰 운전됨. "
                f"스팀밸브는 평소대로 여닫음(열림 {r['2차스팀']:.0%}, 평소 {r['평소2차스팀']:.0%})." for r in sus)
                + "\n→ 살균기 조작판의 2차 압력·온도 설정과, 그날 누가 왜 바꿨는지 확인하세요. (2차 유지 설정값은 살균솥 기록 파일에 남지 않음)", kind="alert")
        doc.issues(iss + _repair_notes(G, "살균")); all_issues += [("살균", s_) for _, s_ in iss]
        # 곡선: 입병일별 한 장 (하루 4솥이 겹쳐 보이게)
        for d in days:
            cv = [c for c in curves if pd.Timestamp(c[3]).normalize() == pd.Timestamp(d).normalize()]
            if cv:
                doc.chart(lambda fig, box, cv=cv: _retort_curves(fig, box, cv, sp1, sp2), h=1.9, title=f"{_d(d)} 입병분 살균 온도 곡선 (솥별, 1분 기록)",
                          alt=lambda cv=cv: _alt_retort(cv, sp1, sp2))
            # 2차 유지온도가 낮은 솥이 있는 날은 압력 곡선도 (평소 2차 유지 압력 기준선) — v1.24.6
            low = cyc[(cyc["start"].dt.normalize() == pd.Timestamp(d).normalize()) & (cyc["2차유지온도"] < sp2 - 1.5)] if sp2 is not None and len(cyc) else cyc.iloc[0:0]
            pv = [c for c in pcurves if pd.Timestamp(c[3]).normalize() == pd.Timestamp(d).normalize()]
            if len(low) and pv:
                pr = low["평소2차압력"].dropna() if "평소2차압력" in low else pd.Series(dtype=float)
                ln = [(float(pr.iloc[0]), f"평소 2차 유지 압력 {pr.iloc[0]:.0f}kPa ({low.iloc[0]['unit']})")] if len(pr) else []
                doc.chart(lambda fig, box, pv=pv, ln=ln: _retort_curves(fig, box, pv, ylab="압력(kPa)", base=0, lines=ln), h=1.7,
                          title=f"{_d(d)} 입병분 살균 압력 곡선 (2차 유지온도 낮은 솥이 있어 함께 표시)",
                          alt=lambda pv=pv, ln=ln: _alt_retort(pv, ylab="압력(kPa)", base=0, lines=ln))
    else:
        doc.note("살균솥 기록에서 이 배치의 사이클을 찾지 못했습니다. (살균솥 PC 공유가 꺼져 있었거나 그날 기록이 없음)")
        if _repair_notes(G, "살균"):
            doc.issues(_repair_notes(G, "살균"))

    # ── 4. 냉각 ──
    cool = G["cool"]
    pairs = G.get("cool_pairs") or []
    if pairs:
        sub = " · ".join(f"{_d(a)} 저녁 입고 → {_d(c)} 아침 접종" for a, c in pairs)
    else:
        sub = f"{_d(g('살균일'))} 저녁 입고 → {_d(b.get('접종일'))} 접종"
    doc.h2("4", "냉각", sub, keep=1.0)
    CL = G.get("cooling") or []
    if CL:
        allW = pd.concat([x[1] for x in CL]) if any(len(x[1]) for x in CL) else pd.DataFrame()
        mornings = {k: v["아침"] for x in CL for k, v in x[3].items()}
        kvs = [("입고 시 최고", _f(allW.temp.max() if len(allW) else np.nan, 0, "℃")),
               ("접종 아침 냉각실(06~08시)", " · ".join(f"{k} {_f(v, 1, '℃')}" for k, v in mornings.items())),
               ("습도", _f(g("냉각hum"), 0, "%"))]
    else:
        kvs = [("냉각실 평균", _f(g("냉각temp"), 1, "℃")), ("입고일 최고 → 접종일 최저", f"{_f(g('냉각temp_max'), 0, '℃')} → {_f(g('냉각temp_min'), 1, '℃')}"), ("습도", _f(g("냉각hum"), 0, "%"))]
    kvs.append(("접종 시 배지온도 (솥 범위)", f"{_f(P['접종온도'].min(), 1)}~{_f(P['접종온도'].max(), 1)}℃" if len(P) and "접종온도" in P else _f(g("접종온도"), 1, "℃")))
    doc.kv(kvs, cols=4)
    iss_cool = []
    if CL:
        pots_all = [(t, f"{u} 완료") for t, u in zip(P["살균완료"], P["unit"])] if len(P) and "살균완료" in P else []
        for (a, c), Wm, ev, morning in CL:
            pots = [(t, l) for t, l in pots_all if pd.notna(t) and len(Wm) and Wm.ts.min() <= t <= Wm.ts.max()]
            if len(Wm):
                doc.chart(lambda fig, box, Wm=Wm, ev=ev, pots=pots, morning=morning: _cool_curve(fig, box, Wm, ev, pots, morning, G.get("rain")), h=1.9,
                          alt=lambda Wm=Wm, ev=ev, pots=pots: _alt_cool(Wm, ev, pots, G.get("rain")),
                          title=f"{_d(a)} 낮 → {_d(c)} 아침 냉각실 온도 (1분 기록) — 점선 세로줄: 살균 완료 시각, 화살표: 마지막 솥 최고점 → 20℃")
            if len(ev):
                t = pd.DataFrame({"냉각실": ev["room"], "입고(오름 시작)": ev["시작"].map(lambda x: x.strftime("%d일 %H:%M")), "솥": ev["솥수"].map(lambda x: _f(x, 0)),
                                  "최고": ev["최고온도"].map(lambda x: _f(x, 1, "℃")), "20℃ 도달": ev["도달"].map(lambda x: x.strftime("%d일 %H:%M")),
                                  "마지막 솥 최고→20℃": ev["소요분"].map(lambda m: f"{int(m) // 60}시간 {int(m) % 60:02d}분"),
                                  "평소(같은 방·비슷한 온도)": [f"{m / 60:.1f}시간 (75% {p / 60:.1f})" if pd.notna(m) else "—" for m, p in zip(ev["평소분"], ev["평소75분"])],
                                  "판정": ["느림" if x else "평소대로" for x in ev["느림"]]})
                doc.table(t, col_w=[0.1, 0.13, 0.05, 0.09, 0.13, 0.13, 0.22, 0.15], fs=7.2)
                for _, r in ev.iterrows():
                    if r["느림"]:
                        iss_cool.append(("이슈", f"{r.room} {r['시작']:%m/%d %H:%M} 입고분: {r.get('마지막최고', r['최고온도']):.0f}℃ → 20℃ {r['소요분'] / 60:.1f}시간 (평소 {r['평소분'] / 60:.1f}시간). 냉각 느림."))
                    elif not r["완료"]:
                        iss_cool.append(("이슈", f"{r.room} {r['시작']:%m/%d %H:%M} 입고분: 기록 안에서 20℃ 아래로 내려오지 않음"))
            else:
                doc.note("이 시간대 냉각실 기록에 뜨거운 솥이 들어온 흔적(온도 급상승)이 없습니다 — 다른 방을 썼거나 기록이 비어 있음")
            for room, mo in morning.items():
                mt, spv = mo["아침"], mo["설정"]
                if mo["과냉"]:
                    iss_cool.append(("이슈", f"{room}: 설정 {spv:.0f}℃보다 {A.COOL_UNDER:.0f}℃ 넘게 낮은 시간 {mo['과냉분'] // 60}시간 {mo['과냉분'] % 60:02d}분 "
                                           f"(최저 {mo['최저']:.1f}℃ {mo['최저시각']:%d일 %H:%M}). 과냉."))
                elif pd.notna(mt) and pd.notna(spv) and mt - spv > 3:
                    iss_cool.append(("참고", f"{_d(c)} 접종 아침(06~08시) {room} {mt:.1f}℃ — 설정 {spv:.0f}℃보다 {mt - spv:+.1f}℃ 높음"))
    if len(cool) and not CL:
        t = pd.DataFrame({"냉각실": cool["room"], "날짜": cool["date"].map(lambda x: _d(x)), "이 배치": cool["구분"] if "구분" in cool else "",
                          "최저": cool["temp_min"].map(lambda x: _f(x, 1, "℃")), "평균": cool["temp"].map(lambda x: _f(x, 1, "℃")),
                          "최고": cool["temp_max"].map(lambda x: _f(x, 1, "℃")), "습도": cool["hum"].map(lambda x: _f(x, 0, "%"))})
        doc.table(t, col_w=[0.12, 0.1, 0.24, 0.13, 0.13, 0.13, 0.15])
    iss = iss_cool + _dossier_issues(T, ["냉각"], lab)
    doc.issues(iss + _repair_notes(G, "냉각")); all_issues += [("냉각", s_) for _, s_ in iss]
    if CL:
        doc.text(f"냉각실은 방 배정 기록이 없어 두 방을 함께 보입니다. '평소'는 같은 방·같은 솥수·최고온도가 비슷한(±6℃) 최근 90일 입고분의 중앙값. "
                 f"느림 = 평소의 {A.COOL_SLOW_RATIO:g}배 이상이면서 {A.COOL_SLOW_ADD:g}분 넘게 늦은 경우. 과냉 = 설정보다 {A.COOL_UNDER:g}℃ 넘게 낮은 시간이 {A.COOL_UNDER_MIN}분 이상.", fs=7.5, color=GRAY)
    else:
        doc.text("냉각실은 방 배정 기록이 없어 냉각1실·냉각실2를 함께 보입니다. 값은 하루 집계라 입고일에는 입고 전 낮 시간이, 접종일에는 접종 뒤 오후(다음 솥 입고)가 섞여 있습니다 — "
             "입고일 '최고'가 뜨거운 솥이 들어온 온도, 접종일 '최저'가 접종 아침 온도에 가깝습니다. 저녁 입고 → 아침 접종 구간의 시간별 곡선은 아직 없습니다.", fs=7.5, color=GRAY)

    # ── 5. 접종 ──
    S = G["spawn"]
    idays = sorted(P["접종일"].dropna().unique()) if len(P) else []
    doc.h2("5", "접종", " · ".join(_d(d, "%Y-%m-%d") for d in idays) if idays else _d(b.get("접종일"), "%Y-%m-%d"), keep=1.0)
    if len(P):
        t = pd.DataFrame([{
            "접종일": _d(d), "입병일": _d(P[P["접종일"] == d]["입병일"].iloc[0]), "솥": len(P[P["접종일"] == d]),
            "접종 병수": _f(P[P["접종일"] == d]["접종수량"].sum(), 0), "LOSS": _f(P[P["접종일"] == d]["접종LOSS"].sum(), 0),
            "손실률": _f(100 * P[P["접종일"] == d]["접종LOSS"].sum() / max(P[P["접종일"] == d]["살균수량"].sum(), 1), 2, "%"),
            "배지온도": f"{_f(P[P['접종일'] == d]['접종온도'].min(), 1)}~{_f(P[P['접종일'] == d]['접종온도'].max(), 1)}℃",
            "종균": ", ".join(sorted({str(x) for x in P[P["접종일"] == d]["종균"].dropna()})) if "종균" in P else "—",
            f"{ROOM} 입상": _f(P[P["접종일"] == d]["기여병수"].iloc[0], 0),
        } for d in idays])
        doc.table(t, col_w=[0.08, 0.08, 0.05, 0.1, 0.07, 0.08, 0.12, 0.3, 0.12], fs=7.2, title="접종일별 (기여한 입병일의 솥 전부)")
        t2 = pd.DataFrame({"입병일": P["입병일"].map(lambda x: _d(x)), "순번": P["입병순번"].map(lambda x: _f(x, 0)) if "입병순번" in P else "",
                           "살균기": P["살균기NO"].map(lambda x: _f(x, 0)), "접종일": P["접종일"].map(lambda x: _d(x)),
                           "접종 병수": P["접종수량"].map(lambda x: _f(x, 0)), "LOSS": P["접종LOSS"].map(lambda x: _f(x, 0)),
                           "배지온도": P["접종온도"].map(lambda x: _f(x, 1, "℃")), "종균 코드": P["종균"].map(_tx) if "종균" in P else "—"})
        doc.table(t2, col_w=[0.1, 0.06, 0.08, 0.1, 0.12, 0.08, 0.12, 0.34], fs=7.2, title="솥별 접종")
    else:
        doc.kv([("그날 팽이 전체", f"{_f(g('접종량'), 0)}병 · 손실 {_f(g('접종손실률%'), 2, '%')}"), ("종균", _tx(g("종균코드")))], cols=2)
    if len(S):
        t = pd.DataFrame({"코드": S["코드"].map(_tx), "탱크": S["탱크NO"].map(lambda x: _f(x, 0)), "제조일": S["일시"].map(lambda x: _d(x)), "계통": S["계통"].map(_tx),
                          "세대": S["세대"].map(lambda x: _f(x, 0)), "pH": S["pH"].map(lambda x: _f(x, 2)), "액배양": S["액배양일"].map(lambda x: _f(x, 0, "일")),
                          "교반": S["교반"].map(lambda x: _tx(x, 14)), "유래": S["유래"].map(lambda x: _tx(x, 26))})
        doc.table(t, col_w=[0.17, 0.06, 0.08, 0.1, 0.06, 0.07, 0.08, 0.14, 0.24], title="종균(액탱크) 기록")
    iss = _dossier_issues(T, ["종균", "접종"], lab)
    if len(P):
        hot = P[pd.to_numeric(P["접종온도"], errors="coerce") > 20]
        for _, r in hot.iterrows():
            iss.append(("이슈", f"{_d(r['입병일'])} 순번{_f(r.get('입병순번'), 0)} 접종 시 배지온도 {r['접종온도']:.1f}℃ — 20℃ 초과"))
    doc.issues(iss + _repair_notes(G, "접종")); all_issues += [("접종", s_) for _, s_ in iss]

    # ── 6. 배양 ──
    PE = G["PE"]
    doc.h2("6", "배양", f"{_d(b.get('접종일'))} → {_d(b['입상일'])} · {_f(g('배양일수'), 0)}일", keep=1.2)
    doc.kv([("배양실 온도", _f(g("배양temp"), 1, "℃")), ("습도", _f(g("배양hum"), 0, "%")), ("CO2", _f(g("배양co2"), 0, "ppm")),
            ("바깥 기온", _f(g("배양외기"), 1, "℃")), ("비 온 날", _f(g("배양비일수"), 0, "일"))], cols=5)
    Pc = PE[PE.phase == "배양"] if len(PE) else pd.DataFrame()
    if len(Pc):
        doc.chart(lambda fig, box: _env_triplet(fig, box, Pc, xlab="입상일 기준 일차 (0 = 균긁기·입상)", rain=G.get("rain")), h=1.7, title="배양실(두 방 평균) 일별 — 실선 실측, 점선 설정",
                  alt=lambda: _alt_env_triplet(Pc, xlab="입상일 기준 일차 (0 = 균긁기·입상)", rain=G.get("rain")))
    iss = _dossier_issues(T, ["배양"], lab)
    doc.issues(iss + _repair_notes(G, "배양")); all_issues += [("배양", s_) for _, s_ in iss]

    # ── 7. 균긁기 ──
    doc.h2("7", "균긁기", f"{_d(b['입상일'], '%Y-%m-%d')} · 발이실{int(b['발이실']) if pd.notna(b.get('발이실')) else '—'} 입상", keep=1.0)
    doc.kv([("배양 출고 (배양소관리)", _f(g("배양출고"), 0, "병")), ("긁기 불량", f"{_f(g('긁기불량'), 0)}병 · {_f(g('긁기불량률%'), 2, '%')}"),
            ("입상량 (배양소관리)", _f(g("입상량"), 0, "병")), ("액탱크", _tx(g("탱크")))], cols=4)
    iss = []
    if len(EV):
        rows = []
        for _, r in EV.sort_values("입병일").iterrows():
            rows.append({"입병일": _d(r["입병일"]), "접종일": _d(r["접종일"]), "배양일수": _f((pd.Timestamp(b["입상일"]) - pd.Timestamp(r["접종일"])).days if pd.notna(r["접종일"]) else np.nan, 0, "일"),
                         "긁기 불량": _f(r["긁기불량"], 0), f"{ROOM} 입상": _f(r["입상수량"], 0), "비고": _tx(r.get("긁기비고"))})
        tot_in, tot_bad = EV["입상수량"].sum(), EV["긁기불량"].sum()
        rows.append({"입병일": "합계", "접종일": "", "배양일수": "", "긁기 불량": _f(tot_bad, 0), f"{ROOM} 입상": _f(tot_in, 0), "비고": ""})
        doc.table(pd.DataFrame(rows), col_w=[0.12, 0.12, 0.1, 0.12, 0.14, 0.4], title=f"입병일별 {ROOM} 입상 (정규철 솥 기록)")
        # 배양소관리와 대조
        chk = []
        if pd.notna(g("배양출고")):
            diff = (tot_in + tot_bad) - g("배양출고")
            chk.append(f"입상 {tot_in:,.0f} + 불량 {tot_bad:,.0f} = {tot_in + tot_bad:,.0f}병 vs 배양소관리 배양출고 {g('배양출고'):,.0f}병 → " + ("일치" if abs(diff) <= 5 else f"차이 {diff:+,.0f}병"))
            if abs(diff) > 5:
                iss.append(("이슈", f"균긁기 병수가 두 기록에서 다름 ({diff:+,.0f}병): 정규철 솥 기록 vs 배양소관리"))
        if pd.notna(g("입상량")):
            diff = tot_in - g("입상량")
            chk.append(f"{ROOM} 입상 {tot_in:,.0f}병 vs 배양소관리 입상량 {g('입상량'):,.0f}병 → " + ("일치" if abs(diff) <= 5 else f"차이 {diff:+,.0f}병"))
            if abs(diff) > 5:
                iss.append(("이슈", f"입상 병수가 두 기록에서 다름 ({diff:+,.0f}병)"))
        if chk:
            doc.note("\n".join(chk), kind="ok" if not any(l == "이슈" for l, _ in iss) else "note")
    pr = _pct_rank(ref["긁기불량률%"] if "긁기불량률%" in ref else pd.Series(dtype=float), g("긁기불량률%"))
    if pd.notna(pr) and pr >= 75:
        iss.append(("이슈" if pr >= 90 else "참고", f"긁기 불량률 {_f(g('긁기불량률%'), 2, '%')} — {lab} 배치 중 상위 {max(1, round(100 - pr))}% (나쁜 쪽)"))
    if len(EV):
        for _, r in EV.iterrows():
            for col, lbl in (("긁기비고", "긁기 비고"), ("상태", "상태")):
                if col in EV and _tx(r.get(col)) != "—":
                    iss.append(("참고", f"{lbl}: {_tx(r.get(col))} ({_d(r['입병일'])} 입병분)"))
    iss += _dossier_issues(T, ["균긁기"], lab)
    doc.issues(iss + _repair_notes(G, "균긁기")); all_issues += [("균긁기", s_) for _, s_ in iss]

    # ── 8. 발이실 ──
    doc.h2("8", "발이실", f"발이실{int(b['발이실']) if pd.notna(b.get('발이실')) else '—'} · {_d(b['입상일'])} → {_d(b['권지일'])} · {_f(g('발이실체류'), 0)}일", keep=1.9)
    doc.kv([("온도", _f(g("발이temp"), 1, "℃")), ("습도", _f(g("발이hum"), 0, "%")), ("CO2", _f(g("발이co2"), 0, "ppm")),
            ("억제 12~14일차", _f(g("억제12_14"), 1, "℃")), ("추종오차", _f(g("발이track"), 2, "℃")), ("바깥 기온 · 비", f"{_f(g('발이외기'), 1, '℃')} · {_f(g('발이비일수'), 0)}일"),
            ("냉각기 가동률", _f(100 * (G.get("cool_ref") or {}).get("발이실", {}).get("mine", np.nan), 0, "%"))], cols=7)
    Pb = PE[PE.phase == "발이실"] if len(PE) else pd.DataFrame()
    if len(Pb):
        doc.chart(lambda fig, box: _env_triplet(fig, box, Pb, marks=[(12, "억제")], xlab="입상 후 일차", rain=G.get("rain")), h=1.7, title="발이실 일별 — 실선 실측(띠: 최저~최고), 점선 설정",
                  alt=lambda: _alt_env_triplet(Pb, marks=[(12, "억제")], xlab="입상 후 일차", rain=G.get("rain")))
    iss = _dossier_issues(T, ["발이"], lab) + _cool_issue(G, "발이실", ROOM)
    if len(Pb):
        flat = (Pb["hum"].notna()) & ((Pb.get("hum_max", Pb["hum"]) - Pb.get("hum_min", Pb["hum"])).fillna(1) < 0.2) if "hum_max" in Pb else pd.Series(False, index=Pb.index)
        bad = int(Pb["err"].fillna(0).gt(0).sum()) if "err" in Pb else 0
        if bad:
            iss.append(("참고", f"컨트롤러 오류 기록이 있는 날 {bad}일"))
    doc.issues(iss + _repair_notes(G, "발이실")); all_issues += [("발이실", s_) for _, s_ in iss]

    # ── 9. 권지 ──
    doc.h2("9", "권지", f"{_d(b['권지일'], '%Y-%m-%d')} · 생육실{int(b['생육실'])} 입실", keep=0.9)
    doc.kv([("입상량 (배양소관리)", _f(g("입상량"), 0, "병")), ("권지 불량", f"{_f(g('불량'), 0)}병 · {_f(g('권지불량률%'), 2, '%')}"),
            ("권지 작업량", _f(g("권지작업량"), 0, "병")), ("발이실 체류", _f(g("발이실체류"), 0, "일"))], cols=4)
    iss = []
    if len(EV) and "권지수량" in EV:
        rows = [{"입병일": _d(r["입병일"]), "권지일": _d(r["권지일"]), "생육실": _f(r["생육실"], 0), "권지 불량": _f(r["권지불량"], 0), "권지 수량": _f(r["권지수량"], 0)}
                for _, r in EV.sort_values("입병일").iterrows()]
        tk, tb = EV["권지수량"].sum(), EV["권지불량"].sum()
        rows.append({"입병일": "합계", "권지일": "", "생육실": "", "권지 불량": _f(tb, 0), "권지 수량": _f(tk, 0)})
        doc.table(pd.DataFrame(rows), col_w=[0.2, 0.2, 0.2, 0.2, 0.2], title="입병일별 권지 (정규철 솥 기록)")
        chk = []
        if pd.notna(g("권지작업량")):
            diff = tk - g("권지작업량")
            chk.append(f"권지 수량 {tk:,.0f}병 vs 배양소관리 권지작업량 {g('권지작업량'):,.0f}병 → " + ("일치" if abs(diff) <= 5 else f"차이 {diff:+,.0f}병"))
            if abs(diff) > 5:
                iss.append(("이슈", f"권지 병수가 두 기록에서 다름 ({diff:+,.0f}병)"))
        if pd.notna(g("불량")):
            diff = tb - g("불량")
            chk.append(f"권지 불량 {tb:,.0f}병 vs 배양소관리 {g('불량'):,.0f}병 → " + ("일치" if abs(diff) <= 2 else f"차이 {diff:+,.0f}병"))
        if chk:
            doc.note("\n".join(chk), kind="ok" if not iss else "note")
    pr = _pct_rank(ref["권지불량률%"] if "권지불량률%" in ref else pd.Series(dtype=float), g("권지불량률%"))
    if pd.notna(pr) and pr >= 75:
        iss.append(("이슈" if pr >= 90 else "참고", f"권지 불량률 {_f(g('권지불량률%'), 2, '%')} — {lab} 배치 중 상위 {max(1, round(100 - pr))}% (나쁜 쪽)"))
    iss += _dossier_issues(T, ["권지"], lab)
    doc.issues(iss + _repair_notes(G, "권지")); all_issues += [("권지", s_) for _, s_ in iss]

    # ── 10. 생육실 ──
    doc.h2("10", "생육실", f"생육실{int(b['생육실'])} · {_d(b['권지일'])} → {_d(g('수확시작'))} · {_f(g('생육실체류'), 0)}일", keep=1.9)
    doc.kv([("온도", _f(g("생육temp"), 1, "℃")), ("습도", _f(g("생육hum"), 0, "%")), ("CO2", _f(g("생육co2"), 0, "ppm")),
            ("추종오차", _f(g("생육track"), 2, "℃")), ("바깥 기온 · 비", f"{_f(g('생육외기'), 1, '℃')} · {_f(g('생육비일수'), 0)}일"),
            ("냉각기 가동률", _f(100 * (G.get("cool_ref") or {}).get("생육실", {}).get("mine", np.nan), 0, "%"))], cols=6)
    Pg = PE[PE.phase == "생육실"] if len(PE) else pd.DataFrame()
    if len(Pg):
        doc.chart(lambda fig, box: _env_triplet(fig, box, Pg, xlab="입상 후 일차", rain=G.get("rain")), h=1.7, title="생육실 일별 — 실선 실측(띠: 최저~최고), 점선 설정",
                  alt=lambda: _alt_env_triplet(Pg, xlab="입상 후 일차", rain=G.get("rain")))
    iss = _dossier_issues(T, ["생육"], lab) + _cool_issue(G, "생육실", f"생육실{int(b['생육실'])}")
    doc.issues(iss + _repair_notes(G, "생육실")); all_issues += [("생육실", s_) for _, s_ in iss]

    # ── 11. 수확 ──
    hv, qc = G["harvest"], G["qc"]
    doc.h2("11", "수확", f"{_d(g('수확시작'), '%Y-%m-%d')} ~ {_d(g('수확종료'))} · {_f(g('수확일수'), 0)}일간 · 입상 후 {_f(g('총일수'), 0)}일", keep=1.2)
    doc.kv([("병당중량", f"{_f(g('병당중량_표시'), 1, 'g')} ({_tx(g('중량기준'))})"),
            ("QC", f"{_f(g('QC'), 1, '점')} ({_tx(g('QC종류'))}, 서식 {_tx(g('QC서식'))})" + (f" · 판정 {qcb}" if qcb != "—" else "")),
            ("실수확", _f(g("실수확병수"), 0, "병")), ("상등급 비중", _f(100 * g("상등급비중") if pd.notna(g("상등급비중")) else np.nan, 0, "%")),
            ("박스", _f(hv["boxes"].sum() if len(hv) and "boxes" in hv else np.nan, 0, "박스")), ("종합평가", f"{grade}" + (f" (상위 {top:.0f}%)" if pd.notna(top) else ""))], cols=6)
    if len(hv):
        t = pd.DataFrame({"날짜": hv["date"].map(lambda x: _d(x)), "수확방": hv["harvest_rooms"].map(_tx),
                          "수확 병수": hv["harvest_bottles"].map(lambda x: _f(x, 0)) if "harvest_bottles" in hv else "—",
                          "베이비": hv["baby_bottles"].map(lambda x: _f(x, 0)) if "baby_bottles" in hv else "—",
                          "실수확": hv["bottles"].map(lambda x: _f(x, 0)), "병당중량": hv["g_per_bottle"].map(lambda x: _f(x, 1, "g")),
                          "상등급": hv["grade_top"].map(lambda x: _f(100 * x if pd.notna(x) else np.nan, 0, "%")),
                          "박스": hv["boxes"].map(lambda x: _f(x, 0)) if "boxes" in hv else "—",
                          "그날 생산": hv["total_kg"].map(lambda x: _f(x, 0, "kg")) if "total_kg" in hv else "—"})
        doc.table(t, col_w=[0.09, 0.09, 0.12, 0.1, 0.11, 0.12, 0.1, 0.11, 0.16], title="수확 일별 (일일작업일지)")
        doc.text("수확방에 여러 방이 적힌 날은 그 방들의 합계입니다. 박스는 그날 포장 작업량(150g×34 기준 환산 값).", fs=7.5, color=GRAY)
    if len(qc):
        t = pd.DataFrame({"날짜": qc["date"].map(lambda x: _d(x)), "종류": qc["kind"].map(_tx), "일차": qc["day_n"].map(lambda x: _f(x, 0)),
                          "점수": qc["score"].map(lambda x: _f(x, 1)), "서식": qc["fmt"].map(_tx), "비고": qc["note"].map(lambda x: _tx(x, 40))})
        doc.table(t, col_w=[0.1, 0.12, 0.08, 0.1, 0.08, 0.52], title="QC 기록 (생육실 기준, 수확 전후)")
    iss = _dossier_issues(T, ["수확"], lab)
    if qcb == "미흡":
        iss.insert(0, ("이슈", f"QC {_f(g('QC'), 1)}점 — 품질 기준서 앵커로 '미흡' (79 미만: 편차 큼·키작은개체 수준 이하)"))
    elif qcb == "양호":
        iss.append(("참고", f"QC {_f(g('QC'), 1)}점 — 품질 기준서 앵커로 '양호' (85 이상: 모든 항목 다소 편차 이내)"))
    if len(qc) and "note" in qc:
        notes = [f"{_d(r['date'])} {_tx(r['note'])}" for _, r in qc.iterrows() if _tx(r.get("note")) != "—"]
        if notes:
            iss.append(("참고", "QC 비고: " + " / ".join(notes)))
    doc.issues(iss); all_issues += [("수확", s_) for _, s_ in iss]

    return all_issues


def build(bid, K=None, ST=None, env=None, PE=None, loads=None, G=None):
    """A4 PDF. 반환 {"pdf", "pngs"(쪽별 미리보기), "title", "issues", "pages"}"""
    _font()
    G = gather(bid, K, ST, env, PE, loads) if G is None else G
    if G is None:
        return None
    title = f"배치 보고서 {bid}"
    doc = Doc(title, f"{C.APP_NAME} · 작성 {dt.datetime.now():%Y-%m-%d %H:%M} · v{C.VERSION}")
    issues = compose(doc, G)
    pdf = doc.close()
    return {"pdf": pdf, "pngs": doc.pngs, "title": title, "issues": issues, "pages": doc.page}


def _md(s):
    """화면(마크다운)에 쓸 글: '1~3일 … 3~7일'처럼 물결표가 둘 있으면 그 사이가 취소선이 되므로 물결표를 그대로 보이게 한다"""
    return str(s).replace("~", "\\~")


class StDoc:
    """같은 내용을 스트림릿 화면에 보통 형태로. Doc 과 같은 메서드 이름을 갖는다 (compose 가 구분하지 않게)."""
    def __init__(self):
        import streamlit as st
        self.st = st

    def h1(self, s, sub=None):
        pass    # 화면에는 이미 배치 제목·종합평가가 위에 있다

    def h2(self, n, s, right=None, keep=0):
        self.st.markdown(f"<div style='margin:14px 0 4px 0;padding:6px 10px;background:#eef1ee;border-left:4px solid #2a78d6;border-radius:4px;display:flex;justify-content:space-between'>"
                         f"<b style='font-size:1.02rem'>{n}. {s}</b><span style='font-size:0.8rem;color:#52514e;align-self:center'>{right or ''}</span></div>", unsafe_allow_html=True)

    def kv(self, pairs, cols=3, fs=None):
        pairs = [(k, v) for k, v in pairs if v not in (None, "", "—")] or pairs
        for i in range(0, len(pairs), cols):
            cc = self.st.columns(cols)
            for c, (k, v) in zip(cc, pairs[i:i + cols]):
                c.markdown(f"<div style='font-size:0.72rem;color:#6b7160;line-height:1.1'>{k}</div><div style='font-size:0.95rem;font-weight:600;margin-bottom:6px'>{v}</div>", unsafe_allow_html=True)

    def table(self, df, col_w=None, fs=None, max_rows=40, title=None, zebra=True, red_rows=None):
        if df is None or len(df) == 0:
            return
        if title:
            self.st.markdown(f"<div style='font-size:0.85rem;font-weight:600;margin-top:4px'>{title}</div>", unsafe_allow_html=True)
        d = df.head(max_rows).reset_index(drop=True)
        if red_rows:
            rr = set(red_rows)
            d = d.style.apply(lambda row: ["color:#e34948;font-weight:600;background-color:#fdf0ef" if row.name in rr else "" for _ in row], axis=1)
        self.st.dataframe(d, hide_index=True, use_container_width=True, height=38 + 35 * min(len(df), max_rows) + 3)

    def chart(self, draw, h=2.1, title=None, alt=None):
        if title:
            self.st.markdown(f"<div style='font-size:0.85rem;font-weight:600;margin-top:4px'>{title}</div>", unsafe_allow_html=True)
        if alt is not None:                     # 화면은 움직이는 그래프(마우스를 올리면 값) — 실패하면 아래 그림으로
            try:
                charts = alt()
                if len(charts) == 1:
                    self.st.altair_chart(charts[0], use_container_width=True)
                else:
                    for c, ch in zip(self.st.columns(len(charts)), charts):
                        c.altair_chart(ch, use_container_width=True)
                return
            except Exception:
                pass
        fig = plt.figure(figsize=(10, max(2.2, h * 1.25)))
        fig.patch.set_facecolor("white")
        draw(fig, [0.05, 0.16, 0.93, 0.76])
        self.st.pyplot(fig, use_container_width=True); plt.close(fig)

    def issues(self, items, ok_text="특이사항 없음"):
        if not items:
            self.st.success(ok_text); return
        lines = [("▲ " if lvl == "이슈" else "• ") + _md(txt) for lvl, txt in items]
        (self.st.error if any(l == "이슈" for l, _ in items) else self.st.warning)("  \n".join(lines))

    def note(self, s, kind="note", fs=None):
        (self.st.success if kind == "ok" else self.st.error if kind == "alert" else self.st.info)(_md(s).replace("\n", "  \n"))

    def text(self, s, fs=None, color=None, bold=False, x=0.0, w=None, indent=0.0):
        self.st.caption(_md(s))

    def space(self, h=0.12):
        pass


def render_streamlit(bid, K=None, ST=None, env=None, PE=None, loads=None, G=None):
    """배치 탐색기용: 같은 내용을 화면에 보통 형태로 펼친다. 반환: 이슈 목록"""
    _font()
    G = gather(bid, K, ST, env, PE, loads) if G is None else G
    if G is None:
        import streamlit as st
        st.info("이 배치의 자료를 찾지 못했습니다."); return []
    return compose(StDoc(), G)
