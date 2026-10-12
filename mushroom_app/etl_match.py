# -*- coding: utf-8 -*-
"""3단 대조: 포장실 일지 출고 → 시장 경매(팩스 판매원표·정산서) → 정산 지급액(→ 입금) (v1.26.2)

경매는 보통 출하 다음 날이지만, 하루 출하분 일부가 그다음 경매로 넘어가기도 한다(동화·서부 등).
그래서 날짜 합계가 아니라 '박스 묶음(같은 단가)' 단위로 맞춘다.
  1단계: 팩스의 줄(단가·박스)을 장부 출고 줄(같은 시장·작물·같은 박스단가, 출하 1~4일 뒤 경매)에 먼저 출하한 것부터 붙인다
  2단계: 단가 없이 합계만 적힌 정산서·단위가 다른 시장(새송이 kg 단가 등)은 '판매일 하루 전 출하일' 날짜 합계 금액으로 맞춘다
  3단계: 장부 비고에 '재정산'이 있으면 팩스(처음 경락)와 금액이 달라도 '재정산'으로 둔다
결과는 출하일 단위(장부 쪽)와 판매일 단위(팩스 쪽) 두 표, 그리고 정산(지급액) 표.
"""
import collections
import numpy as np
import pandas as pd

LAG_MIN, LAG_MAX = 1, 4
TOL = 1000        # 원


def fax_lots(D, L):
    """팩스 → 판매 묶음 [작물, 시장, 판매일, 출하자, 단가, 박스, 금액, 종류(단가/합계만), 파일]
    같은 문서 두 번(재전송)은 한 번만, 판매원표 줄을 우선 쓰고 정산서가 더 넓으면(다른 판매분 합산) 남는 몫을 합계만 묶음으로."""
    if D is None or D.empty or L is None or L.empty:
        return pd.DataFrame()
    D = D[D["문서"].isin(["판매원표", "정산서"])].copy()
    D["_k"] = D["파일"] + "#" + D["장"].astype(str)
    L = L.copy()
    L["_k"] = L["파일"] + "#" + L["장"].astype(str)
    D["_공제"] = D[["수수료", "운임", "기타공제"]].sum(axis=1, min_count=1).fillna(0)
    Lg = {k: g for k, g in L.groupby("_k", sort=False)}          # 문서(파일#장)별 줄 — 묶음마다 전체 줄 표를 거르지 않는다 (v1.27.1)
    _empty = L.iloc[0:0]

    def lines_of(keys):
        parts = [Lg[k] for k in keys if k in Lg]
        return pd.concat(parts) if parts else _empty
    out = []

    def add(rows, crop, mk, day, sh, kind):
        for r in rows.itertuples():
            q, a = r.수량, r.금액
            if pd.isna(q) or pd.isna(a) or q <= 0:
                continue
            out.append(dict(작물=crop, 시장=mk, 판매일=day, 출하자=sh, 단가=round(a / q), 박스=q, 금액=a, 종류=kind, 파일=r.파일))

    for (crop, mk, day, sh), g in D.groupby(["작물", "시장", "날짜", "출하자"]):
        u = g.drop_duplicates("서명") if "서명" in g else g
        W = u[u["문서"] == "판매원표"]
        S = u[u["문서"] == "정산서"]
        if len(S):
            S = S.sort_values("_공제", ascending=False).drop_duplicates(["수량", "금액"])
        lw = lines_of(W["_k"])
        ls = lines_of(S["_k"])
        if len(lw):
            add(lw, crop, mk, day, sh, "단가")
            if len(S):
                dq, da = S["수량"].sum() - lw["수량"].sum(), S["금액"].sum() - lw["금액"].sum()
                if da > TOL and dq > 0:                      # 정산서가 다른 판매분까지 합친 경우 남는 몫
                    out.append(dict(작물=crop, 시장=mk, 판매일=day, 출하자=sh, 단가=round(da / dq), 박스=dq, 금액=da, 종류="합계만", 파일=", ".join(sorted(set(S["파일"])))))
        elif len(ls):
            # 정산서 줄도 금액÷수량이 딱 떨어지는 단가(50원 단위)면 그 단가로 판 묶음
            for r in ls.itertuples():
                q, a = r.수량, r.금액
                if pd.isna(q) or pd.isna(a) or q <= 0:
                    continue
                u = a / q
                ok = pd.notna(r.단가) or (abs(u - round(u)) < 1e-6 and round(u) % 50 == 0)
                out.append(dict(작물=crop, 시장=mk, 판매일=day, 출하자=sh, 단가=round(u), 박스=q, 금액=a, 종류="단가" if ok else "합계만", 파일=r.파일))
        elif len(S):
            add(S, crop, mk, day, sh, "합계만")
    return pd.DataFrame(out)


def _ship_day(sale, days, last):
    k = sale - pd.Timedelta(days=1)
    return k


