# -*- coding: utf-8 -*-
"""'업무일지' 페이지 (관리자 전용) — 담당자별 일일 업무보고서를 날짜별로 모아 보고, 이슈만 따로 본다.
자료: etl_worklog 가 만든 app_cache\\worklog.json (refresh_all.bat → mirror_nas 5e 복사 → etl_cli 가 읽음)."""
import os, re, html, time, datetime as dt
import pandas as pd
import streamlit as st

from . import config as C, etl_worklog as WL

WD = "월화수목금토일"
TAG_ORDER = ["이슈"] + [n for n, _ in WL.TAGS]
CSS = """<style>
.wl-card{border:1px solid rgba(128,128,128,.28);border-radius:8px;padding:.55rem .8rem .45rem;margin:.35rem 0 .6rem}
.wl-h{font-weight:600;margin-bottom:.2rem}.wl-h small{font-weight:400;opacity:.65;margin-left:.35rem}
.wl-card ul{margin:.1rem 0 .2rem 1.1rem;padding:0}.wl-card li{margin:.05rem 0;line-height:1.45}
.wl-card li.sub{margin-left:1.1rem;list-style:circle}.wl-card li.rep{opacity:.5}
.wl-card li.iss{color:#b42318}.wl-note{margin-top:.25rem;padding:.3rem .5rem;border-radius:6px;background:rgba(240,180,40,.12)}
.wl-tag{display:inline-block;font-size:.72em;padding:0 .4em;margin-left:.3em;border-radius:9px;border:1px solid rgba(128,128,128,.4);opacity:.75;vertical-align:1px}
.wl-tag.iss{border-color:#d92d20;color:#d92d20;opacity:1}
.wl-card mark{padding:0 .1em}.wl-day{margin:.9rem 0 .1rem;font-size:1.05rem;font-weight:700}
</style>"""


def _sig():
    try:
        return int(os.path.getmtime(WL.OUT))
    except OSError:
        return 0


@st.cache_data(max_entries=2, show_spinner=False)
def _load(sig):
    return WL.load()


def _dlabel(d):
    x = dt.date.fromisoformat(d)
    return f"{x.month}/{x.day} ({WD[x.weekday()]})"


def _hl(t, q):
    s = html.escape(t)
    if q:
        s = re.sub("(" + re.escape(html.escape(q)) + ")", r"<mark>\1</mark>", s, flags=re.I)
    return s


def _li(l, q):
    cls = " ".join(c for c, on in (("sub", l.get("sub")), ("rep", l.get("rep")), ("iss", "이슈" in l["tags"])) if on)
    tags = "".join(f'<span class="wl-tag{" iss" if t == "이슈" else ""}">{t}</span>' for t in l["tags"] if t == "이슈" or not l.get("rep"))
    return f'<li class="{cls}">{_hl(l["t"], q)}{tags}</li>'


def _card(r, q, show_rep):
    L = r["lines"]
    # 양식 줄은 접되, 바로 아래에 새 내용이 이어지는 머리 줄(예: '설비 보수 및 긴급 대응')은 흐리게 남겨 무슨 항목인지 보이게 한다
    lines = [l for i, l in enumerate(L) if show_rep or not l.get("rep") or (i + 1 < len(L) and not L[i + 1].get("rep"))]
    hidden = len(r["lines"]) - len(lines)
    h = f'<div class="wl-card"><div class="wl-h">{html.escape(r["who"])}<small>{html.escape(" ".join(x for x in (r.get("role"), r.get("title")) if x))}</small></div>'
    if lines:
        h += "<ul>" + "".join(_li(l, q) for l in lines) + "</ul>"
    if hidden:
        h += f'<div style="font-size:.8em;opacity:.55">매일 같은 양식 줄 {hidden}개 접음</div>'
    if r["note"]:
        h += '<div class="wl-note"><b>비고</b><ul>' + "".join(_li(l, q) for l in r["note"]) + "</ul></div>"
    return h + "</div>"


def _match(l, tag, q):
    return (tag == "전체" or tag in l["tags"]) and (not q or q.lower() in l["t"].lower())


def _filter(recs, tag, q):
    """분류·검색으로 줄을 거른다. 걸러진 줄이 없는 보고는 뺀다."""
    if tag == "전체" and not q:
        return recs
    out = []
    for r in recs:
        ls = [l for l in r["lines"] if _match(l, tag, q)]
        ns = [l for l in r["note"] if _match(l, tag, q)]
        if ls or ns:
            out.append({**r, "lines": ls, "note": ns})
    return out


