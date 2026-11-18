# -*- coding: utf-8 -*-
"""'조직도' 페이지 (v1.38.0) — 회사 조직도 그림 + 부서·파트별 역할 표. 모든 계정이 본다.

내용은 DATA_DIR\\org_chart.json 한 파일에 있다(없으면 아래 DEFAULT). 관리자는 화면 아래
'조직도 고치기'에서 표를 고치고 저장하면 그림까지 바로 바뀐다.
표 한 줄 = 부서·파트(팀) 하나. 부서가 '경영'인 줄은 맨 위 상자, 나머지는 적힌 순서대로 아래 줄에 놓인다.
한 부서에 줄이 여럿이거나 '파트·팀' 칸이 채워져 있으면 그 부서는 큰 상자 안에 파트 상자를 나란히 그린다.
'강조'에 체크한 파트는 파란 상자(2026-11 개편으로 새로 생긴 파트장 자리 등)."""
import os, json, time, html
import pandas as pd
import streamlit as st

from . import config as C

try:                                    # 스트림릿 1.47 부터 width='stretch' (app.py 와 같은 방식)
    from packaging.version import Version as _V
    _FULL = {"width": "stretch"} if _V(st.__version__) >= _V("1.47") else {"use_container_width": True}
except Exception:
    _FULL = {"use_container_width": True}

ORG_FILE = os.path.join(C.DATA_DIR, "org_chart.json")
COLS = ["부서", "파트·팀", "책임자", "구성원", "주요 업무", "강조"]

DEFAULT = {
    "기준일": "2026-11-18",
    "제목": "생산부는 종균배양·생육 두 파트장이 나란히 부대표에게 보고",
    "메모": "외국인 근로자 배치는 따로 정리할 예정입니다.",
    "rows": [
        {"부서": "경영", "파트·팀": "", "책임자": "대표 박학주", "구성원": "부대표 박경주", "주요 업무": "전체 의사결정, 생산부 두 파트 조율", "강조": False},
        {"부서": "생산부", "파트·팀": "종균배양 파트", "책임자": "남기천 수석연구원", "구성원": "오동준 대리", "주요 업무": "종균·접종원 배양, 실험실·배양실 관리, 균긁기·권지 품질", "강조": True},
        {"부서": "생산부", "파트·팀": "생육 파트", "책임자": "권석현 소장", "구성원": "이장우 사원", "주요 업무": "팽이·새송이 재배사 환경·생육 관리, 수확, 재배 설비", "강조": True},
        {"부서": "생산부", "파트·팀": "생산관리·출하", "책임자": "안석구 팀장", "구성원": "", "주요 업무": "포장·출하 지시, 시장별 출하량, 생산 집계", "강조": False},
        {"부서": "총무부", "파트·팀": "", "책임자": "허지형 부장", "구성원": "최세라 과장, 김세영 사원", "주요 업무": "회계·매출 정산, 인사·근태, 구매", "강조": False},
        {"부서": "반가공센터", "파트·팀": "", "책임자": "김병준 부장", "구성원": "김정미 과장, 현장 작업자", "주요 업무": "과자 생산·품질·출고, 일일 업무보고·매출장", "강조": False},
    ],
}


# ───────── 자료 ─────────
def load():
    try:
        if os.path.exists(ORG_FILE):
            with open(ORG_FILE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("rows"), list):
                return d
    except Exception:
        pass
    return json.loads(json.dumps(DEFAULT, ensure_ascii=False))


def save(d, who=""):
    d = dict(d)
    d["수정"] = time.strftime("%Y-%m-%d %H:%M") + (f" {who}" if who else "")
    tmp = ORG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ORG_FILE)


def _clean(rows):
    out = []
    for r in rows:
        r = {k: r.get(k) for k in COLS}
        for k in COLS[:-1]:
            v = r.get(k)
            r[k] = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
        r["강조"] = bool(r.get("강조")) if r.get("강조") is not None and not (isinstance(r.get("강조"), float) and pd.isna(r.get("강조"))) else False
        if r["부서"] or r["책임자"]:
            out.append(r)
    return out


def _members(s):
    return [x.strip() for x in str(s or "").replace("·", "·").split(",") if x.strip()]


# ───────── 그림 (SVG) ─────────
def _tw(s, fs):
    """글자 폭 어림: 한글·전각 = 글자 크기, 그 밖 = 0.6배."""
    return sum(fs if ord(ch) > 0x1100 else fs * 0.6 for ch in str(s))