def match(MD, D, L):
    """반환 dict(ship=출하일 표, sale=판매일 표, lots=장부 줄별 결과, faxlots=팩스 묶음별 결과)"""
    F = fax_lots(D, L)
    if MD is None or MD.empty or F.empty:
        return {}
    start = F["판매일"].min() - pd.Timedelta(days=LAG_MAX)
    G = MD[(MD["날짜"] >= start) & (MD["박스"] > 0)].copy().reset_index(drop=True)
    G["재정산"] = G["비고"].astype(str).str.contains("재정산")
    G["예정경매일"] = _planned_sale(G)                 # 포장실 비고 "9/5 정산" = 그 박스는 9/5 경매로 넘김 (v1.26.8)
    G["경매박스"] = 0.0          # 같은 단가로 다음 날 경매 확인
    G["이월박스"] = 0.0          # 같은 단가로 2~4일 뒤 경매 확인
    G["합계확인박스"] = 0.0
    G["경매금액"] = 0.0
    G["경매일"] = ""
    F = F.sort_values(["판매일", "단가"]).reset_index(drop=True)
    F["확인박스"] = 0.0
    F["짝"] = ""
    ship_days = set(G["날짜"])
    last_ship = G["날짜"].max()

    for (crop, mk), f in F.groupby(["작물", "시장"]):
        gi = G[(G["작물"] == crop) & (G["시장"] == mk)]
        left = {i: gi.at[i, "박스"] for i in gi.index}
        priced = gi[gi["박스단가"].notna()].sort_values("날짜")

        def take(i, j, t, sale, kind):
            left[i] -= t
            F.at[j, "확인박스"] += t
            G.at[i, kind] += t
            G.at[i, "경매금액"] += t * F.at[j, "단가"]
            G.at[i, "경매일"] = _add_day(G.at[i, "경매일"], sale)

        # 판매일 순서대로: ① 바로 앞 출하일의 같은 단가 ② 합계만 정산서·단위 다른 시장은 그날 합계 ③ 더 앞 출하일에서 넘어온 같은 단가
        for sale, fd in f.groupby("판매일"):
            sd = _prev_ship(sale, gi, ship_days, last_ship)
            unit = fd[fd["종류"] == "단가"]
            for j, r in unit.iterrows():                                   # ⓪ 비고에 이 경매일로 넘긴다고 적힌 줄부터
                need = r["박스"] - F.at[j, "확인박스"]
                for i in priced.index[(priced["예정경매일"] == sale) & (priced["박스단가"].round() == r["단가"])]:
                    if need <= 0.5:
                        break
                    t = min(need, left[i])
                    if t > 0:
                        take(i, j, t, sale, "이월박스" if gi.at[i, "날짜"] < sd else "경매박스"); need -= t
            for j, r in unit.iterrows():                                   # ①
                need = r["박스"] - F.at[j, "확인박스"]
                for i in priced.index[(priced["날짜"] == sd) & (priced["박스단가"].round() == r["단가"]) & priced["예정경매일"].isna()]:
                    if need <= 0.5:
                        break
                    t = min(need, left[i])
                    if t > 0:
                        take(i, j, t, sale, "경매박스"); need -= t
            # ①-2 단위가 다른 줄: 장부 '미니 24박스 12,000원'이 시장에선 '2kg 비포 96개 3,000원'(한 박스 = k개) — 단가가 1/k 이고
            #     개수가 k배인 줄들을 모아 금액으로 맞춘다 (서부청과 새송이, v1.27.3)
            for i in [i for i in priced.index if priced.at[i, "날짜"] == sd and left[i] > 0.5 and pd.isna(priced.at[i, "예정경매일"])]:
                p_ = round(gi.at[i, "박스단가"])
                for k in range(2, 11):
                    if p_ % k:
                        continue
                    cand_j = [j for j in unit.index if abs(F.at[j, "단가"] - p_ / k) < 1 and F.at[j, "박스"] - F.at[j, "확인박스"] > 0.5]
                    avail = sum(F.at[j, "박스"] - F.at[j, "확인박스"] for j in cand_j)
                    need_u = left[i] * k
                    if not cand_j or avail + 0.5 < need_u:
                        continue
                    for j in cand_j:
                        if need_u <= 0.5:
                            break
                        t = min(need_u, F.at[j, "박스"] - F.at[j, "확인박스"])
                        if t > 0:
                            left[i] -= t / k
                            F.at[j, "확인박스"] += t
                            G.at[i, "경매박스"] += t / k
                            G.at[i, "경매금액"] += t * F.at[j, "단가"]
                            G.at[i, "경매일"] = _add_day(G.at[i, "경매일"], sale)
                            F.at[j, "짝"] = f"단위 다름(1박스={k}개) 일치"
                            need_u -= t
                    break
            cand = [i for i in gi.index if gi.at[i, "날짜"] == sd and left[i] > 0.5]   # ②
            rest = fd[fd["박스"] - F.loc[fd.index, "확인박스"] > 0.5]
            sumonly = rest[rest["종류"] == "합계만"]
            use = sumonly if len(sumonly) else rest
            if cand and len(use):
                fbox = (use["박스"] - F.loc[use.index, "확인박스"]).sum()
                famt = (use["금액"] * (1 - F.loc[use.index, "확인박스"] / use["박스"])).sum()
                lbox = sum(left[i] for i in cand)
                lamt = sum(left[i] * gi.at[i, "박스단가"] for i in cand if pd.notna(gi.at[i, "박스단가"]))
                resettle = any(gi.at[i, "재정산"] for i in cand)
                same_amt = abs(famt - lamt) <= TOL
                if same_amt or resettle or (len(sumonly) and abs(fbox - lbox) < 0.5):
                    tag = "합계 일치" if same_amt else ("재정산" if resettle else "박스만 일치")
                    for i in cand:
                        G.at[i, "합계확인박스"] += left[i]
                        G.at[i, "경매금액"] += famt * left[i] / lbox if lbox else 0
                        G.at[i, "경매일"] = _add_day(G.at[i, "경매일"], sale)
                        left[i] = 0
                    for j in use.index:
                        F.at[j, "확인박스"] = F.at[j, "박스"]
                        F.at[j, "짝"] = tag
            for j, r in unit.iterrows():                                   # ③
                need = r["박스"] - F.at[j, "확인박스"]
                if need <= 0.5:
                    F.at[j, "짝"] = F.at[j, "짝"] or "단가 일치"
                    continue
                old = priced[(priced["날짜"] < sd) & (priced["날짜"] >= sale - pd.Timedelta(days=LAG_MAX)) & (priced["박스단가"].round() == r["단가"])
                             & (priced["예정경매일"].isna() | (priced["예정경매일"] == sale))]
                for i in old.index:
                    if need <= 0.5:
                        break
                    t = min(need, left[i])
                    if t > 0:
                        take(i, j, t, sale, "이월박스"); need -= t
                if not str(F.at[j, "짝"]).startswith("단위 다름"):
                    F.at[j, "짝"] = "단가 일치" if need <= 0.5 else ("일부" if need < r["박스"] else "")
        # ④ 그래도 남은 합계만 묶음(단가 없는 정산서)은 1~4일 전 출하의 남은 박스에 먼저 출하한 것부터 박스 수만큼 붙인다
        for j, r in F[(F["작물"] == crop) & (F["시장"] == mk) & (F["종류"] == "합계만")].iterrows():
            need = r["박스"] - F.at[j, "확인박스"]
            if need <= 0.5:
                continue
            win = gi[(gi["날짜"] < r["판매일"]) & (gi["날짜"] >= r["판매일"] - pd.Timedelta(days=LAG_MAX))].sort_values("날짜")
            for i in win.index:
                if need <= 0.5:
                    break
                t = min(need, left[i])
                if t > 0:
                    take(i, j, t, r["판매일"], "합계확인박스"); need -= t
            F.at[j, "짝"] = "합계(박스 맞춤)" if need <= 0.5 else ("일부" if need < r["박스"] else "")
        for i in gi.index:
            G.at[i, "_left"] = left[i]

    G["미확인박스"] = G["_left"].fillna(G["박스"])
    sale_days = {(c, m): set(x["판매일"]) for (c, m), x in F.groupby(["작물", "시장"])}
    G["넘긴경매팩스없음"] = [
        (r.미확인박스 > 0.5 and pd.notna(r.예정경매일) and r.예정경매일 not in sale_days.get((r.작물, r.시장), set()))
        for r in G.itertuples()]
    G["넘긴박스"] = np.where(G["넘긴경매팩스없음"], G["미확인박스"], 0.0)
    G["넘긴경매일"] = np.where(G["넘긴경매팩스없음"], G["예정경매일"].dt.strftime("%m/%d").fillna(""), "")
    # 출하일 단위 표
    S = G.groupby(["작물", "시장", "날짜"]).agg(
        출고박스=("박스", "sum"), 장부금액=("금액", lambda x: x.sum(min_count=1)), 미정박스=("박스단가", lambda x: 0),
        경매박스=("경매박스", "sum"), 이월박스=("이월박스", "sum"), 합계확인박스=("합계확인박스", "sum"), 미확인박스=("미확인박스", "sum"),
        경매금액=("경매금액", "sum"), 재정산=("재정산", "any"), 경매일=("경매일", lambda s: ", ".join(sorted(set(", ".join(s).split(", ")) - {""}))),
        넘긴박스=("넘긴박스", "sum"), 넘긴경매일=("넘긴경매일", lambda s: ", ".join(sorted(set(s) - {""}))),
    ).reset_index()
    und = G[G["박스단가"].isna()].groupby(["작물", "시장", "날짜"])["박스"].sum()
    S = S.set_index(["작물", "시장", "날짜"])
    S["미정박스"] = und.reindex(S.index).fillna(0)
    S = S.reset_index()
    span = F.groupby(["작물", "시장"])["판매일"].agg(["min", "max"])
    S = S.join(span, on=["작물", "시장"])
    inside = S["min"].notna() & (S["날짜"] >= S["min"] - pd.Timedelta(days=1)) & (S["날짜"] <= S["max"] - pd.Timedelta(days=1))
    S["차액"] = S["경매금액"] - S["장부금액"].fillna(0)
    S["상태"] = np.select(
        [~inside, S["미정박스"] > 0,
         (S["미확인박스"] > 0.5) & (S["넘긴박스"] >= S["미확인박스"] - 0.5),
         (S["미확인박스"] < 0.5) & S["재정산"] & (S["차액"].abs() > TOL),
         (S["미확인박스"] < 0.5) & (S["이월박스"] > 0),
         (S["미확인박스"] < 0.5) & (S["합계확인박스"] > 0),
         (S["미확인박스"] < 0.5),
         S["미확인박스"] >= S["출고박스"] - 0.5],
        ["팩스 기간 밖", "장부 단가 미정", "넘긴 경매 팩스 없음", "재정산", "일치(일부 다음 경매)", "일치(합계)", "일치", "팩스 없음"], "일부 미확인")
    S = S.drop(columns=["min", "max"])
    # 판매일 단위(팩스 쪽) 남은 것
    F["미확인박스"] = (F["박스"] - F["확인박스"]).clip(lower=0)
    return {"ship": S, "lots": G.drop(columns=["_left"]), "fax": F}