def render(ver=None):
    st.title("업무일지")
    D = _load(_sig())
    recs = D.get("recs", [])
    if not recs:
        st.info(f"아직 읽은 업무일지가 없습니다. 사본 폴더: `{WL.base_dir()}` (mirror_nas.bat 이 Z:\\업무일지 를 복사) — 아래 '지금 다시 읽기'.")
        _reload_box(D)
        return
    st.markdown(CSS, unsafe_allow_html=True)
    people = sorted({r["who"] for r in recs})
    today = dt.date.today()
    st.caption(f"Z:\\업무일지 의 담당자별 업무보고서(워드·한글·엑셀)와 반가공 부장 일일업무보고서 {len(D.get('files', []))}개 파일 → {len(recs):,}일치 · 마지막 읽음 {D.get('updated', '')} · "
               "줄마다 내용을 보고 분류를 자동으로 붙였습니다(가끔 틀릴 수 있음). 전화·계좌·차량번호는 가리고 출퇴근 시각 줄은 뺐습니다.")

    c1, c2, c3, c4 = st.columns([1.1, 1.8, 1.1, 1.6])
    per = c1.selectbox("기간", ["최근 7일", "최근 14일", "최근 30일", "이번 달", "지난 달", "최근 1년", "전체"], index=1, key="wl_per")
    first = today.replace(day=1)
    rng = {"최근 7일": (today - dt.timedelta(days=6), today), "최근 14일": (today - dt.timedelta(days=13), today),
           "최근 30일": (today - dt.timedelta(days=29), today), "이번 달": (first, today),
           "지난 달": ((first - dt.timedelta(days=1)).replace(day=1), first - dt.timedelta(days=1)),
           "최근 1년": (today - dt.timedelta(days=365), today)}.get(per, (dt.date(2000, 1, 1), today))
    who = c2.multiselect("담당자", people, key="wl_who", placeholder="전체 담당자")
    tag = c3.selectbox("분류", ["전체"] + TAG_ORDER, key="wl_tag")
    q = c4.text_input("내용 찾기", key="wl_q", placeholder="예: 빈대, 냉동기, 오염").strip()
    d0, d1 = rng[0].isoformat(), rng[1].isoformat()
    X = [r for r in recs if d0 <= r["d"] <= d1 and (not who or r["who"] in who)]
    Y = _filter(X, tag, q)

    n_iss = sum(1 for r in X for l in r["lines"] + r["note"] if "이슈" in l["tags"])
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("보고", f"{len(X)}건", help="담당자 × 날짜")
    k2.metric("담당자", f"{len({r['who'] for r in X})}명")
    k3.metric("이슈 줄", f"{n_iss}개", help="고장·누수·오염·불량·지연·빈대 같은 낱말이 든 줄 (매일 같은 양식 문구는 뺌)")
    k4.metric("가장 최근 보고", _dlabel(max(r["d"] for r in recs)))

    t1, t2, t3, t4 = st.tabs(["📅 날짜별", "⚠️ 이슈 모아보기", "👤 작성 현황", "📄 문서·파일"])
    with t1:
        show_rep = st.toggle("매일 같은 양식 줄도 보기", value=False, key="wl_rep",
                             help="양식에 늘 적혀 있는 줄(예: '접종기 분해 청소 매일')은 기본으로 접어 둡니다")
        _by_day(Y, q, show_rep)
    with t2:
        _issues(X, q)
    with t3:
        _status(recs, today)
    with t4:
        _docs(D, q)
    _reload_box(D)


def _by_day(Y, q, show_rep):
    if not Y:
        st.info("조건에 맞는 기록이 없습니다."); return
    days = sorted({r["d"] for r in Y}, reverse=True)
    lim = st.session_state.get("wl_lim", 10)
    for d in days[:lim]:
        rs = sorted([r for r in Y if r["d"] == d], key=lambda r: r["who"])
        st.markdown(f'<div class="wl-day">{_dlabel(d)} <small style="font-weight:400;opacity:.6">{d[:4]}년 · {len(rs)}명</small></div>', unsafe_allow_html=True)
        cols = st.columns(2) if len(rs) > 1 else [st.container()]
        for i, r in enumerate(rs):
            cols[i % len(cols)].markdown(_card(r, q, show_rep), unsafe_allow_html=True)
    if len(days) > lim:
        if st.button(f"이전 날짜 더 보기 ({len(days) - lim}일 남음)", key="wl_more"):
            st.session_state["wl_lim"] = lim + 10; st.rerun()


def _issues(X, q):
    rows = []
    for r in X:
        for part, ls in (("업무", r["lines"]), ("비고", r["note"])):
            for l in ls:
                if "이슈" in l["tags"] and (not q or q.lower() in l["t"].lower()):
                    rows.append({"날짜": r["d"], "요일": WD[dt.date.fromisoformat(r["d"]).weekday()], "담당자": r["who"],
                                 "내용": l["t"], "분류": ", ".join(t for t in l["tags"] if t != "이슈"), "칸": part})
    st.caption("고장·누수·파손·오염·불량·지연·빈대·단종·보류 같은 낱말이 든 줄만 모았습니다. 같은 고장을 여러 사람이 적으면 여러 줄로 나옵니다.")
    if not rows:
        st.info("기간 안에 이슈 줄이 없습니다."); return
    T = pd.DataFrame(rows).sort_values(["날짜", "담당자"], ascending=[False, True])
    st.dataframe(T, hide_index=True, use_container_width=True, height=min(600, 38 + 35 * len(T)),
                 column_config={"날짜": st.column_config.TextColumn(width=90), "요일": st.column_config.TextColumn(width=40),
                                "담당자": st.column_config.TextColumn(width=70), "내용": st.column_config.TextColumn(width=520),
                                "분류": st.column_config.TextColumn(width=130), "칸": st.column_config.TextColumn(width=50)})
    c = T["분류"].str.split(", ").explode().replace("", "기타").value_counts()
    st.caption("분류별 이슈 줄 (한 줄이 여러 분류에 들 수 있음): " + " · ".join(f"{k} {v}" for k, v in c.items()))