EDGE, ACC, ACC_FILL, TINT = "#b4b8ae", "#2a6fdb", "rgba(42,111,219,0.10)", "rgba(128,128,128,0.06)"
FS_NAME, FS_LINE, LH = 15, 13.5, 20


def _box_lines(head, lead, members):
    lines = ([lead] if lead else []) + members
    w = max([_tw(head, FS_NAME)] + [_tw(x, FS_LINE) for x in lines] + [96]) + 28
    h = 36 + LH * len(lines)
    return w, h, lines


def _draw_box(x, y, w, h, head, lines, accent=False, tint=False):
    e = html.escape
    fill = ACC_FILL if accent else (TINT if tint else "none")
    stroke, sw = (ACC, 2) if accent else (EDGE, 1.25)
    out = [f"<rect x='{x:.1f}' y='{y:.1f}' width='{w:.1f}' height='{h:.1f}' rx='8' fill='{fill}' stroke='{stroke}' stroke-width='{sw}'/>",
           f"<text x='{x + 14:.1f}' y='{y + 24:.1f}' font-size='{FS_NAME}' font-weight='600' fill='currentColor'>{e(head)}</text>"]
    for i, ln in enumerate(lines):
        op = "1" if i == 0 else "0.85"
        out.append(f"<text x='{x + 14:.1f}' y='{y + 24 + LH * (i + 1):.1f}' font-size='{FS_LINE}' fill='currentColor' fill-opacity='{op}'>{e(ln)}</text>")
    return out


def build_svg(rows):
    """표 줄 → 조직도 SVG 문자열. 맨 위 '경영' 상자, 그 아래 부서들을 한 줄로."""
    rows = _clean(rows)
    top = [r for r in rows if r["부서"] == "경영"]
    order = []
    for r in rows:
        if r["부서"] != "경영" and r["부서"] not in order:
            order.append(r["부서"])
    depts = []
    for dn in order:
        parts = [r for r in rows if r["부서"] == dn]
        nested = len(parts) > 1 or any(p["파트·팀"] and p["파트·팀"] not in ("-", "—") for p in parts)
        if nested:
            subs = []
            for p in parts:
                head = p["파트·팀"] if p["파트·팀"] not in ("", "-", "—") else p["책임자"]
                w, h, lines = _box_lines(head, p["책임자"] if head != p["책임자"] else "", _members(p["구성원"]))
                subs.append({"w": w, "h": h, "head": head, "lines": lines, "acc": p["강조"]})
            sh = max(s["h"] for s in subs)
            inner = sum(s["w"] for s in subs) + 12 * (len(subs) - 1)
            w = max(inner + 32, _tw(dn, FS_NAME) + 32)
            depts.append({"name": dn, "nested": True, "subs": subs, "sh": sh, "w": w, "h": 40 + sh + 16})
        else:
            p = parts[0]
            w, h, lines = _box_lines(dn, p["책임자"], _members(p["구성원"]))
            depts.append({"name": dn, "nested": False, "w": w, "h": h, "lines": lines, "acc": p["강조"]})
    gap = 18
    row_w = sum(d["w"] for d in depts) + gap * max(len(depts) - 1, 0)
    VW = max(760, row_w + 48)
    y = 16
    out = []
    if top:
        head = top[0]["책임자"] or "경영"
        lines = [m for t in top for m in ([t["책임자"]] if t is not top[0] else []) + _members(t["구성원"])]
        tw_ = max([_tw(head, FS_NAME)] + [_tw(x, FS_LINE) for x in lines] + [180]) + 40
        th = 36 + LH * len(lines)
        tx = (VW - tw_) / 2
        e = html.escape
        out.append(f"<rect x='{tx:.1f}' y='{y}' width='{tw_:.1f}' height='{th}' rx='8' fill='none' stroke='{EDGE}' stroke-width='1.25'/>")
        out.append(f"<text x='{VW / 2:.1f}' y='{y + 24}' text-anchor='middle' font-size='{FS_NAME}' font-weight='600' fill='currentColor'>{e(head)}</text>")
        for i, ln in enumerate(lines):
            out.append(f"<text x='{VW / 2:.1f}' y='{y + 24 + LH * (i + 1)}' text-anchor='middle' font-size='{FS_LINE}' fill='currentColor'>{e(ln)}</text>")
        y += th
    if depts:
        x0 = (VW - row_w) / 2
        bus = y + 22
        dy = bus + 22 if top else y
        cx = []
        x = x0
        for d in depts:
            d["x"], d["y"] = x, dy
            cx.append(x + d["w"] / 2)
            x += d["w"] + gap
        if top:
            out.append(f"<g fill='none' stroke='{EDGE}' stroke-width='1.25'><path d='M{VW / 2:.1f} {y}V{bus}'/>"
                       f"<path d='M{min(cx + [VW / 2]):.1f} {bus}H{max(cx + [VW / 2]):.1f}'/>"
                       + "".join(f"<path d='M{c:.1f} {bus}V{dy}'/>" for c in cx) + "</g>")
        for d in depts:
            if d["nested"]:
                out.append(f"<rect x='{d['x']:.1f}' y='{dy}' width='{d['w']:.1f}' height='{d['h']}' rx='8' fill='none' stroke='{EDGE}' stroke-width='1.25'/>")
                out.append(f"<text x='{d['x'] + 16:.1f}' y='{dy + 24}' font-size='{FS_NAME}' font-weight='600' fill='currentColor'>{html.escape(d['name'])}</text>")
                sx = d["x"] + 16
                for s in d["subs"]:
                    out += _draw_box(sx, dy + 40, s["w"], d["sh"], s["head"], s["lines"], accent=s["acc"])
                    sx += s["w"] + 12
            else:
                out += _draw_box(d["x"], dy, d["w"], d["h"], d["name"], d["lines"], accent=d["acc"])
        y = dy + max(d["h"] for d in depts)
    VH = y + 16
    return (f"<svg viewBox='0 0 {VW:.0f} {VH:.0f}' width='100%' style='max-width:{VW:.0f}px;display:block' role='img' aria-label='조직도' "
            f"font-family='inherit'>" + "".join(out) + "</svg>")