def _planned_sale(G):
    """포장실 비고 'M/D 정산' → 그 박스를 팔기로 한 경매일(출하일 기준으로 연도 맞춤). 없으면 NaT."""
    m = G["비고"].astype(str).str.extract(r"(\d{1,2})\s*/\s*(\d{1,2})\s*정산")
    out = pd.Series(pd.NaT, index=G.index, dtype="datetime64[ns]")
    ok = m[0].notna()
    for i in G.index[ok]:
        y = G.at[i, "날짜"].year
        try:
            d = pd.Timestamp(y, int(m.at[i, 0]), int(m.at[i, 1]))
            if d < G.at[i, "날짜"] - pd.Timedelta(days=180):
                d = pd.Timestamp(y + 1, int(m.at[i, 0]), int(m.at[i, 1]))
            out.at[i] = d
        except ValueError:
            pass
    return out


def _add_day(s, day):
    d = f"{day:%m/%d}"
    parts = [p for p in str(s or "").split(", ") if p]
    return ", ".join(sorted(set(parts + [d])))


def _gap_has_ship(G, gi, i, sale):
    """출하일과 판매일 사이에 그 시장 출하가 또 있었으면(=다음 경매로 넘어간 것) True"""
    d0 = G.at[i, "날짜"]
    return bool(((gi["날짜"] > d0) & (gi["날짜"] < sale)).any())