def _status(recs, today):
    st.caption("담당자별 마지막 보고일과 최근 30일 작성 일수입니다. 일요일·휴무일은 원래 비는 날이 있습니다.")
    rows = []
    d30 = (today - dt.timedelta(days=29)).isoformat()
    for w in sorted({r["who"] for r in recs}):
        rs = [r for r in recs if r["who"] == w]
        last = max(r["d"] for r in rs)
        gap = (today - dt.date.fromisoformat(last)).days
        rows.append({"담당자": w, "직급·업무": " ".join(x for x in (rs[0].get("role"), rs[0].get("title")) if x),
                     "마지막 보고": _dlabel(last) + f" {last[:4]}", "경과": f"{gap}일", "최근 30일": sum(1 for r in rs if r["d"] >= d30),
                     "전체": len(rs), "처음": rs[-1]["d"] if rs else "",
                     "상태": "🟢" if gap <= 2 else ("🟡" if gap <= 7 else "⚪ 오래됨")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _talk_docs(q):
    """톡방(연우/진성 관리자 업무방)에 올라온 사진 — 누가 언제 무엇을 올렸나 (v1.33.0)."""
    try:
        from . import etl_talk
        T = etl_talk.docs()
    except Exception:
        return
    if T is None or T.empty:
        return
    st.markdown("**톡방에 올린 사진** (톡클라우드 → 매일 08:20·14:20 받아 읽음)")
    if q:
        m = T["제목"].str.contains(q, case=False, na=False) | T["메모"].str.contains(q, case=False, na=False) | T["올린사람"].str.contains(q, case=False, na=False)
        T = T[m]
    T = T.copy()
    T["올린시각"] = T["올린시각"].dt.strftime("%m/%d %H:%M")
    st.dataframe(T[["올린시각", "올린사람", "종류", "제목", "메모", "파일"]].head(200), hide_index=True, use_container_width=True, height=min(400, 38 + 35 * min(len(T), 10)))
    st.caption("출하지시서는 시장별 지시 박스를 읽어 경영 자료 → 출하 대사에 '지시 박스'로 넣습니다. 엑셀 화면 캡처는 제목만 남기고 숫자는 원본 엑셀에서 읽습니다.")


def _docs(D, q):
    _talk_docs(q)
    docs = D.get("docs", [])
    if docs:
        st.markdown("**날짜 없는 문서** (시험결과 보고서 등 — 앞부분만)")
        for d in sorted(docs, key=lambda x: -x.get("mtime", 0)):
            if q and not any(q.lower() in l["t"].lower() for l in d["lines"]) and q.lower() not in d["title"].lower():
                continue
            with st.expander(f"{d['title']} · {d['who']} · {time.strftime('%Y-%m-%d', time.localtime(d['mtime']))}"):
                st.markdown("<ul>" + "".join(f"<li>{_hl(l['t'], q)}</li>" for l in d["lines"]) + "</ul>", unsafe_allow_html=True)
    F = pd.DataFrame(D.get("files", []))
    if not F.empty:
        F["수정"] = pd.to_datetime(F["mtime"], unit="s").dt.tz_localize("UTC").dt.tz_convert("Asia/Seoul").dt.strftime("%Y-%m-%d %H:%M")
        F["상태"] = F.apply(lambda r: "못 읽음: " + r["err"] if r["err"] else ("문서" if r["doc"] else
                            ("다른 파일과 날짜 겹침(최근 파일 사용)" if r["n"] and not r["used"] else "")), axis=1)
        st.markdown("**읽은 파일**")
        st.dataframe(F[["file", "who", "n", "used", "수정", "상태"]].rename(columns={"file": "파일", "who": "담당자", "n": "날짜 수", "used": "화면에 쓴 날짜"}),
                     hide_index=True, use_container_width=True)
    st.caption("한글(.hwp)은 글자만 읽습니다. 50MB 넘는 파일(사진이 많이 든 월 파일 등)은 복사 단계에서 빠집니다.")


def _reload_box(D):
    with st.expander("지금 다시 읽기"):
        st.caption(f"사본 폴더 `{WL.base_dir()}` 를 다시 읽습니다(바뀐 파일만). Z: 에서 새로 복사하는 것은 매일 09:00·15:00 자동 갱신 때 합니다.")
        if st.button("업무일지 다시 읽기", key="wl_reload"):
            with st.spinner("읽는 중…"):
                n = WL.run(progress=lambda m: None)
            _load.clear()
            st.success(f"{n}개 파일을 새로 읽었습니다."); st.rerun()