# ───────── 화면 ─────────
def render(me=None):
    d = load()
    rows = _clean(d.get("rows", []))
    st.title("조직도")
    sub = d.get("제목") or ""
    st.caption((f"**{sub}** · " if sub else "") + f"기준 {d.get('기준일', '')}"
               + (f" · 마지막 수정 {d['수정']}" if d.get("수정") else ""))
    st.markdown(build_svg(rows), unsafe_allow_html=True)
    st.subheader("부서·파트별 역할")
    t = pd.DataFrame(rows, columns=COLS).drop(columns=["강조"])
    t["파트·팀"] = t["파트·팀"].replace("", "—")
    t["구성원"] = t["구성원"].replace("", "—")
    st.dataframe(t, hide_index=True, **_FULL,
                 column_config={"주요 업무": st.column_config.TextColumn("주요 업무", width="large")})
    if d.get("메모"):
        st.caption(d["메모"])

    admin = bool(me) and (me.get("role") == "관리자" or me.get("무인증"))
    if not admin:
        return
    with st.expander("조직도 고치기 (관리자)"):
        st.caption("표 한 줄 = 부서·파트(팀) 하나. 부서 '경영' 줄은 맨 위 상자. 같은 부서에 줄이 여럿이면 그 부서 상자 안에 파트가 나란히 그려집니다. "
                   "구성원은 쉼표로 나눠 적습니다. '강조'는 파란 상자. 줄 순서가 그림의 왼쪽→오른쪽 순서입니다. 저장하면 모든 계정에 바로 보입니다.")
        c1, c2 = st.columns([1, 3])
        base = c1.text_input("기준일", d.get("기준일", ""), key="org_base")
        title = c2.text_input("한 줄 설명", d.get("제목", ""), key="org_title")
        memo = st.text_input("표 아래 메모", d.get("메모", ""), key="org_memo")
        E = st.data_editor(pd.DataFrame(rows, columns=COLS), num_rows="dynamic", hide_index=True, **_FULL, key="org_edit",
                           column_config={"강조": st.column_config.CheckboxColumn("강조", default=False),
                                          "주요 업무": st.column_config.TextColumn("주요 업무", width="large")})
        new = _clean(E.to_dict("records"))
        if new != rows:
            st.markdown("**미리보기**")
            st.markdown(build_svg(new), unsafe_allow_html=True)
        b1, b2 = st.columns([1, 1])
        if b1.button("저장", type="primary", key="org_save"):
            save({"기준일": base, "제목": title, "메모": memo, "rows": new}, (me or {}).get("id") or (me or {}).get("name") or "")
            st.success("저장했습니다."); st.rerun()
        if os.path.exists(ORG_FILE) and b2.button("처음 내용(2026-11 개편안)으로 되돌리기", key="org_reset"):
            save(DEFAULT, (me or {}).get("id") or "")
            st.rerun()