def _prev_ship(sale, gi, ship_days, last):
    k = sale - pd.Timedelta(days=1)
    if k in ship_days or k > last:
        return k
    prev = gi[(gi["날짜"] < k) & (gi["날짜"] >= k - pd.Timedelta(days=3))]["날짜"]
    return prev.max() if len(prev) else k


def settle(D):
    """정산 지급액 (작물, 시장, 판매일, 출하자) — 입금 대조용. 가정산·본정산은 본정산, 같은 문서는 한 번."""
    if D is None or D.empty:
        return pd.DataFrame()
    S = D[D["문서"] == "정산서"].copy()
    if S.empty:
        return pd.DataFrame()
    S = S.drop_duplicates(["시장", "날짜", "서명"]) if "서명" in S else S      # 같은 문서 재전송만 빼기 (날짜가 다른 똑같은 금액은 다른 거래)
    S["_공제"] = S[["수수료", "운임", "기타공제"]].sum(axis=1, min_count=1).fillna(0)
    S = S.sort_values("_공제", ascending=False).drop_duplicates(["작물", "시장", "날짜", "출하자", "수량", "금액"])
    s1 = lambda x: x.sum(min_count=1)
    # 한 장에 두 작물이 적힌 정산서는 작물별 줄로 나뉘어 있지만 통장 입금은 한 번 → 묶음 번호를 같이 둔다
    S["묶음"] = S["서명"].str.replace(r"#.*$", "", regex=True) if "서명" in S else S["파일"]
    return S.groupby(["시장", "날짜", "출하자", "작물"]).agg(판매대금=("금액", s1), 수수료=("수수료", s1), 운임=("운임", s1),
                                                        기타공제=("기타공제", s1), 지급액=("지급액", s1),
                                                        파일=("파일", lambda s: ", ".join(sorted(set(s)))),
                                                        묶음=("묶음", lambda s: "|".join(sorted(set(s))))).reset_index()


def deposits(ST, DEP, win=15):
    """정산서 지급액 ↔ 통장 입금 (같은 시장, 판매일부터 15일 안).
    ① 같은 금액 한 건 ② 이름 끝 숫자가 판매일(동화청과18 = 18일분)인 입금 여러 건의 합 ③ 두 건의 합.
    반환 (정산 표 + 입금액·입금일·입금대조, 입금 표 + 짝)"""
    import itertools
    if ST is None or ST.empty:
        return pd.DataFrame(), pd.DataFrame()
    ST = ST.sort_values("날짜").reset_index(drop=True).copy()
    ST["입금액"] = np.nan
    ST["입금일"] = pd.NaT
    ST["입금대조"] = ""
    if DEP is None or DEP.empty:
        ST["입금대조"] = "입금 자료 없음"
        return ST, pd.DataFrame()
    dep = DEP[DEP["구분"].isin(["", "송금"])].copy().reset_index(drop=True)
    dep["짝"] = ""
    last = dep["일시"].max()
    if "묶음" not in ST:
        ST["묶음"] = ST.index.astype(str)
    gsum = {}                                    # (시장, 날짜, 묶음) → 문서 전체 지급액 (두 작물로 나뉜 줄은 합쳐서 입금과 맞춘다)
    for _, r in ST.iterrows():
        k = (r["시장"], r["날짜"], r["묶음"])
        if pd.notna(r["지급액"]):
            gsum[k] = gsum.get(k, 0.0) + float(r["지급액"])
    done_grp = {}
    for i, r in ST.iterrows():
        a = gsum.get((r["시장"], r["날짜"], r["묶음"]), r["지급액"])
        gk = (r["시장"], r["날짜"], r["묶음"])
        if gk in done_grp:                       # 같은 문서의 다른 작물 줄 → 결과 복사
            ST.at[i, "입금액"], ST.at[i, "입금일"], ST.at[i, "입금대조"] = done_grp[gk]
            if pd.notna(ST.at[i, "입금액"]):
                ST.at[i, "입금액"] = r["지급액"]
            continue
        if pd.isna(a):
            ST.at[i, "입금대조"] = "지급액 없음"
            done_grp[gk] = (np.nan, pd.NaT, "지급액 없음")
            continue
        c = dep[(dep["시장"] == r["시장"]) & (dep["짝"] == "") & (dep["일시"] >= r["날짜"]) & (dep["일시"] <= r["날짜"] + pd.Timedelta(days=win))]
        one = c[(c["금액"] - a).abs() < 1]
        if len(one):
            j = one.index[0]
            dep.at[j, "짝"] = f"{r['날짜']:%m/%d} 정산"
            ST.at[i, "입금액"], ST.at[i, "입금일"], ST.at[i, "입금대조"] = r["지급액"], dep.at[j, "일시"], "일치"
            done_grp[gk] = (a, dep.at[j, "일시"], "일치")
            continue
        tag = c[c["꼬리"] == r["날짜"].day]
        if len(tag) >= 2 and abs(tag["금액"].sum() - a) < 1:
            dep.loc[tag.index, "짝"] = f"{r['날짜']:%m/%d} 정산(나눠 입금)"
            ST.at[i, "입금액"], ST.at[i, "입금일"], ST.at[i, "입금대조"] = r["지급액"], tag["일시"].max(), "나눠 입금"
            done_grp[gk] = (a, tag["일시"].max(), "나눠 입금")
            continue
        hit = None
        for j, k in itertools.combinations(list(c.index[:30]), 2):
            if abs(dep.at[j, "금액"] + dep.at[k, "금액"] - a) < 1:
                hit = (j, k)
                break
        if hit:
            dep.loc[list(hit), "짝"] = f"{r['날짜']:%m/%d} 정산(나눠 입금)"
            ST.at[i, "입금액"], ST.at[i, "입금일"], ST.at[i, "입금대조"] = r["지급액"], dep.loc[list(hit), "일시"].max(), "나눠 입금"
            done_grp[gk] = (a, dep.loc[list(hit), "일시"].max(), "나눠 입금")
            continue
        # 가정산(운송비 미공제) 정산서만 오고 본정산이 빠진 날 — 입금은 지급액에서 운송비(만 원 단위, 30만 원까지)를 뺀 금액 (v1.27.2, 대전농협 9/3·9/16 경리 확인)
        diff = a - c["금액"]
        fr = c[(diff >= 9999) & (diff <= 300001) & ((diff / 10000).round() * 10000 - diff).abs() < 2]
        if len(fr):
            j = fr.index[0]
            d_ = int(round(a - dep.at[j, "금액"]))
            dep.at[j, "짝"] = f"{r['날짜']:%m/%d} 정산(운송비 {d_:,}원 차감 추정)"
            ST.at[i, "입금액"], ST.at[i, "입금일"], ST.at[i, "입금대조"] = dep.at[j, "금액"], dep.at[j, "일시"], f"운송비 차감 추정({d_:,}원)"
            done_grp[gk] = (dep.at[j, "금액"], dep.at[j, "일시"], ST.at[i, "입금대조"])
            continue
        ST.at[i, "입금대조"] = "아직 입금 전" if r["날짜"] + pd.Timedelta(days=win) > last else "입금 못 찾음"
        done_grp[gk] = (np.nan, pd.NaT, ST.at[i, "입금대조"])
    other = DEP[~DEP["구분"].isin(["", "송금"])].assign(짝=lambda d: d["구분"])
    return ST, pd.concat([dep, other], ignore_index=True).sort_values("일시")


def deposits_by_auction(FXD, ST, DM, win=15, tol=0.012):
    """정산서는 없고 판매원표만 온 날 — 경락 합계 × (1 − 그 시장 평균 수수료율)이 남은 입금과 ±1.2% 안에서 맞으면 '추정 확인'.
    반환: 정산 표에 추가할 줄 [시장, 날짜(판매일), 출하자, 작물, 판매대금, 수수료(추정), 지급액(추정), 입금액, 입금일, 입금대조='추정 확인', 파일], 입금 표 갱신"""
    if FXD is None or FXD.empty or ST is None or ST.empty or DM is None or DM.empty:
        return pd.DataFrame(), DM
    ok = ST[ST["입금대조"].isin(["일치", "나눠 입금"])]
    rate = (1 - ok.groupby("시장")["지급액"].sum() / ok.groupby("시장")["판매대금"].sum()).to_dict()
    W = FXD[FXD["문서"] == "판매원표"]
    have = set(zip(ST["시장"], ST["날짜"], ST["작물"]))
    dm = DM.copy()
    rows = []
    for (mk, d, crop), g in W.groupby(["시장", "날짜", "작물"]):
        if (mk, d, crop) in have or mk not in rate:
            continue
        gross = g.drop_duplicates("서명")["금액"].sum() if "서명" in g else g["금액"].sum()
        if not gross:
            continue
        est = gross * (1 - rate[mk])
        c = dm[(dm["시장"] == mk) & (dm["짝"] == "") & (dm["구분"] == "") & (dm["일시"] >= d) & (dm["일시"] <= d + pd.Timedelta(days=win))]
        hit = c[((c["금액"] - est).abs() / est) <= tol]
        if len(hit):
            j = hit.index[0]
            dm.at[j, "짝"] = f"{d:%m/%d} 경락 추정(정산서 없음)"
            rows.append({"시장": mk, "날짜": d, "출하자": "·".join(sorted(set(g["출하자"]))), "작물": crop, "판매대금": gross, "수수료": gross - dm.at[j, "금액"],
                         "운임": np.nan, "기타공제": np.nan, "지급액": dm.at[j, "금액"], "입금액": dm.at[j, "금액"], "입금일": dm.at[j, "일시"],
                         "입금대조": "추정 확인", "파일": ", ".join(sorted(set(g["파일"]))), "묶음": f"est|{mk}|{d:%Y%m%d}|{crop}"})
    return pd.DataFrame(rows), dm


# ───────────────────────── 한 줄 요약: 출하일마다 출고 → 경매 → 정산·입금 → 명세표 (v1.26.5) ─────────────────────────
def _ship_of(sale, crop, mk, MD):
    """판매일 → 그 경매에 나간 출하일(그 시장 바로 앞 출고일, 4일 안). 없으면 판매일 하루 전."""
    m = MD[(MD["작물"] == crop) & (MD["시장"] == mk) & (MD["날짜"] < sale) & (MD["날짜"] >= sale - pd.Timedelta(days=4))]["날짜"]
    return m.max() if len(m) else sale - pd.Timedelta(days=1)


def unified(J, TW, ST, MD, tol=1000):
    """(작물, 시장, 출하일) 한 줄 = 그날 출고가 경매·정산·입금·명세표까지 맞는지.
    반환 열: 작물, 시장, 날짜(출하일), 출고박스, 장부금액, 미정박스, 확인박스, 미확인박스, 경매금액, 경매일, 팩스상태,
            판매대금, 수수료, 지급액, 입금액, 입금상태, 명세표, 명세표상태, 등급(확인/참고/대기/정상), 문제"""
    keys = ["작물", "시장", "날짜"]
    parts = []
    if J is not None and len(J):
        j = J[keys + [c for c in ("장부박스", "장부금액", "미정박스", "명세표", "구분", "팩스박스", "팩스금액") if c in J]].rename(columns={"구분": "명세표상태"})
        parts.append(j)
    S = TW.get("ship") if isinstance(TW, dict) else None
    if S is not None and len(S):
        s = S[S["상태"] != "팩스 기간 밖"].copy()
        s["확인박스"] = s["경매박스"] + s["이월박스"] + s["합계확인박스"]
        s = s[keys + ["출고박스", "확인박스", "미확인박스", "경매금액", "경매일", "재정산", "상태", "넘긴경매일"]].rename(columns={"상태": "팩스상태"})
        parts.append(s)
    if not parts:
        return pd.DataFrame()
    U = parts[0]
    for p in parts[1:]:
        U = U.merge(p, on=keys, how="outer")
    if ST is not None and len(ST) and MD is not None and len(MD):
        st_ = ST.copy()
        st_["출하일"] = [_ship_of(r.날짜, r.작물, r.시장, MD) for r in st_.itertuples()]
        rank = {"입금 못 찾음": 3, "아직 입금 전": 2, "나눠 입금": 1, "일치": 1, "추정 확인": 0.5, "지급액 없음": 0, "입금 자료 없음": 0, "": 0}
        st_["_r"] = st_["입금대조"].map(rank)
        st_.loc[st_["입금대조"].astype(str).str.startswith("운송비 차감 추정"), "_r"] = 0.7
        st_["_r"] = st_["_r"].fillna(0)
        s1 = lambda x: x.sum(min_count=1)
        g = st_.groupby(["작물", "시장", "출하일"]).agg(판매대금=("판매대금", s1), 수수료=("수수료", s1), 지급액=("지급액", s1),
                                                     입금액=("입금액", s1), _r=("_r", "max")).reset_index().rename(columns={"출하일": "날짜"})
        g["입금상태"] = g["_r"].map({3: "입금 못 찾음", 2: "아직 입금 전", 1: "입금 확인", 0.7: "입금 확인(운송비 차감)", 0.5: "입금 확인(추정)", 0: ""})
        U = U.merge(g.drop(columns="_r"), on=keys, how="left")
    for c in ("출고박스", "장부박스", "장부금액", "미정박스", "명세표", "확인박스", "미확인박스", "경매금액", "판매대금", "수수료", "지급액", "입금액"):
        if c not in U:
            U[c] = np.nan
    for c in ("명세표상태", "팩스상태", "입금상태", "경매일"):
        if c not in U:
            U[c] = ""
        U[c] = U[c].fillna("")
    # 팩스(정산서)가 시작되기 전 출하는 경매·입금을 건별로 맞출 수 없다 — 빈 칸 대신 그렇게 적는다
    F_ = TW.get("fax") if isinstance(TW, dict) else None
    if F_ is not None and len(F_):
        # 시장(작물)마다 팩스(또는 서부청과 엑셀)가 시작된 날이 다르다 — 그 전 출하는 '팩스 전' (v1.27.2: 시장별)
        first = (F_.groupby(["작물", "시장"])["판매일"].min() - pd.Timedelta(days=1))
        U["_fs"] = [first.get((c, m), pd.NaT) for c, m in zip(U["작물"], U["시장"])]
        pre = U["_fs"].isna() | (U["날짜"] < U["_fs"])
        U.loc[pre & (U["팩스상태"] == ""), "팩스상태"] = "팩스 전"
        U.loc[pre & (U["입금상태"] == ""), "입금상태"] = ["팩스 전" + (f"({d:%m/%d}~)" if pd.notna(d) else "") for d in U.loc[pre & (U["입금상태"] == ""), "_fs"]]
        U.loc[~pre & (U["입금상태"] == "") & U["지급액"].isna(), "입금상태"] = "정산서 없음"
        U = U.drop(columns=["_fs"])
    U["출고박스"] = U["출고박스"].fillna(U["장부박스"])
    U["재정산"] = U["재정산"].astype("boolean").fillna(False).astype(bool) if "재정산" in U else False

    def judge(r):
        red, ref, wait = [], [], []
        f, s, d = r["팩스상태"], r["명세표상태"], r["입금상태"]
        if f == "팩스 없음":
            red.append("경매 팩스 없음")
        elif f == "넘긴 경매 팩스 없음":
            msg = f"{r['미확인박스']:,.0f}박스는 {r.get('넘긴경매일', '')} 경매분(장부 비고) — 그날 팩스만 없음"
            (ref if s == "일치" else red).append(msg + (" · 장부·명세표 금액은 일치" if s == "일치" else ""))
        elif f == "일부 미확인":
            led_, stm_, fxa_, fxb_ = r["장부금액"], r["명세표"], r.get("팩스금액"), r.get("팩스박스")
            same_box = pd.notna(fxb_) and abs(fxb_ - (r["출고박스"] or 0)) < 0.5
            if s == "일치":          # 돈(명세표=장부)은 맞고 팩스 줄 단위(kg 단가·작은 상자)가 달라 박스만 못 맞춘 것
                ref.append(f"팩스와 박스 단위가 달라 {r['미확인박스']:,.0f}박스 못 맞춤(금액은 명세표와 같음)")
            elif same_box and pd.notna(stm_) and pd.notna(fxa_) and abs(stm_ - fxa_) <= tol:
                # 박스 수는 팩스와 같고 명세표도 팩스와 같다 — 장부에 단가를 잘못 적은 것 (서울 9/13: 장부 13,500 / 팩스 13,000, 경리 확인)
                ref.append(f"장부 단가 입력 차이({r['미확인박스']:,.0f}박스, 장부가 팩스보다 {(led_ or 0) - (fxa_ or 0):+,.0f}원) — 명세표는 팩스와 일치")
                s = "_단가차이"
            else:
                red.append(f"경매에서 {r['미확인박스']:,.0f}박스 확인 안 됨")
        elif f == "장부 단가 미정":
            wait.append("장부 단가 미정")
        if d == "입금 못 찾음":
            red.append("입금 못 찾음")
        elif d == "아직 입금 전":
            wait.append("아직 입금 전")
        elif d == "입금 확인(추정)":
            ref.append("정산서 없음 — 경락액×평소 수수료율로 입금 확인")
        elif d == "입금 확인(운송비 차감)":
            ref.append(f"본정산 없이 가정산만 옴 — 지급액에서 운송비 {((r['지급액'] or 0) - (r['입금액'] or 0)):,.0f}원을 뺀 입금 확인")
        elif d == "정산서 없음" and f not in ("팩스 없음", "넘긴 경매 팩스 없음"):
            wait.append("정산서 없음(판매원표만 옴)")
        led, stm, auc = r["장부금액"], r["명세표"], r["경매금액"]
        if s in ("금액 다름", "단가 미정"):
            if pd.notna(auc) and auc and pd.notna(stm) and abs(stm - auc) <= tol:
                (ref if r["재정산"] else red).append("장부 금액이 시장 정산과 다름(명세표는 팩스와 같음)" + (" · 재정산" if r["재정산"] else ""))
            else:
                red.append(f"명세표 금액 다름({(stm or 0) - (led or 0):+,.0f}원)")
        elif s == "명세표에 없음":
            red.append("명세표에 없음")
        elif s == "장부에 없음":
            red.append("장부에 없음(명세표에만 있음)")
        elif s == "명세표 아직 없음":
            wait.append("명세표 아직 없음")
        if (r["출고박스"] or 0) == 0 and (pd.notna(auc) and auc or pd.notna(r["지급액"]) and r["지급액"]):
            # 장부엔 그날 그 작물 출고가 없는데 팩스·정산에는 있음 — 한 장에 두 작물이 섞인 정산서(소량)면 참고, 아니면 확인
            amt = r["지급액"] if pd.notna(r["지급액"]) else (auc or 0)
            (ref if amt < 500000 else red).append(f"장부엔 출고 없음, 시장 정산에만 있음({amt:,.0f}원)")
        if f == "재정산" and not any("재정산" in x for x in red + ref):
            ref.append(f"재정산(팩스 첫 경락과 {(auc or 0) - (led or 0):+,.0f}원)")
        if f == "일치(합계)" and not red:
            pass
        if red:
            return "확인", " · ".join(red + ref + wait)
        if ref:
            return "참고", " · ".join(ref + wait)
        if wait:
            return "대기", " · ".join(wait)
        return "정상", ""
    for c in ("판매대금", "수수료", "지급액", "입금액", "경매금액"):
        U[c] = U[c].round(0)
    out = U.apply(judge, axis=1, result_type="expand")
    U["등급"], U["문제"] = out[0], out[1]
    U = _offset_pairs(U)
    return U.sort_values(["날짜", "작물", "시장"]).reset_index(drop=True)


def _offset_pairs(U, days=7):
    """명세표 금액 차이가 같은 시장·작물의 7일 안 다른 날과 +x / −x 로 상쇄되면 — 경리가 일부 박스를 다른 경매일에 입력한 것
    (구리 9/8 +15,000 · 9/11 −15,000: 새송이 2kg 30박스를 9/12 경매로 입력, 경리 확인) → 🔴 대신 🟡 (v1.27.2)"""
    if U is None or U.empty or "문제" not in U:
        return U
    pat = r"명세표 금액 다름\(([+-][\d,]+)원\)"
    X = U[U["문제"].astype(str).str.contains(pat, regex=True)].copy()
    if X.empty:
        return U
    X["_d"] = X["문제"].astype(str).str.extract(pat)[0].str.replace(",", "").astype(float)
    used = set()
    for i, r in X.iterrows():
        if i in used:
            continue
        c = X[(X["작물"] == r["작물"]) & (X["시장"] == r["시장"]) & (X.index != i) & ~X.index.isin(used)
              & ((X["날짜"] - r["날짜"]).abs() <= pd.Timedelta(days=days)) & ((X["_d"] + r["_d"]).abs() < 1)]
        if c.empty:
            continue
        j = c.index[0]
        used.update([i, j])
        for a, b in ((i, j), (j, i)):
            msg = f"명세표 입력 날짜 차이({X.at[a, '_d']:+,.0f}원 — {U.at[b, '날짜']:%m/%d}와 상쇄)"
            parts = [x for x in str(U.at[a, "문제"]).split(" · ") if "명세표 금액 다름" not in x]
            U.at[a, "문제"] = " · ".join([msg] + parts)
            if U.at[a, "등급"] == "확인" and not any(("못 찾음" in x) or ("확인 안 됨" in x) or ("팩스 없음" in x) or ("없음(" in x) for x in parts):
                U.at[a, "등급"] = "참고"
    return U


def fill_by_ledger(U, DM, win=16, tol=0.012):
    """경매 팩스(정산서)가 없는 출하일 — 출고장 금액(= 명세표 금액) × (1 − 그 시장 평소 수수료율)과 ±1.2% 안에서 맞는
    남은 입금이 있으면 '입금 확인(추정·장부)'. 팩스 전 기간(8/12 이전)도 이걸로 채운다 (v1.26.10).
    수수료율은 정산서와 입금이 원 단위로 맞은 건들에서 시장별로 계산해 U 의 '수수료율' 열로 넘겨받거나 여기서 다시 센다."""
    if U is None or U.empty or DM is None or DM.empty:
        return U, DM
    ok = U[U["입금상태"] == "입금 확인"]
    ok = ok[ok["판매대금"].notna() & ok["지급액"].notna() & (ok["판매대금"] > 0)]
    rate = (1 - ok.groupby("시장")["지급액"].sum() / ok.groupby("시장")["판매대금"].sum()).to_dict()
    dm = DM.copy()
    need = U[U["지급액"].isna() & (U["장부금액"].fillna(0) > 0) & (U["미정박스"].fillna(0) == 0)
             & ~U["입금상태"].isin(["입금 확인", "입금 확인(추정)"])].sort_values("날짜")
    # 시장별로 '아직 짝 없는 일반 입금'만 미리 추려 두고(일시 순), 날짜 창은 이진 탐색 — 매번 전체 표를 거르지 않는다 (v1.27.1, 10초 → 0.1초)
    free = dm[(dm["짝"] == "") & (dm["구분"] == "")]
    pool = {}
    for mk, g in free.groupby("시장"):
        g = g.sort_values("일시")
        pool[mk] = {"idx": g.index.to_numpy(), "t": g["일시"].to_numpy(), "a": g["금액"].to_numpy(dtype=float), "used": np.zeros(len(g), dtype=bool)}
    for i, r in need.iterrows():
        mk = r["시장"]
        if mk not in rate or mk not in pool:
            continue
        est = r["장부금액"] * (1 - rate[mk])
        d0 = r["날짜"]
        P = pool[mk]
        lo = np.searchsorted(P["t"], np.datetime64(d0), side="right")
        hi = np.searchsorted(P["t"], np.datetime64(d0 + pd.Timedelta(days=win)), side="right")
        if lo >= hi:
            continue
        seg = np.arange(lo, hi)
        ok_ = seg[(~P["used"][lo:hi]) & (np.abs(P["a"][lo:hi] - est) / est <= tol)]
        if len(ok_):
            k = ok_[0]
            P["used"][k] = True
            j = P["idx"][k]
            dm.at[j, "짝"] = f"{d0:%m/%d} 출하 추정(팩스 없음, 장부액×수수료율)"
            U.at[i, "입금액"] = dm.at[j, "금액"]
            U.at[i, "지급액"] = dm.at[j, "금액"]
            U.at[i, "수수료"] = r["장부금액"] - dm.at[j, "금액"]
            U.at[i, "입금상태"] = "입금 확인(추정·장부)"
            f = U.at[i, "팩스상태"]
            msg = ("경매 팩스 없음" if f in ("팩스 없음", "팩스 전", "") else U.at[i, "문제"].split(" · ")[0]) + " — 장부액×평소 수수료율로 입금 확인"
            rest = [x for x in str(U.at[i, "문제"]).split(" · ") if x and "팩스 없음" not in x and "정산서 없음" not in x and "입금" not in x]
            U.at[i, "문제"] = " · ".join([msg] + rest)
            if U.at[i, "등급"] == "확인" and not any(("금액 다름" in x) or ("못 찾음" in x) or ("확인 안 됨" in x) or ("없음(" in x) for x in rest):
                U.at[i, "등급"] = "참고"
            elif U.at[i, "등급"] == "대기" and not rest:
                U.at[i, "등급"] = "참고"
    return U, dm
