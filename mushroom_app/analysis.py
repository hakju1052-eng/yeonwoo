# -*- coding: utf-8 -*-
"""배치 사슬·일차별 환경·월별 지표·이상 감시 규칙."""
import numpy as np
import pandas as pd
from . import config as C
from . import etl_env, etl_logs, etl_retort

ENV_COLS = ["temp", "hum", "co2", "sp_temp", "sp_hum", "sp_co2", "heat", "cool", "humid", "dehum", "co2r",
            "run", "temp_min", "temp_max", "temp_sd", "err", "track_err", "reset_n", "day_last", "가습주기"]


def env_daily():
    d = etl_env.daily_view()
    return d


# ───────────────────────── 팽이 ─────────────────────────
def paengi_harvests():
    h = etl_logs.load("paengi_harvest")
    if h.empty:
        return pd.DataFrame()
    rows = []
    for _, r in h.iterrows():
        rooms = [int(x) for x in str(r.harvest_rooms or "").replace("，", ",").split(",") if x.strip().isdigit()]
        for rm in rooms:
            rows.append({"harvest": r.date, "생육실": rm, "n_rooms": len(rooms), "gpb": r.g_per_bottle,
                         "bottles": r.bottles, "grade_top": r.grade_top})
    H = pd.DataFrame(rows)
    q = etl_logs.load("paengi_qc")
    if not q.empty:
        q = q[q.kind == "본수확"].groupby(["date", "room"]).agg(QC=("score", "mean"), QC서식=("fmt", "first"), QC일차=("day_n", "first")).reset_index()
        H = H.merge(q, left_on=["harvest", "생육실"], right_on=["date", "room"], how="left").drop(columns=["date", "room"])
    return H


def qc_lookup(room, d0, d1, near=2):
    """그 방의 QC 점수를 찾는다. 본수확 우선, 없으면 점검수확. 수확일과 정확히 안 맞으면 앞뒤 near일까지 본다.
    반환: (점수, 서식, 종류) — 없으면 (nan, None, None)."""
    q = etl_logs.load("paengi_qc")
    if q.empty:
        return (np.nan, None, None)
    w = q[(q.room == room) & (q.date >= pd.Timestamp(d0) - pd.Timedelta(days=near)) & (q.date <= pd.Timestamp(d1) + pd.Timedelta(days=near))]
    if w.empty:
        return (np.nan, None, None)
    for kind in ("본수확", "점검수확"):
        k = w[w.kind == kind]
        if k.empty:
            continue
        # 수확 구간 안의 값을 먼저, 없으면 가장 가까운 날
        inside = k[(k.date >= pd.Timestamp(d0)) & (k.date <= pd.Timestamp(d1))]
        k = inside if not inside.empty else k.assign(_gap=(k.date - pd.Timestamp(d0)).abs()).nsmallest(1, "_gap")
        return (float(k.score.mean()), k.fmt.dropna().iloc[0] if k.fmt.notna().any() else None, kind)
    return (np.nan, None, None)


def paengi_chain():
    """권지 일지 기준 사슬: 접종 → 균긁기(발이실 입상) → 권지(생육실 입실) → 수확."""
    K = etl_logs.load("cult_kwonji")
    if K.empty:
        return pd.DataFrame()
    K = K.dropna(subset=["권지일", "입상일", "생육실"]).copy()
    K["생육실"] = K["생육실"].astype(int)
    P = etl_logs.load("cult_scrape_paengi")
    if not P.empty:
        P = P.dropna(subset=["긁기일"]).copy()
        P["방"] = pd.to_numeric(P["방"], errors="coerce")
        P = P.groupby(["긁기일", "방"]).agg(배양출고=("배양출고", "sum"), 긁기불량=("불량", "sum"), 탱크=("탱크", "first"), 접종일_긁기=("접종일", "first")).reset_index()
        K = K.merge(P, left_on=["입상일", "발이실"], right_on=["긁기일", "방"], how="left").drop(columns=["긁기일", "방"], errors="ignore")
    J = etl_logs.load("cult_inoc")
    if not J.empty:
        J = J[J["품목"] == "팽이"].groupby("일자").agg(접종량=("접종량", "sum"), 접종손실=("손실", "sum")).reset_index()
        K = K.merge(J, left_on="접종일", right_on="일자", how="left").drop(columns=["일자"], errors="ignore")
    K["긁기불량률%"] = 100 * K["긁기불량"] / K["배양출고"]
    K["권지불량률%"] = 100 * K["불량"] / K["입상량"]
    K["접종손실률%"] = 100 * K["접종손실"] / (K["접종량"] + K["접종손실"])
    K["배양일수"] = (K["입상일"] - K["접종일"]).dt.days
    K["발이실체류"] = (K["권지일"] - K["입상일"]).dt.days
    H0 = etl_logs.load("paengi_harvest")
    if not H0.empty:                                   # 일지가 있는 기간만
        K = K[K["권지일"] >= H0.date.min() - pd.Timedelta(days=30)]
    K = K.sort_values(["권지일", "생육실"]).reset_index(drop=True)
    K["bid"] = ["P" + d.strftime("%y%m%d") + f"-{r:02d}" for d, r in zip(K["권지일"], K["생육실"])]
    # 수확 연결: 같은 생육실, 권지일 이후 4~25일 안의 첫 수확 구간
    H = paengi_harvests()
    if not H.empty:
        H = H.sort_values("harvest")
        link = []
        for _, b in K.iterrows():
            h = H[(H["생육실"] == b["생육실"]) & (H.harvest >= b["권지일"] + pd.Timedelta(days=4)) & (H.harvest <= b["권지일"] + pd.Timedelta(days=25))]
            if h.empty:
                continue
            # 연속된 수확일만(다음 배치 수확과 섞이지 않게: 첫 수확 후 6일 이내)
            h = h[h.harvest <= h.harvest.min() + pd.Timedelta(days=6)]
            single = h[h.n_rooms == 1]
            qc_v, qc_f, qc_k = (h.QC.mean() if "QC" in h else np.nan), (h["QC서식"].dropna().iloc[0] if "QC서식" in h and h["QC서식"].notna().any() else None), ("본수확" if "QC" in h and h.QC.notna().any() else None)
            if pd.isna(qc_v):                      # 수확일에 본수확 QC가 없으면 점검수확·앞뒤 날짜에서 찾는다
                qc_v, qc_f, qc_k = qc_lookup(b["생육실"], h.harvest.min(), h.harvest.max())
            link.append({"bid": b.bid, "수확시작": h.harvest.min(), "수확종료": h.harvest.max(), "수확일수": len(h), "QC종류": qc_k,
                         "병당중량": single.gpb.mean() if len(single) else np.nan, "단일방수확": len(single) > 0,
                         "병당중량_합산": h.gpb.mean(), "합산방수": int(h.n_rooms.max()), "실수확병수": h.bottles.sum(), "상등급비중": h.grade_top.mean(),
                         "QC": qc_v, "QC서식": qc_f})
        if link:
            K = K.merge(pd.DataFrame(link), on="bid", how="left")
    if "병당중량" in K:
        # 표시용 병당중량: 그 방만 수확한 날 값이 있으면 그것, 없으면 같은 날 함께 수확한 방들의 평균(일지가 방별로 나누지 않음)
        K["병당중량_표시"] = K["병당중량"].where(K["단일방수확"].fillna(False), K["병당중량_합산"])
        K["중량기준"] = np.where(K["단일방수확"].fillna(False), "단일방", np.where(K["병당중량_합산"].notna(), K["합산방수"].fillna(0).astype(int).astype(str) + "방 합산", "—"))
    else:
        K["병당중량_표시"], K["중량기준"] = np.nan, "—"
    K["생육실체류"] = (K["수확시작"] - K["권지일"]).dt.days if "수확시작" in K else np.nan
    K["총일수"] = (K["수확시작"] - K["입상일"]).dt.days if "수확시작" in K else np.nan
    K["season"] = K["입상일"].dt.month.map(C.SEASON)
    K["month"] = K["입상일"].dt.strftime("%Y-%m")
    K["완결"] = K["수확시작"].notna() if "수확시작" in K else False
    return grade_batches(K, "팽이")


def grade_batches(df, crop):
    """배치 종합평가 — QC 점수·불량률·병당중량 셋을 '같은 계절의 수확 완료 배치'와 견주어 매긴다.
    열 추가: 종합점수(정렬용 합성값), 계절상위%·계절순위·계절배치수, 종합평가(좋음/보통/미흡 — 같은 계절·같은 QC서식 안 순위 1/4 기준), QC판정(품질 기준서 앵커 절대 판정, 팽이 서식 B), 평가근거.
      팽이   : QC(100점) ↑ · 긁기불량률%+권지불량률% ↓ · 병당중량_표시(g) ↑
      새송이 : QC종합(50점) ↑ · 균긁기 불량률(긁기불량_방/배양출고_방) ↓ · 병당g ↑
    점수 = 세 지표의 백분위(불량률은 낮을수록 높게)를 같은 비중(1/3)으로 평균. 셋 다 있는 배치만 점수를 준다(빠지면 '—').
    같은 계절끼리 비교하는 이유: 여름 배치는 QC·중량이 전반적으로 낮아서 전체와 견주면 늘 '미흡'이 되기 때문.
    등급: 같은 계절 순위 상위 25% 안이면 좋음, 하위 25% 안이면 미흡, 그 사이 보통."""
    if df is None or df.empty:
        return df
    df = df.copy()
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(np.nan, index=df.index)
    if crop == "팽이":
        qc = num("QC")
        b1, b2 = num("긁기불량률%"), num("권지불량률%")
        bad = b1.fillna(0) + b2.fillna(0)
        bad[b1.isna() & b2.isna()] = np.nan
        wt = num("병당중량_표시")
        qc_txt = lambda v: f"QC {v:.1f}"
    else:
        qc = num("QC종합")
        out, nb = num("배양출고_방"), num("긁기불량_방")
        bad = 100 * nb / out.where(out > 0)
        wt = num("병당g")
        qc_txt = lambda v: f"QC {v:.0f}/50"
    done = df["완결"].fillna(False).astype(bool) if "완결" in df else pd.Series(True, index=df.index)
    season = df["season"].fillna("") if "season" in df else pd.Series("", index=df.index)
    # QC 서식이 바뀌면(팽이: 2026-06-19 A→B) 점수 수준이 달라 서로 견줄 수 없다. 같은 계절이라도 서식이 같은 배치끼리만 견준다.
    fmt = df["QC서식"].fillna("") if "QC서식" in df else pd.Series("", index=df.index)
    group = season + "|" + fmt
    qc_p = pd.Series(np.nan, index=df.index); bad_p = pd.Series(np.nan, index=df.index); wt_p = pd.Series(np.nan, index=df.index)
    score = pd.Series(np.nan, index=df.index)
    rank_in = pd.Series(np.nan, index=df.index); n_in = pd.Series(np.nan, index=df.index)
    basis = pd.Series("", index=df.index, dtype=object)
    refkey = pd.Series("", index=df.index, dtype=object)       # 비교군을 이룬 '계절|QC서식' 묶음 (앞 계절과 합치면 둘)
    full = qc.notna() & bad.notna() & wt.notna()
    PREV = {"봄": "겨울", "여름": "봄", "가을": "여름", "겨울": "가을"}
    MIN_N = 8
    # 계절이 막 바뀌면 같은 계절 완료 배치가 몇 개 안 돼 순위를 못 매긴다 → 그 계절 배치가 MIN_N 개 될 때까지는 바로 앞 계절(같은 서식)과 합쳐 견준다.
    #  앞 계절 배치들의 등급은 바꾸지 않는다(그 배치들은 자기 계절끼리 매긴 그대로).
    for g in group.unique():
        tgt = done & (group == g)
        if not tgt.any():
            continue
        se, fm = (g.split("|", 1) + [""])[:2]
        ref = tgt
        refkey[tgt] = g
        if (tgt & full).sum() < MIN_N and se in PREV:
            ref = done & group.isin([g, PREV[se] + "|" + fm])
            basis[tgt] = f"{PREV[se]}·{se}"
            refkey[tgt] = PREV[se] + "|" + fm + "+" + g
        p_ = {}
        for nm, v, hi in (("qc", qc, True), ("bad", bad, False), ("wt", wt, True)):
            r = v[ref]
            p_[nm] = (r.rank(pct=True) * 100 if hi else 100 - r.rank(pct=True) * 100) if r.notna().sum() >= 5 else pd.Series(np.nan, index=r.index)
        qc_p[tgt] = p_["qc"][tgt]; bad_p[tgt] = p_["bad"][tgt]; wt_p[tgt] = p_["wt"][tgt]
        sc = (p_["qc"] + p_["bad"] + p_["wt"]) / 3          # 셋 다 있어야 '종합'
        score[tgt] = sc[tgt]
        ok = sc.notna()
        if ok.any():
            rk = sc[ok].rank(ascending=False, method="min")
            hit = tgt & ok.reindex(df.index, fill_value=False)
            rank_in[hit] = rk.reindex(df.index)[hit]; n_in[hit] = int(ok.sum())
    df["종합점수"] = score.round(0)          # 정렬용 합성값(세 백분위 평균). 화면에는 안 보인다
    df["비교군키"] = refkey
    df["계절순위"] = rank_in; df["계절배치수"] = n_in
    top = (100 * rank_in / n_in).round(0)
    df["계절상위%"] = top
    # 등급: 같은 계절 상위 1/4 = 좋음, 하위 1/4 = 미흡
    df["종합평가"] = np.where(top.isna(), "—", np.where(top <= 25, "좋음", np.where(top > 75, "미흡", "보통")))
    # 품질 기준서 앵커로 만든 QC 절대 판정 (config.QC_BANDS, 서식별). 앵커가 없는 서식은 '—'
    bands = getattr(C, "QC_BANDS", {})
    def _qc_band(v, f):
        if pd.isna(v) or f not in bands:
            return "—"
        for lo, name in bands[f]:
            if v >= lo:
                return name
        return bands[f][-1][1]
    df["QC판정"] = [_qc_band(v, f) for v, f in zip(qc, fmt)] if crop == "팽이" else "—"
    why = []
    for i in df.index:
        parts = []
        if pd.notna(qc_p[i]):
            parts.append(f"{qc_txt(qc[i])} (상위 {max(1, 100 - qc_p[i]):.0f}%)")
        if pd.notna(bad_p[i]):
            parts.append(f"불량 {bad[i]:.2f}% (상위 {max(1, 100 - bad_p[i]):.0f}%)")
        if pd.notna(wt_p[i]):
            parts.append(f"병당 {wt[i]:.0f}g (상위 {max(1, 100 - wt_p[i]):.0f}%)")
        if pd.notna(rank_in[i]):
            parts.append((f"{basis[i]} 합쳐서(이번 계절 배치가 아직 적음)" if basis[i] else "같은 계절") + f"{'·같은 QC서식' if fmt[i] else ''} {int(n_in[i])}개 중 {int(rank_in[i])}위")
        why.append(" · ".join(parts) if parts else "")
    df["평가근거"] = why
    return df


def paengi_batch_env(K=None, env=None):
    """배치 × 일차(입상일=1일차) 환경. 발이실 구간과 생육실 구간을 이어 붙인다."""
    K = paengi_chain() if K is None else K
    env = env_daily() if env is None else env
    if K.empty or env.empty:
        return pd.DataFrame()
    e = env[env.farm == "1433"].set_index(["room", "date"])
    cult = cult_env_daily(env)
    rows = []
    for _, b in K.iterrows():
        end = b["수확종료"] if pd.notna(b.get("수확종료")) else b["권지일"] + pd.Timedelta(days=14)
        segs = []
        if pd.notna(b.get("접종일")) and not cult.empty:          # 배양 구간(배양실1·2 평균, 방 배정 기록 없음)
            for d in pd.date_range(b["접종일"], b["입상일"] - pd.Timedelta(days=1)):
                if d in cult.index:
                    r = cult.loc[d]
                    rec = {"bid": b.bid, "phase": "배양", "room": "배양실(평균)", "date": d, "cday": (d - b["입상일"]).days + 1}
                    rec.update({c: r[c] for c in ENV_COLS if c in r})
                    rows.append(rec)
        if pd.notna(b["발이실"]):
            segs.append((f"발이실{int(b['발이실']):02d}", b["입상일"], b["권지일"] - pd.Timedelta(days=1), "발이실"))
        segs.append((f"생육실{int(b['생육실']):02d}", b["권지일"], end, "생육실"))
        for room, d0, d1, phase in segs:
            for d in pd.date_range(d0, d1):
                if (room, d) in e.index:
                    r = e.loc[(room, d)]
                    rec = {"bid": b.bid, "phase": phase, "room": room, "date": d, "cday": (d - b["입상일"]).days + 1}
                    rec.update({c: r[c] for c in ENV_COLS if c in r})
                    rows.append(rec)
    return pd.DataFrame(rows)


def cult_env_daily(env=None):
    """배양실1·2 의 날짜별 평균 환경(운전중 기준)."""
    env = env_daily() if env is None else env
    if env.empty:
        return pd.DataFrame()
    c = env[env.room.isin(["배양실1", "배양실2"])]
    if c.empty:
        return pd.DataFrame()
    # 가습주기 같은 글자 열은 평균을 낼 수 없으므로 숫자 열만 고른다
    cols = [x for x in ENV_COLS if x in c and pd.api.types.is_numeric_dtype(c[x])]
    return c.groupby("date")[cols].mean()


def cult_env_monthly(env=None):
    """배양실 방별 월 평균 환경."""
    env = env_daily() if env is None else env
    if env.empty:
        return pd.DataFrame()
    c = env[env.room.isin(["배양실1", "배양실2", "냉각1실", "냉각실2"])].copy()
    c["m"] = c.date.dt.strftime("%Y-%m")
    g = c.groupby(["room", "m"]).agg(temp=("temp", "mean"), temp_min=("temp_min", "min"), temp_max=("temp_max", "max"),
                                    hum=("hum", "mean"), co2=("co2", "mean"), sp_temp=("sp_temp", "median"), track_err=("track_err", "mean")).reset_index()
    return g


# ───────────────────────── 새송이 ─────────────────────────
def ss_batches():
    ev = etl_logs.load("ss_events")
    daily = etl_logs.load("ss_daily")
    if ev.empty:
        return pd.DataFrame()
    ips = ev[ev.event == "입상"].groupby("date").room.count().rename("n_rooms")
    daily = daily.merge(ips, left_on="date", right_index=True, how="left")
    daily["bottles_per_room"] = daily.bottles / daily.n_rooms
    bpr = daily.set_index("date")["bottles_per_room"]
    B = []
    for room, g in ev.sort_values(["date", "event"]).groupby("room"):
        starts = g[g.event == "입상"].date.tolist()
        for i, s in enumerate(starts):
            nxt = starts[i + 1] if i + 1 < len(starts) else pd.NaT
            w = g[(g.date >= s) & ((g.date < nxt) if pd.notna(nxt) else True)]
            h = w[w.event == "수확"].date
            rec = {"bid": "S" + s.strftime("%y%m%d") + "-" + room, "room": room, "block": room[0], "입상일": s,
                   "season": C.SEASON[s.month], "month": s.strftime("%Y-%m"), "bottles": bpr.get(s, np.nan),
                   "뒤집기": w[w.event == "뒤집기"].date.min(), "솎기": w[w.event == "솎기"].date.min(),
                   "수확시작": h.min(), "수확종료": h.max(), "수확일수": h.nunique(),
                   "폐상": w[w.event == "폐상"].date.min(), "탈병": w[w.event == "탈병"].date.min(), "재입상": nxt,
                   # 일지에 적힌 작업 날짜 전부 (솎기는 하루~이틀, 수확은 여러 날)
                   "작업일": {e_: sorted(set(w[w.event == e_].date)) for e_ in ("뒤집기", "솎기", "수확", "폐상", "탈병")}}
            for k in ("뒤집기", "솎기", "수확시작", "수확종료", "폐상", "탈병", "재입상"):
                rec[k + "_d"] = (rec[k] - s).days if pd.notna(rec[k]) else np.nan
            rec["완결"] = pd.notna(rec["수확시작"])
            B.append(rec)
    B = pd.DataFrame(B).sort_values(["입상일", "room"]).reset_index(drop=True)
    S = etl_logs.load("cult_scrape_ss")
    if not S.empty and "방" in S:
        rows = []
        for _, r in S.dropna(subset=["긁기일"]).iterrows():
            for rm in etl_logs.ss_rooms(r.get("방_raw") if "방_raw" in S else None) or []:
                rows.append({"room": rm, "긁기일": r["긁기일"], "접종일": r["접종일"], "배양출고_방": r["배양출고"], "긁기불량_방": r["불량"]})
        if rows:
            M = pd.DataFrame(rows).drop_duplicates(["room", "긁기일"])
            B = B.merge(M, on="room", how="left")
            ok = (B["긁기일"] - B["입상일"]).dt.days.abs() <= 1
            B.loc[~ok, ["긁기일", "접종일", "배양출고_방", "긁기불량_방"]] = np.nan
            B = B.sort_values("긁기일", na_position="first").drop_duplicates("bid", keep="last").sort_values(["입상일", "room"]).reset_index(drop=True)
            B["배양일수"] = (B["입상일"] - B["접종일"]).dt.days
    B = _ss_attach_room_yield(B)
    G = grade_batches(B, "새송이")
    # 수확은 끝났는데 평가가 비어 있는 배치: 왜 비었는지(포장 진행 중·QC 미입력·불량 기록 없음) 적고 '대기'로 표시
    today = pd.Timestamp.today().normalize()
    for i in G.index[(G["종합평가"] == "—") & G["완결"].fillna(False)]:
        r = G.loc[i]; why = []
        pe = r.get("포장종료")
        if pd.isna(r.get("병당g")):
            why.append(f"포장 {pd.Timestamp(pe):%m/%d} 끝나면 수확량 반영" if pd.notna(pe) and pd.Timestamp(pe) > today else "수확량(생산량 파일) 미입력")
        if pd.isna(r.get("QC종합")):
            why.append("QC 미입력")
        if pd.isna(r.get("긁기불량_방")):
            why.append("균긁기 불량 기록 없음")
        if why:
            G.at[i, "종합평가"] = "대기"
            G.at[i, "평가근거"] = "평가 대기 — " + " · ".join(why)
    return G


SS_QC_COLS = ["생육", "대모양", "갓상태", "병해", "생산량점"]


def _ss_attach_room_qc(B):
    """정규철 '생산량' 파일의 실별QC(2026-03~) → 방 단위 수확kg·병당g·QC 점수.
    '새송이 방별 포장량'(2025-10~2026-04)이 없는 구간을 메운다. 이미 값이 있으면 덮어쓰지 않는다."""
    Q = etl_logs.load("ss_room_qc")
    if B.empty or Q.empty:
        return B
    Q = Q.dropna(subset=["입상일", "room"]).sort_values("입상일")
    today = pd.Timestamp.today().normalize()
    for c in ["QC종합"] + SS_QC_COLS:
        if c not in B:
            B[c] = np.nan
    for c in ("솎기_QC", "포장종료"):
        if c not in B:
            B[c] = pd.Series(pd.NaT, index=B.index, dtype="datetime64[ns]")
    for c in ("발이메모", "QC비고"):
        if c not in B:
            B[c] = pd.Series([None] * len(B), index=B.index, dtype=object)
    for i, b in B.iterrows():
        m = Q[(Q.room == b.room) & ((Q["입상일"] - b["입상일"]).dt.days.abs() <= 1)]
        if m.empty:
            continue
        q = m.iloc[0]
        if pd.isna(B.at[i, "입상병수_방별"]):
            B.at[i, "입상병수_방별"] = q["입상병수"]
        # 포장이 끝나지 않은 배치의 수확량은 집계 중(부분값)이라 쓰지 않는다
        done_pack = pd.notna(q.get("포장종료")) and pd.Timestamp(q["포장종료"]) <= today
        if pd.isna(B.at[i, "수확kg"]) and pd.notna(q["수확kg"]) and done_pack:
            B.at[i, "수확kg"] = q["수확kg"]
            B.at[i, "병당g"] = q["병당g"]
        for c in ["QC종합"] + SS_QC_COLS:
            B.at[i, c] = q.get(c)
        B.at[i, "발이메모"] = q.get("발이메모"); B.at[i, "QC비고"] = q.get("비고")
        B.at[i, "솎기_QC"] = pd.to_datetime(q.get("솎기"), errors="coerce")
        B.at[i, "포장종료"] = pd.to_datetime(q.get("포장종료"), errors="coerce")
    return B


def _ss_attach_room_yield(B):
    """방별 포장량 자료(새송이 방별 포장량.xlsx)가 있으면 배치별 실제 수확 kg·병당g를 붙인다.
    배치 창: 입상일 ~ (재입상 전날 | 입상+20일). 자료가 없는 배치는 NaN.
    그 뒤 정규철 실별QC 자료로 빈 구간을 메운다(_ss_attach_room_qc)."""
    for c in ("수확kg", "미니kg", "병당g", "미니비중%", "입상병수_방별"):
        B[c] = np.nan
    P = etl_logs.load("ss_room_pack")
    if B.empty:
        return B
    if P.empty:
        return _ss_attach_weather(_ss_attach_room_qc(B))
    P = P.dropna(subset=["date"]).sort_values("date")
    p_max = P.date.max()
    I = etl_logs.load("ss_room_in")
    for i, b in B.iterrows():
        end = b["재입상"] - pd.Timedelta(days=1) if pd.notna(b["재입상"]) else b["입상일"] + pd.Timedelta(days=20)
        end = min(end, b["입상일"] + pd.Timedelta(days=20))
        if end > p_max or b["입상일"] < P.date.min():       # 자료 범위를 벗어난(부분 집계) 배치는 제외
            continue
        w = P[(P.room == b.room) & (P.date >= b["입상일"]) & (P.date <= end)]
        if not I.empty:
            m = I[(I.room == b.room) & ((I["입상일"] - b["입상일"]).dt.days.abs() <= 1)]
            if len(m):
                B.at[i, "입상병수_방별"] = m["입상병수"].iloc[0]
        if w.empty:
            continue
        kg, mini = w.kg.sum(), w["미니kg"].sum()
        B.at[i, "수확kg"], B.at[i, "미니kg"] = kg, mini
        B.at[i, "미니비중%"] = 100 * mini / kg if kg else np.nan
        bottles = B.at[i, "입상병수_방별"] if pd.notna(B.at[i, "입상병수_방별"]) else b.get("bottles")
        if bottles and bottles > 0 and kg > 0:
            B.at[i, "병당g"] = 1000 * kg / bottles
    return _ss_attach_weather(_ss_attach_room_qc(B))


def _ss_attach_weather(B):
    """입상~수확종료 구간의 바깥 기온 평균·비 온 일수. 계절 효과를 배치 단위로 보기 위한 변수."""
    W = weather_daily()
    if B.empty or W.empty:
        return B
    w = W.set_index("date")[["외기온", "비"]]
    tt, rr = [], []
    for _, b in B.iterrows():
        end = b["수확종료"] if pd.notna(b.get("수확종료")) else b["입상일"] + pd.Timedelta(days=16)
        x = w.reindex(pd.date_range(b["입상일"], end))
        tt.append(x["외기온"].mean()); rr.append(x["비"].sum())
    B = B.copy(); B["외기온"] = tt; B["비일수"] = rr
    return B


def ss_batch_env(B=None, env=None):
    B = ss_batches() if B is None else B
    env = env_daily() if env is None else env
    if B.empty or env.empty:
        return pd.DataFrame()
    e = env[env.farm.isin(["1377", "1378"])].set_index(["room", "date"])
    cult = cult_env_daily(env)
    rows = []
    for _, b in B.iterrows():
        if pd.notna(b.get("접종일")) and not cult.empty:
            for d in pd.date_range(b["접종일"], b["입상일"] - pd.Timedelta(days=1)):
                if d in cult.index:
                    r = cult.loc[d]
                    rec = {"bid": b.bid, "phase": "배양", "room": "배양실(평균)", "date": d, "cday": (d - b["입상일"]).days}
                    rec.update({c: r[c] for c in ENV_COLS if c in r})
                    rows.append(rec)
        end = b["재입상"] - pd.Timedelta(days=1) if pd.notna(b["재입상"]) else b["입상일"] + pd.Timedelta(days=20)
        end = min(end, b["입상일"] + pd.Timedelta(days=20))
        # 폐상(또는 탈병)한 날까지만 — 그 뒤는 빈 방이거나 다음 배치 준비라 이 배치 기록이 아니다 (v1.23.3).
        # 마지막 수확보다 앞선 폐상·탈병은 앞 배치 것이 섞인 것이라 뺀다. 기록이 없으면 마지막 수확 다음 날까지.
        if pd.notna(b.get("수확종료")):
            he = pd.Timestamp(b["수확종료"])
            J = b.get("작업일") if isinstance(b.get("작업일"), dict) else {}
            cand = list(J.get("폐상", [])) + list(J.get("탈병", [])) + [b.get("폐상"), b.get("탈병")]
            clean = [pd.Timestamp(x) for x in cand if pd.notna(x) and pd.Timestamp(x) >= he]
            end = min(end, min(clean) if clean else he + pd.Timedelta(days=1))
        for d in pd.date_range(b["입상일"], end):
            if (b.room, d) in e.index:
                r = e.loc[(b.room, d)]
                cd = (d - b["입상일"]).days
                ph = "미완" if not b["완결"] else ("입상~뒤집기" if cd < (b["뒤집기_d"] if pd.notna(b["뒤집기_d"]) else 7)
                                              else "뒤집기~솎기" if cd < (b["솎기_d"] if pd.notna(b["솎기_d"]) else 11)
                                              else "솎기~수확" if cd < b["수확시작_d"] else "수확" if cd <= b["수확종료_d"] else "정리")
                rec = {"bid": b.bid, "phase": ph, "room": b.room, "date": d, "cday": cd, "작업": ss_day_task(b, d)}
                rec.update({c: r[c] for c in ENV_COLS if c in r})
                rows.append(rec)
    return pd.DataFrame(rows)


def ss_day_task(b, d):
    """새송이 배치의 그날 작업 (일지에 적힌 날짜 그대로): 입상 / 뒤집기 / 솎기 / 수확 / 폐상·탈병 / 생육.
    예) 0일 입상 → 1~5일 생육 → 6일 뒤집기 → 7~11일 생육 → 12일 솎기(이틀이면 12·13일) → 13~14일 생육 → 15일~ 수확."""
    d = pd.Timestamp(d).normalize()
    if d == pd.Timestamp(b["입상일"]).normalize():
        return "입상"
    J = b.get("작업일") if isinstance(b.get("작업일"), dict) else {}
    hit = [e_ for e_ in ("뒤집기", "솎기", "수확") if any(pd.Timestamp(x).normalize() == d for x in J.get(e_, []))]
    if hit:
        return "·".join(hit)
    last_h = max(J.get("수확", []) or [pd.NaT]) if J.get("수확") else pd.NaT
    if pd.notna(last_h) and d > pd.Timestamp(last_h):
        if any(pd.Timestamp(x).normalize() == d for x in J.get("폐상", []) + J.get("탈병", [])):
            return "폐상·탈병"
        return "수확 후"
    return "생육"


# ───────────────────────── 공통 ─────────────────────────
def profile(batch_env, bids=None, max_day=None, min_day=None):
    """일차별 평균 곡선."""
    e = batch_env if bids is None else batch_env[batch_env.bid.isin(bids)]
    if max_day is not None:
        e = e[e.cday <= max_day]
    if min_day is not None:
        e = e[e.cday >= min_day]
    if e.empty:
        return pd.DataFrame()
    g = e.groupby("cday").agg(n=("bid", "nunique"), temp=("temp", "mean"), sp_temp=("sp_temp", "mean"), hum=("hum", "mean"),
                              sp_hum=("sp_hum", "mean"), co2=("co2", "mean"), sp_co2=("sp_co2", "mean"),
                              cool=("cool", "mean"), heat=("heat", "mean"), humid=("humid", "mean"), co2r=("co2r", "mean"))
    return g.reset_index()


def paengi_monthly():
    d = etl_logs.load("paengi_daily")
    h = etl_logs.load("paengi_harvest")
    q = etl_logs.load("paengi_qc")
    if d.empty:
        return pd.DataFrame()
    d["m"] = d.date.dt.strftime("%Y-%m")
    out = d.groupby("m").agg(생산kg=("total_kg", "sum"), 입상병수=("ipsang", "sum"), 생산일=("total_kg", lambda s: (s.fillna(0) > 0).sum())).reset_index()
    if not h.empty:
        h["m"] = h.date.dt.strftime("%Y-%m")
        hh = h.groupby("m").agg(병당중량=("g_per_bottle", "mean"), 상등급비중=("grade_top", "mean")).reset_index()
        out = out.merge(hh, on="m", how="left")
    if not q.empty:
        q["m"] = q.date.dt.strftime("%Y-%m")
        for f in ("A", "B"):
            qq = q[(q.kind == "본수확") & (q.fmt == f)].groupby("m").score.mean().rename(f"QC(서식{f})").reset_index()
            out = out.merge(qq, on="m", how="left")
    return out.rename(columns={"m": "월"})


def ss_monthly(B=None):
    d = etl_logs.load("ss_daily")
    k = etl_logs.load("ss_kgcat")
    B = ss_batches() if B is None else B
    if d.empty:
        return pd.DataFrame()
    d["m"] = d.date.dt.strftime("%Y-%m")
    out = d.groupby("m").agg(생산kg=("kg", "sum"), 입상병수=("bottles", "sum")).reset_index()
    if not k.empty:
        k["m"] = k.date.dt.strftime("%Y-%m")
        kk = k.groupby("m").agg(kg=("kg", "sum"), 파지=("파지", "sum"), 미니=("미니", "sum"), 깎기=("깎기", "sum")).reset_index()
        for c in ("파지", "미니", "깎기"):
            kk[c + "%"] = 100 * kk[c] / kk.kg
        out = out.merge(kk[["m", "파지%", "미니%", "깎기%"]], on="m", how="left")
    if not B.empty:
        c = B[B["완결"]].copy()
        c["hm"] = c["수확시작"].dt.strftime("%Y-%m")
        # 그 달에 수확 시작한 배치의 입상 병수 ↔ 그 달 생산 kg (일 합계는 방별 분리가 안 되므로 월 단위만)
        kg = d.groupby("m").kg.sum()
        bb = c.groupby("hm").agg(수확배치=("bid", "count"), 배치병수=("bottles", "sum"), 수확소요일=("수확일수", "mean"), 재입상간격=("재입상_d", "mean")).reset_index().rename(columns={"hm": "m"})
        bb["병당g"] = [1000 * kg.get(m, np.nan) / b if b else np.nan for m, b in zip(bb.m, bb.배치병수)]
        out = out.merge(bb, on="m", how="left")
    return out.rename(columns={"m": "월"})


def cult_monthly():
    J = etl_logs.load("cult_inoc"); P = etl_logs.load("cult_scrape_paengi"); S = etl_logs.load("cult_scrape_ss"); K = etl_logs.load("cult_kwonji")
    rows = []

    def add(df, dcol, bad, den, label):
        if df.empty:
            return
        x = df.dropna(subset=[dcol]).copy(); x = x[x[den] > 0]
        x["m"] = x[dcol].dt.strftime("%Y-%m")
        g = x.groupby("m").agg(처리=(den, "sum"), 불량=(bad, "sum")).reset_index()
        g["항목"] = label; g["비율%"] = 100 * g.불량 / g.처리
        rows.append(g)
    if not J.empty:
        J["입병량"] = J["접종량"] + J["손실"]
        add(J[J.품목 == "팽이"], "일자", "손실", "입병량", "접종손실(팽이)")
        add(J[J.품목 == "새송이"], "일자", "손실", "입병량", "접종손실(새송이)")
    add(P, "긁기일", "불량", "배양출고", "균긁기불량(팽이)")
    add(S, "긁기일", "불량", "배양출고", "균긁기불량(새송이)")
    add(K, "권지일", "불량", "입상량", "권지불량(팽이)")
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def tank_rank(ref_date=None):
    """팽이 액탱크별 균긁기 불량률 순위. 복수 탱크 배치는 병수를 균등 배분. 열: 배치, 불량률%, 단독배치, 단독불량률%, 최근90일%, 월평균대비pp"""
    import re
    P = etl_logs.load("cult_scrape_paengi")
    if P.empty or "탱크" not in P:
        return pd.DataFrame()
    P = P.dropna(subset=["긁기일", "배양출고", "탱크"])
    rows = []
    for _, r in P.iterrows():
        tanks = [t for t in re.split(r"[,/ ]+", str(r["탱크"])) if t.strip().isdigit()]
        for t in tanks:
            rows.append({"탱크": int(t), "긁기일": r["긁기일"], "출고": r["배양출고"] / len(tanks), "불량": (r["불량"] or 0) / len(tanks),
                         "단독": len(tanks) == 1, "월": r["긁기일"].strftime("%Y-%m")})
    T = pd.DataFrame(rows)
    if T.empty:
        return T
    today = pd.Timestamp(ref_date) if ref_date else T["긁기일"].max()
    m = T.groupby("월").apply(lambda d: 100 * d["불량"].sum() / d["출고"].sum()).rename("월평균")
    T = T.merge(m, left_on="월", right_index=True)
    T["상대"] = 100 * T["불량"] / T["출고"] - T["월평균"]
    g = T.groupby("탱크").agg(배치=("긁기일", "count"), 출고=("출고", "sum"), 불량=("불량", "sum"), 월평균대비pp=("상대", "mean"))
    g["불량률%"] = 100 * g["불량"] / g["출고"]
    s1 = T[T["단독"]].groupby("탱크").agg(단독배치=("긁기일", "count"), o=("출고", "sum"), b=("불량", "sum"))
    g["단독배치"] = s1["단독배치"]; g["단독불량률%"] = 100 * s1.b / s1.o
    rc = T[T["긁기일"] >= today - pd.Timedelta(days=90)].groupby("탱크").agg(o=("출고", "sum"), b=("불량", "sum"))
    g["최근90일%"] = 100 * rc.b / rc.o
    g = g[["배치", "불량률%", "단독배치", "단독불량률%", "최근90일%", "월평균대비pp"]].round(2).sort_values("불량률%", ascending=False)
    g.attrs["period"] = (T["긁기일"].min().date(), T["긁기일"].max().date())
    return g


# ───────────────────────── 원가 ─────────────────────────
# 월말보고서(권석현)에 실린 병당 원가 — 앱 산식 검증용 참고값 (원/병)
REPORT_COST = {
    ("2026-04", "팽이"): {"원재료비": 135.29, "연료비": 21.68, "배양전기": 22.86, "생육전기": 24.64, "합계": 204.47},
    ("2026-05", "팽이"): {"원재료비": 135.58, "연료비": 21.66, "배양전기": 22.82, "생육전기": 23.42, "합계": 203.48},
    ("2026-06", "팽이"): {"원재료비": 138.64, "연료비": 23.07, "배양전기": 25.66, "생육전기": 29.82, "합계": 217.19},
    ("2026-07", "팽이"): {"원재료비": 143.20, "연료비": 22.67, "배양전기": 25.79, "생육전기": 34.09, "합계": 225.75},
    ("2026-08", "팽이"): {"원재료비": 145.78, "연료비": 24.74, "배양전기": 32.47, "생육전기": 38.78, "합계": 241.77},
    ("2026-04", "새송이"): {"원재료비": 123.93, "연료비": 21.68, "배양전기": 22.86, "생육전기": 26.62, "합계": 195.09},
    ("2026-05", "새송이"): {"원재료비": 123.61, "연료비": 21.66, "배양전기": 22.82, "생육전기": 18.87, "합계": 186.96},
    ("2026-06", "새송이"): {"원재료비": 125.55, "연료비": 23.07, "배양전기": 25.66, "생육전기": 21.24, "합계": 195.52},
    ("2026-07", "새송이"): {"원재료비": 130.00, "연료비": 22.67, "배양전기": 25.79, "생육전기": 26.46, "합계": 204.92},
    ("2026-08", "새송이"): {"원재료비": 132.73, "연료비": 24.74, "배양전기": 32.47, "생육전기": 35.40, "합계": 225.34},
    ("2026-01", "팽이"): {"원재료비": 131.17}, ("2026-01", "새송이"): {"원재료비": 119.39},
}


def _meter_role(name):
    for k, v in C.METER_ROLE.items():
        if k in str(name):
            return v
    return "기타"


def cost_monthly():
    """월·작물별 병당 원가 (원/병): 원재료비 + 연료비(LPG·경유, 공통) + 배양전기(공통) + 생육전기(작물별).
    입병수·작물별 재료비는 원재료입고.xlsx 블록 값을 우선 쓰고, 없으면 재배일보 입병수와 최근 배합비(kg/입병일)로 추정한다."""
    M, MB, BT, EL, FU = (etl_logs.load(k) for k in ("materials", "materials_block", "bottling", "electric", "fuel"))
    if M.empty:
        return pd.DataFrame(), pd.DataFrame()
    crops = ("팽이", "새송이")
    # 1) 입병수·입병일수
    bot = {}
    if not BT.empty:
        BT = BT.copy(); BT["month"] = BT["date"].dt.strftime("%Y-%m")
        for (m, c), g in BT.groupby(["month", "crop"]):
            bot[(m, c)] = {"입병수": g.bottles.sum(), "입병일수": g["date"].nunique(), "src": "재배일보"}
    if not MB.empty:
        for _, r in MB.iterrows():
            for c in crops:
                n, d = r.get(f"{c}_입병수"), r.get(f"{c}_입병일수")
                cur = bot.get((r.month, c), {})
                if pd.notna(n) and n:
                    cur = {"입병수": n, "입병일수": d if pd.notna(d) else cur.get("입병일수"), "src": "원재료입고"}
                elif pd.notna(d) and d and cur:
                    cur = {**cur, "입병일수": d}
                if cur:
                    bot[(r.month, c)] = cur
    # 2) 작물별 재료비: 파일 값 우선, 없으면 최근 배합비(kg/입병일)×입병일수×단가
    recipe = {}
    Ms = M.sort_values("month")
    for (m, item), g in Ms.groupby(["month", "item"]):
        r = g.iloc[-1]
        for c in crops:
            kg, d = r.get(f"{c}_kg"), bot.get((m, c), {}).get("입병일수")
            if pd.notna(kg) and kg and d:
                recipe[(c, item)] = (m, kg / d)          # 가장 최근 달 값이 남는다
    mat = {}
    for m, g in Ms.groupby("month"):
        for c in crops:
            won = g[f"{c}_원"].dropna()
            kgs = g[[f"{c}_kg", "단가"]].dropna()
            if len(won):
                mat[(m, c)] = {"원재료비_원": float(won.sum()), "src": "원재료입고(원)"}
            elif len(kgs):
                mat[(m, c)] = {"원재료비_원": float((kgs[f"{c}_kg"] * kgs["단가"]).sum()), "src": "원재료입고(kg×단가)"}
            else:
                d = bot.get((m, c), {}).get("입병일수")
                if not d:
                    continue
                tot = 0.0; ok = False
                for _, r in g.iterrows():
                    rc = recipe.get((c, r["item"]))
                    if rc and pd.notna(r["단가"]):
                        tot += rc[1] * d * r["단가"]; ok = True
                if ok:
                    mat[(m, c)] = {"원재료비_원": tot, "src": "배합비 추정"}
    # 3) 연료·전기 (월 합계)
    fuel = {}
    if not FU.empty:
        FU = FU.copy(); FU["month"] = FU["date"].dt.strftime("%Y-%m")
        fuel = {m: v for m, v in FU.groupby("month").amount.sum().items() if v and v > 0}   # 단가 미입력(금액 0)인 달은 제외
    elec = {}
    if not EL.empty:
        EL = EL.copy(); EL["role"] = EL.meter.map(_meter_role)
        for (m, role), g in EL.groupby(["month", "role"]):
            elec[(m, role)] = float(g.cost.fillna(0).sum())
    # 4) 합치기
    rows = []
    months = sorted({m for m, _ in mat} | {m for m, _ in bot})
    for m in months:
        n_tot = sum(bot.get((m, c), {}).get("입병수", 0) or 0 for c in crops)
        for c in crops:
            b = bot.get((m, c))
            if not b or not b.get("입병수"):
                continue
            n = b["입병수"]
            rec = {"월": m, "작물": c, "입병수": n, "입병일수": b.get("입병일수"), "입병수출처": b["src"]}
            mt = mat.get((m, c))
            rec["원재료비_원"] = mt["원재료비_원"] if mt else np.nan
            rec["원재료비"] = rec["원재료비_원"] / n if mt else np.nan
            rec["재료비출처"] = mt["src"] if mt else "—"
            f = fuel.get(m)
            rec["연료비_원"] = f if f is not None else np.nan
            rec["연료비"] = f / n_tot if (f is not None and n_tot) else np.nan
            cc = elec.get((m, "배양공통"))
            rec["배양전기_원"] = cc if cc is not None else np.nan
            rec["배양전기"] = cc / n_tot if (cc is not None and n_tot) else np.nan
            gc = elec.get((m, f"{c}생육"))
            rec["생육전기_원"] = gc if gc is not None else np.nan
            rec["생육전기"] = gc / n if gc is not None else np.nan
            parts = [rec["원재료비"], rec["연료비"], rec["배양전기"], rec["생육전기"]]
            rec["합계"] = sum(parts) if all(pd.notna(x) for x in parts) else np.nan
            ref = REPORT_COST.get((m, c))
            rec["보고서_합계"] = ref.get("합계") if ref else np.nan
            rec["보고서_원재료비"] = ref.get("원재료비") if ref else np.nan
            rows.append(rec)
    out = pd.DataFrame(rows)
    price = M.pivot_table(index="month", columns="item", values="단가", aggfunc="last") if not M.empty else pd.DataFrame()
    return out, price


# ───────────────────────── 재고 대사 ─────────────────────────
LEDGER_KEY = {"팽이": ["spec", "grade", "cust"], "새송이": ["name", "spec", "grade", "cust"]}


def stock_recon(crop):
    """입출고 원장 대사. 반환: (월별표, 월초조정 상세, 일별 음수잔고, 무가출고)
    월별표: 기초재고·입고·출고·계산기말 = 기초+입고−출고, 다음달기초, 월초조정 = 다음달기초 − 계산기말(실사·정정분), 기말재고일수."""
    L = etl_logs.load("ledger")
    if L.empty:
        return (pd.DataFrame(),) * 4
    L = L[L.crop == crop].copy()
    if L.empty:
        return (pd.DataFrame(),) * 4
    key = [k for k in LEDGER_KEY[crop] if k in L.columns]
    L["kg"] = pd.to_numeric(L["kg"], errors="coerce").fillna(0)
    IN, OUT = ("재고", "입고", "매입", "실사"), ("출고", "클레임출고", "기타출고", "폐기")
    months = sorted(L.month.unique())
    rows, items, neg = [], [], []
    for i, m in enumerate(months):
        g = L[L.month == m]
        op = g[g.type == "재고"].groupby(key).kg.sum()
        inn = g[g.type.isin(IN) & (g.type != "재고")].groupby(key).kg.sum()
        out = g[g.type.isin(OUT)].groupby(key).kg.sum()
        calc = op.add(inn, fill_value=0).sub(out, fill_value=0)
        days = g[g.type.isin(OUT)].date.nunique()
        avg = out.sum() / days if days else np.nan
        rec = {"월": m, "기초재고": op.sum(), "입고": inn.sum(), "출고": out.sum(), "계산기말": calc.sum(), "일평균출고": avg,
               "기말재고일수": calc.sum() / avg if avg else np.nan, "다음달기초": np.nan, "월초조정": np.nan, "조정률%": np.nan}
        if i + 1 < len(months):
            nxt = L[L.month == months[i + 1]]
            op2 = nxt[nxt.type == "재고"].groupby(key).kg.sum()
            d = pd.DataFrame({"계산기말": calc, "다음달기초": op2}).fillna(0)
            d["조정"] = d["다음달기초"] - d["계산기말"]
            rec.update({"다음달기초": op2.sum(), "월초조정": d["조정"].sum(), "조정률%": 100 * d["조정"].sum() / inn.sum() if inn.sum() else np.nan,
                        "항목별조정절대합": d["조정"].abs().sum()})
            for k, r in d[d["조정"].abs() > 1].iterrows():
                items.append({"월": m, **dict(zip(key, k if isinstance(k, tuple) else (k,))), "계산기말": r["계산기말"], "다음달기초": r["다음달기초"], "조정": r["조정"]})
        rows.append(rec)
        # 일별 음수 잔고 (출고가 장부 재고를 초과)
        for k, gg in g.groupby(key):
            bal = 0.0
            for d0, dd in gg.sort_values("date").groupby("date"):
                bal += dd[dd.type.isin(IN)].kg.sum() - dd[dd.type.isin(OUT)].kg.sum()
                if bal < -1:
                    neg.append({"월": m, "날짜": d0, **dict(zip(key, k if isinstance(k, tuple) else (k,))), "장부잔고kg": round(bal, 1)})
    R = pd.DataFrame(rows).round(1)
    o = L[L.type.isin(OUT)].copy(); o["amt"] = pd.to_numeric(o["amt"], errors="coerce").fillna(0)
    unp = o[o.amt <= 0][["month", "date", "type", "cust", "sub", "grade", "spec", "box", "kg", "note"]].rename(
        columns={"month": "월", "date": "날짜", "type": "구분", "cust": "거래처", "sub": "거래처세부", "grade": "등급", "spec": "규격", "box": "박스", "note": "비고"})
    return R, pd.DataFrame(items).round(1), pd.DataFrame(neg), unp


# ───────────────────────── 이상 감시 ─────────────────────────
def sensor_fault_minutes(env):
    """방·날짜별 '진짜' 센서 이상 분 (v1.22.0). 정상 운전에서 늘 나오는 값은 세지 않는다.
    - 습도 101~RULES['hum_sat_max'](115)% : 센서 포화(물기). 그날 습도 원값이 0 이상·기준 이하이면 습도 분을 빼고
    - 냉각실 온도 45~RULES['cool_room_temp_max'](80)℃ : 뜨거운 솥 입고. 냉각실이고 그날 온도 원값이 −5~80℃ 안이면 온도 분을 뺀다
    항목별 분(temp_bad_n 등)이 없는 옛 캐시는 sensor_bad_n 을 그대로 쓴다.
    반환: (분 Series, 항목별 포함 여부 DataFrame[temp, hum, co2]) — 둘 다 env 와 같은 index"""
    R = C.RULES
    if env is None or env.empty:
        return pd.Series(dtype=float), pd.DataFrame(columns=["temp", "hum", "co2"])
    if not {"temp_bad_n", "hum_bad_n", "co2_bad_n"} <= set(env.columns):
        s = env["sensor_bad_n"].fillna(0)
        return s, pd.DataFrame({"temp": s > 0, "hum": s > 0, "co2": s > 0}, index=env.index)
    na = pd.Series(np.nan, index=env.index)
    hmax, hmin = env.get("hum_raw_max", na), env.get("hum_raw_min", na)
    tmax, tmin = env.get("temp_raw_max", na), env.get("temp_raw_min", na)
    hum_sat = (hmax.fillna(np.inf) <= R.get("hum_sat_max", 101)) & (hmin.fillna(0) >= 0)
    cool_hot = (env["room"].astype(str).str.startswith("냉각") & (tmax.fillna(np.inf) <= R.get("cool_room_temp_max", 45))
                & (tmin.fillna(0) >= -5))
    t_n, h_n, c_n = env["temp_bad_n"].fillna(0), env["hum_bad_n"].fillna(0), env["co2_bad_n"].fillna(0)
    use = pd.DataFrame({"temp": (t_n > 0) & ~cool_hot, "hum": (h_n > 0) & ~hum_sat, "co2": c_n > 0}, index=env.index)
    s = t_n.where(use["temp"], 0) + h_n.where(use["hum"], 0) + c_n.where(use["co2"], 0)
    return s.clip(upper=env["sensor_bad_n"].fillna(s)), use


def days_since_reset(env):
    """방·날짜별로 마지막 입실·입상 뒤 지난 일수(그날 = 0). 입실·입상으로 보는 날:
    재배일자가 리셋(reset_n>0)되거나 줄어든 날, 재배일자가 하루에 3 이상 뛴 날(생육실처럼 다른 방에서 병이 넘어옴),
    전날 거의 안 돌다가(가동 50% 미만·기록 없음) 다시 돌기 시작한 날(비워 뒀던 방에 병을 넣음).
    필요한 열이 없으면 999. 반환 Series(index = env.index)"""
    if env is None or env.empty or not {"reset_n", "day_first", "day_last", "run"} <= set(env.columns):
        return pd.Series(999.0, index=getattr(env, "index", None))
    e = env[["room", "date", "reset_n", "day_first", "day_last", "run"]].sort_values(["room", "date"])
    prev_run = e.groupby("room")["run"].shift()
    gap1 = (e["date"] - e.groupby("room")["date"].shift()).dt.days == 1
    prev_run = prev_run.where(gap1, 0.0)                     # 전날 기록이 없으면 쉰 것으로 본다
    turn = ((e["reset_n"].fillna(0) > 0) | (e["day_first"] > e["day_last"]) | ((e["day_last"] - e["day_first"]) >= 3)
            | ((e["run"].fillna(0) >= 0.5) & (prev_run.fillna(0) < 0.5)))
    last = e["date"].where(turn).groupby(e["room"]).ffill()
    return (e["date"] - last).dt.days.astype(float).fillna(999.0).reindex(env.index)


def _episodes(df, gap=2):
    """같은 방에서 날짜가 이어지는(gap일 이내) 행에 같은 묶음 번호(_ep)를 붙인다."""
    df = df.sort_values(["room", "date"])
    new = (df["room"] != df["room"].shift()) | ((df["date"] - df["date"].shift()).dt.days > gap)
    return df.assign(_ep=new.cumsum())


def _span(g):
    """묶음이 여러 날이면 ' — 3일 연속 (09/28~09/30)' 같은 꼬리말"""
    if len(g) <= 1:
        return ""
    d0, d1 = pd.Timestamp(g["date"].min()), pd.Timestamp(g["date"].max())
    whole = (d1 - d0).days + 1 == len(g)
    return f" — {len(g)}일{' 연속' if whole else ''} ({d0:%m/%d}~{d1:%m/%d})"


def anomalies(env=None, days=14, ref_date=None):
    """이상 감시 목록. 기준은 config.RULES (v1.22.0 에서 실측 분포로 다시 정함).
    같은 방에서 며칠 이어진 센서 이상·장비 에러·추종오차는 한 줄(N일 연속)로 묶는다. 날짜 = 마지막 날."""
    env = env_daily() if env is None else env
    if env.empty:
        return pd.DataFrame()
    env = env[~env.room.isin(C.MONITOR_EXCLUDE)]        # 창고 등 재배와 무관한 방 제외
    R = C.RULES
    today = pd.Timestamp(ref_date) if ref_date else env.date.max()
    lo = today - pd.Timedelta(days=days)
    env = env[env.date <= today]
    w = env[(env.date > lo)].copy()
    out = []

    farm_of = env.drop_duplicates("room").set_index("room").farm.map(C.FARMS).to_dict()

    def add(sev, cat, room, date, msg, val=None):
        out.append({"심각도": sev, "분류": cat, "재배사": farm_of.get(room, "배양센터" if room == "배양실" else ""), "방": room,
                    "날짜": pd.Timestamp(date).date(), "내용": msg, "값": val})

    ERR_KO = {"co2_error": "CO2", "temp_error": "온도", "humi_error": "습도", "sensor_error": "센서", "device_error": "장비",
              "sensor1_error": "센서1", "sensor2_error": "센서2", "sensor3_error": "센서3", "sensor4_error": "센서4"}

    def sensor_msg(r, use=None):
        """항목별·센서별 불가능한 값 설명. 예: 'CO2 센서2·3 (최대 65,535ppm) 240분'. use: 포함할 항목(포화·뜨거운 솥은 뺌)"""
        parts = []
        for f, ko, unit, fmt in (("temp", "온도", "°C", "{:.1f}"), ("hum", "습도", "%", "{:.0f}"), ("co2", "CO2", "ppm", "{:,.0f}")):
            n = r.get(f + "_bad_n", 0)
            if not n or (use is not None and not bool(use.get(f, True))):
                continue
            raw = "humidity" if f == "hum" else f
            sens = [str(i) for i in range(1, 5) if r.get(f"s{i}_{raw}_bad_n", 0) > 0]
            lo_, hi_ = r.get(f + "_raw_min"), r.get(f + "_raw_max")
            rng = {"temp": (-5, 45), "hum": (0, 101), "co2": (0, 20000)}[f]
            ext = ("최대 " + fmt.format(hi_) + unit) if pd.notna(hi_) and hi_ > rng[1] else ("최소 " + fmt.format(lo_) + unit) if pd.notna(lo_) and lo_ < rng[0] else ""
            parts.append(f"{ko}" + (f" 센서{'·'.join(sens)}" if sens else "") + f" {int(n)}분" + (f" ({ext})" if ext else ""))
        if parts:
            return " / ".join(parts)
        return f"{int(r.sensor_bad_n)}분 — 항목·센서 상세는 환경 캐시를 다시 만든 뒤(build_cache.bat) 표시됩니다"

    def err_msg(r):
        items = [(ERR_KO[e], int(r.get(e + "_n", 0))) for e in C.ERR_FIELDS if r.get(e + "_n", 0) > 0]
        items.sort(key=lambda t: -t[1])
        return ", ".join(f"{k} {n}분" for k, n in items[:4]) if items else f"{int(r.err_n)}분"

    # 1. 로그 중단
    last = env.groupby("room").date.max()
    active = env[env.date > today - pd.Timedelta(days=60)].groupby("room").run.mean()
    for room, d in last.items():
        if (today - d).days > R["log_stale_days"] and active.get(room, 0) > 0.2:
            add("높음", "로그 중단", room, d, f"마지막 기록 {d.date()} 이후 {(today - d).days}일간 데이터 없음")
    # 2. 센서 이상 (습도 포화·냉각실 뜨거운 솥은 뺀 '불가능한 값') · 장비 에러 — 이어진 날은 한 건
    sbad, suse = sensor_fault_minutes(w)
    x = w.assign(_bad=sbad)[sbad > R.get("sensor_bad_min", 30)]       # (빈 표에 assign 하면 행이 생기므로 먼저 붙이고 거른다)
    if len(x):
        for _, g in _episodes(x).groupby("_ep"):
            r = g.sort_values("date").iloc[-1]
            add("높음", "센서 이상", r.room, r.date, "측정 범위 밖 값: " + sensor_msg(r, suse.loc[r.name]) + _span(g), int(g["_bad"].max()))
    x = w[w.err_n > R.get("err_min", 60)]
    if len(x):
        for _, g in _episodes(x).groupby("_ep"):
            r = g.sort_values("date").iloc[-1]
            add("중간", "장비 에러", r.room, r.date, "에러 상태: " + err_msg(r) + _span(g), int(g.err_n.max()))
    # 3. 온도 추종오차 — 운전 80% 이상·그날 설정을 안 바꾼 날. 입실·입상 당일과 다음날(병이 들어와 식는 중),
    #    냉각실(매일 뜨거운 솥)·배양실(6b에서 따로)·발이실 1·2번(다른 용도)은 보지 않는다
    since = days_since_reset(env).reindex(w.index).fillna(999)
    stable = (w.sp_temp_last.astype(float) - w.sp_temp).abs() < 0.5          # 그날 설정을 안 바꾼 날만
    x = w[(w.run > 0.8) & stable & (w.track_err > R["track_err_c"]) & (since > R.get("track_skip_days", 1))
          & (~w.room.isin(["발이실01", "발이실02"])) & (~w.room.astype(str).str.startswith(("냉각", "배양실")))]
    if len(x):
        for _, g in _episodes(x).groupby("_ep"):
            r = g.sort_values("date").iloc[-1]
            peak = f", 최대 {g.track_err.max():.1f}°C" if len(g) > 1 else ""
            add("중간", "온도 추종오차", r.room, r.date, f"실측 {r.temp:.1f}°C vs 설정 {r.sp_temp:.1f}°C (오차 {r.track_err:.1f}°C)" + _span(g) + peak,
                round(float(g.track_err.max()), 2))
    # 4. 새송이 설정값 미사용 (습도 설정 <50 또는 CO2 설정 >2500 인데 운전중)
    x = w[(w.crop == "새송이") & (w.run > 0.5) & ((w.sp_hum < 50) | (w.sp_co2 > 2500)) & (~w.room.isin(C.EXCLUDE_ROOMS))]
    for room, g in x.groupby("room"):
        add("중간", "설정값 미사용", room, g.date.max(), f"습도 설정 {g.sp_hum.mean():.0f}% / CO2 설정 {g.sp_co2.mean():.0f}ppm — 실측과 무관한 값 ({len(g)}일)")
    # 5. 팽이 발이실 억제 스케줄 이탈
    p = w[(w.farm == "1433") & (w.room.str.startswith("발이실")) & (w.run > 0.5)]
    for _, r in p.iterrows():
        dl = r.day_last
        if pd.notna(dl) and int(dl) in C.PAENGI_SUPPRESS_STD:
            std = C.PAENGI_SUPPRESS_STD[int(dl)]
            if r.sp_temp - std > R["suppress_tol_c"]:
                add("중간", "억제 스케줄 늦음", r.room, r.date, f"{int(dl)}일차 설정 {r.sp_temp:.1f}°C (1~6월 방식 {std}°C) — 7월 이후 방식", round(r.sp_temp - std, 1))
    # 6. 배양 불량률 (최근 4주, 주 단위)
    hm = R.get("scrape_high_mult", 3)
    for name, key, lim in (("균긁기불량(팽이)", "cult_scrape_paengi", R["scrape_bad_paengi"]), ("균긁기불량(새송이)", "cult_scrape_ss", R["scrape_bad_ss"])):
        d = etl_logs.load(key)
        if d.empty:
            continue
        d = d[(d["긁기일"] > today - pd.Timedelta(days=28)) & (d["긁기일"] <= today)]
        if d.empty:
            continue
        d["wk"] = d["긁기일"].dt.to_period("W").dt.start_time
        g = d.groupby("wk").agg(out=("배양출고", "sum"), bad=("불량", "sum"))
        for wk, r in g.iterrows():
            rate = 100 * r.bad / r.out if r.out else 0
            if rate > lim:
                add("높음" if rate >= hm * lim else "중간", name, "배양실", wk, f"{wk.date()} 주 불량률 {rate:.2f}% ({int(r.bad):,}/{int(r.out):,}병, 기준 {lim}%)", round(rate, 2))
    d = etl_logs.load("cult_kwonji")
    if not d.empty:
        d = d[(d["권지일"] > today - pd.Timedelta(days=28)) & (d["권지일"] <= today)]
        if not d.empty:
            d["wk"] = d["권지일"].dt.to_period("W").dt.start_time
            g = d.groupby("wk").agg(out=("입상량", "sum"), bad=("불량", "sum"))
            for wk, r in g.iterrows():
                rate = 100 * r.bad / r.out if r.out else 0
                if rate > R["kwonji_bad"]:
                    add("중간", "권지불량(팽이)", "배양실", wk, f"{wk.date()} 주 불량률 {rate:.2f}% (기준 {R['kwonji_bad']}%)", round(rate, 2))
    # 6b. 배양실 환경: 습도 기준 초과, 설정과 온도 차이 (운전중) — 최근 며칠 이상일 때
    nd, h_lim, t_lim = int(R.get("cult_days", 3)), R.get("cult_hum", 85), R.get("cult_temp_c", 1.0)
    c = w[w.room.isin(["배양실1", "배양실2"])]
    for room, g in c.groupby("room"):
        hi = g[g.hum > h_lim]
        if len(hi) >= nd:
            add("중간", "배양실 습도", room, hi.date.max(), f"습도 {h_lim}% 초과 {len(hi)}일 (평균 {hi.hum.mean():.0f}%) — 여름 오염 급등기와 같은 조건", round(hi.hum.mean(), 1))
        te = g[g.track_err > t_lim]
        if len(te) >= nd:
            add("낮음", "배양실 온도", room, te.date.max(), f"설정과 {te.track_err.mean():.1f}°C 차이 {len(te)}일 (설정 {te.sp_temp.mean():.0f}°C, 실측 {te.temp.mean():.1f}°C)", round(te.track_err.mean(), 2))
    # 7. 새송이 방당 병수 — 같은 동(A~D) 최근 120일 입상의 중앙값 ±ss_bottles_tol (동마다 방 크기·운영이 다름).
    #    비교할 입상이 8번 미만이면(오래 쉬다 다시 시작한 동 등) 판정하지 않는다
    B = ss_batches()
    if not B.empty and "bottles" in B:
        tol = R.get("ss_bottles_tol", 0.2)
        ref_all = B.dropna(subset=["bottles", "입상일"])
        ref_all = ref_all[~ref_all.room.isin(C.EXCLUDE_ROOMS)]
        b = B[(B["입상일"] > lo) & (B["입상일"] <= today)]
        for _, r in b.iterrows():
            if pd.isna(r.bottles):
                continue
            blk = str(r.room)[:1]
            ref = ref_all[(ref_all.room.astype(str).str[:1] == blk) & (ref_all["입상일"] < r["입상일"]) & (ref_all["입상일"] >= r["입상일"] - pd.Timedelta(days=120))]
            if len(ref) >= 8:
                m = float(ref.bottles.median())
                if abs(r.bottles - m) > tol * m:
                    add("낮음", "입상 병수", r.room, r["입상일"], f"방당 {r.bottles:,.0f}병 ({blk}동 최근 120일 보통 {m:,.0f}병, ±{tol:.0%} 밖)", r.bottles)
    # 8. 냉각 느림·냉각실 과냉·살균 지연 (냉각실 1분 곡선 + 솥 단위 기록)
    try:
        PA = process_alerts(lo, today)
        for _, r in (PA.iterrows() if len(PA) else []):
            add(r["심각도"], r["분류"], r["방"], r["날짜"], r["내용"], r["값"])
    except Exception:
        pass
    A = pd.DataFrame(out)
    if A.empty:
        return A
    order = {"높음": 0, "중간": 1, "낮음": 2}
    return A.sort_values(["심각도", "날짜"], key=lambda s: s.map(order) if s.name == "심각도" else s, ascending=[True, False]).reset_index(drop=True)


# ───────────────────────── 수리 기록 (v1.23.0) ─────────────────────────
REPAIR_COLS = ["date", "구역", "장소", "방", "분류", "내용", "사양", "업체", "비고", "시트", "행"]
REPAIR_STAGES = ["입병", "살균", "냉각", "접종", "배양", "균긁기", "발이실", "권지", "생육실"]
# 배치 이력 카드(STAGE_ORDER)에 붙일 때의 단계 이름
REPAIR_STAGE_CARD = {"입병": "배지", "살균": "살균", "냉각": "냉각", "접종": "접종", "배양": "배양", "균긁기": "배양",
                     "발이실": "발이", "권지": "생육", "생육실": "생육"}


def _txt(s):
    return s.astype(object).where(s.notna(), "").astype(str).replace({"None": "", "nan": "", "NaT": ""}).str.strip()


def repairs():
    """수리 기록 한 줄 = 수리 한 건 (기계수리.xlsx 구역 시트·일백 시트 + 가습기 수리내역). 날짜순.
    장소 = 방·장비(쉼표로 여러 개), 방 = 환경 기록의 방 이름(쉼표로 여러 개, 못 찾으면 빈칸)."""
    R = etl_logs.load("repairs")
    if R.empty:
        return pd.DataFrame(columns=REPAIR_COLS)
    R = R.copy()
    for c in REPAIR_COLS[1:]:
        if c not in R:
            R[c] = "" if c != "행" else 0
        if c != "행":
            R[c] = _txt(R[c])
    R["date"] = pd.to_datetime(R["date"], errors="coerce").dt.normalize()
    return R.dropna(subset=["date"]).sort_values(["date", "구역", "장소"]).reset_index(drop=True)[REPAIR_COLS]


def repair_costs():
    """수리 완료 보고서(수리비). 보고서 한 장 = 한 줄. 일백 냉동 공사는 한 달 치를 묶어 한 장으로 낸다."""
    K = etl_logs.load("repair_costs")
    if K.empty:
        return K
    K = K.drop(columns=["_src"], errors="ignore").copy()
    for c in ("date", "완료일"):
        if c in K:
            K[c] = pd.to_datetime(K[c], errors="coerce")
    for c in ("구역", "수리명", "업체", "발주처", "인원", "기간", "내용", "시트"):
        if c in K:
            K[c] = _txt(K[c])
    K["수리비"] = pd.to_numeric(K.get("수리비"), errors="coerce")
    return K.sort_values("date").reset_index(drop=True)


def _split(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


def repairs_by(R, col):
    """'장소' 또는 '방' 하나당 한 줄로 펼친다(한 수리가 여러 방에 걸치면 방마다 한 줄). 펼친 값은 '_key' 열."""
    if R is None or R.empty:
        return pd.DataFrame(columns=list(REPAIR_COLS) + ["_key"])
    x = R.assign(_key=R[col].map(_split)).explode("_key")
    return x[x["_key"].notna() & (x["_key"] != "")]


def _short(s, n=26):
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…"


def batch_repairs(b, R=None):
    """배치가 거친 방·장비의 수리 기록. b: 배치 이력 한 줄(dict 도 됨 — 살균일·접종일·입상일·권지일·수확시작·수확종료·
    발이실·생육실·살균솥·살균기·입병일). 방(냉각·배양·발이실·생육실)은 머문 기간과 그 앞뒤(앞 1~3일, 뒤 3~7일 — 고친 날 전부터
    문제가 있었을 수 있음), 공정 장비(입병·살균·접종·균긁기·권지)는 그날과 다음날만 본다.
    반환 DataFrame[단계, 날짜, 시점, 장소, 분류, 내용, 업체, 비고]"""
    import re
    cols = ["단계", "날짜", "시점", "장소", "분류", "내용", "업체", "비고"]
    R = repairs() if R is None else R
    if R is None or R.empty or b is None:
        return pd.DataFrame(columns=cols)
    get = (lambda k: b.get(k)) if hasattr(b, "get") else (lambda k: None)

    def T(k):
        v = get(k)
        try:
            return pd.Timestamp(v).normalize() if v is not None and pd.notna(v) else pd.NaT
        except (TypeError, ValueError):
            return pd.NaT

    def num(k):
        v = pd.to_numeric(get(k), errors="coerce")
        return int(v) if pd.notna(v) else None
    sal, jj, ip, kw = T("살균일"), T("접종일"), T("입상일"), T("권지일")
    if pd.isna(sal) and pd.notna(jj):
        sal = jj - pd.Timedelta(days=1)
    ipb = T("입병일") if pd.notna(T("입병일")) else sal
    h_end = T("수확종료") if pd.notna(T("수확종료")) else T("수확시작")
    units = []
    for v in (get("살균솥"), get("살균기")):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        for n in re.findall(r"\d", str(v)):
            if 1 <= int(n) <= 4 and f"{n}호기" not in units:
                units.append(f"{n}호기")
    bal, saeng = num("발이실"), num("생육실")
    places, rooms = R["장소"].map(_split), R["방"].map(_split)
    is_ret = places.map(lambda ps: any(p.startswith("살균솥") for p in ps))
    is_boil = places.map(lambda ps: any(p.startswith("보일러") for p in ps))
    ret_txt = R["내용"].str.contains("가마|살균", regex=True)
    in_room = lambda want: rooms.map(lambda rs: bool(set(want) & set(rs)))
    specs = [   # 단계, 대상 행, 시작, 끝, 앞 며칠, 뒤 며칠, 방 단계인가
        ("입병", (R["구역"] == "입병실") & ~is_ret & ~is_boil & ~ret_txt, ipb, ipb, 0, 1, False),
        ("살균", (is_ret & rooms.map(lambda rs: not units or bool(set(rs) & set(units)))) | is_boil | ((R["구역"] == "입병실") & ret_txt & ~is_ret),
         sal, sal, 0, 1, False),
        ("냉각", in_room(["냉각1실", "냉각실2"]), sal, jj, 1, 3, True),
        ("접종", (R["구역"] == "접종·냉각실") & (rooms.map(len) == 0) & ~is_ret & ~places.map(lambda ps: "액냉각실" in ps), jj, jj, 0, 1, False),
        ("배양", in_room(["배양실1", "배양실2"]), jj, ip, 3, 7, True),
        ("균긁기", R["구역"] == "균긁기실", ip, ip, 0, 1, False),
        ("발이실", in_room([f"발이실{bal:02d}"]) if bal else pd.Series(False, index=R.index), ip, kw, 3, 7, True),
        ("권지", R["구역"] == "권지실", kw, kw, 0, 1, False),
        ("생육실", in_room([f"생육실{saeng:02d}"]) if saeng else pd.Series(False, index=R.index), kw, h_end, 3, 7, True),
    ]
    out = []
    for stage, mask, d0, d1, pre, post, room_stage in specs:
        if pd.isna(d0):
            continue
        d1 = d0 if pd.isna(d1) else d1
        x = R[mask & (R.date >= d0 - pd.Timedelta(days=pre)) & (R.date <= d1 + pd.Timedelta(days=post))]
        for _, r in x.iterrows():
            d = r["date"]
            if d0 <= d <= d1:
                when = "머무는 중" if room_stage else "그날"
            elif d < d0:
                when = f"들어오기 {(d0 - d).days}일 전" if room_stage else f"{(d0 - d).days}일 전"
            else:
                when = f"나간 뒤 {(d - d1).days}일" if room_stage else "다음날" if (d - d1).days == 1 else f"{(d - d1).days}일 뒤"
            out.append({"단계": stage, "날짜": d, "시점": when, "장소": r["장소"], "분류": r["분류"], "내용": r["내용"], "업체": r["업체"], "비고": r["비고"]})
    D = pd.DataFrame(out, columns=cols)
    if D.empty:
        return D
    D["_o"] = D["단계"].map({s: i for i, s in enumerate(REPAIR_STAGES)})
    return D.sort_values(["_o", "날짜"]).drop(columns="_o").reset_index(drop=True)


def anomaly_repairs(an, R=None, before=30, after=30):
    """이상 감시 표의 줄마다 같은 방의 수리 기록(그 날짜 앞 before일 ~ 뒤 after일) 요약. 예: '08/27 발이실6번 1번쿨러 용접 외 2건'.
    '배양실'(불량률 줄)은 배양실1·2 를 함께 본다."""
    if an is None or an.empty:
        return pd.Series(dtype=str)
    R = repairs() if R is None else R
    if R.empty:
        return pd.Series("", index=an.index)
    L = repairs_by(R, "방")
    out = []
    for _, r in an.iterrows():
        d = pd.Timestamp(r["날짜"])
        want = {"배양실1", "배양실2"} if r["방"] == "배양실" else {r["방"]}
        x = L[L["_key"].isin(want) & (L.date >= d - pd.Timedelta(days=before)) & (L.date <= d + pd.Timedelta(days=after))]
        x = x.drop_duplicates(["date", "내용"]).sort_values("date", ascending=False)
        if x.empty:
            out.append(""); continue
        top = x.iloc[0]
        out.append(f"{top.date:%m/%d} {_short(top['내용'])}" + (f" 외 {len(x) - 1}건" if len(x) > 1 else ""))
    return pd.Series(out, index=an.index)


def room_sort_key(room):
    """방 이름 정렬: 냉각 → 배양 → 발이실 → 생육실 → 새송이 A~D → 살균솥 → 그 밖"""
    import re
    s = str(room)
    m = re.match(r"(\D*)(\d+)", s)
    base, n = (m.group(1), int(m.group(2))) if m else (s, 0)
    rank = [("냉각", 0), ("배양", 1), ("발이실", 2), ("생육실", 3), ("A", 4), ("B", 5), ("C", 6), ("D", 7)]
    r = next((i for k, i in rank if s.startswith(k)), 9 if s.endswith("호기") else 8)
    return (r, base, n, s)


# ───────────────────────── 설비 가동률 (재배 1주기 기준 방끼리 비교, v1.24.1) ─────────────────────────
# 같은 동(새송이 A~D동)·팽이 발이실·팽이 생육실은 방마다 냉방기·난방기·가습기·환기(시로코팬) 사양이 같다.
# 재배 패턴(주기 안의 날짜)마다 쓰는 양이 다르므로, 같은 묶음에서 '주기 n일째'가 같고 시작일이 가까운(±21일, 날씨가 비슷한)
# 다른 방 주기들의 중앙값을 기대값으로 두고, 이 방이 한 주기 동안 그보다 얼마나 더 돌았나(%p)를 본다.
EQUIP = (("cool", "냉방"), ("heat", "난방"), ("humid", "가습"), ("co2r", "환기"))
EQUIP_NAMES = dict(EQUIP)
EQUIP_GROUPS = ("새송이 A동", "새송이 B동", "새송이 C동", "새송이 D동", "팽이 발이실", "팽이 생육실")
EQUIP_NEAR_DAYS = 21          # 기대값을 만들 때 시작일이 이만큼 안쪽인 다른 방 주기만 (계절·날씨를 맞추려고)
EQUIP_MIN_PEERS = 3           # 그날의 기대값을 만들 다른 주기 수
EQUIP_MIN_DAYS = 5            # 주기 하나를 평가할 최소 일수
EQUIP_HIGH = 10               # 한 주기 평균이 기대보다 10%p 넘게 높으면 '유독 높음'


def equip_cycle_days(PE=None, SE=None, K=None):
    """재배 주기 × 일차 가동률(%) 표. 새송이 = 입상~폐상(배치), 팽이 발이실 = 입상~권지 전날, 팽이 생육실 = 권지~수확.
    반환 열: 묶음, room, cyc(주기 id), 시작, date, cday(주기 안 n일째, 첫날=1), cool·heat·humid·co2r(%), 기대(각 _exp), 차이(각 _ex)"""
    parts = []
    if SE is not None and not SE.empty:
        x = SE[SE["phase"] != "배양"].copy()
        x = x[x["room"].astype(str).str.match(r"^[A-D]\d+$")]
        x["묶음"] = "새송이 " + x["room"].str[0] + "동"
        x["cyc"] = x["bid"]
        x["시작"] = pd.to_datetime(x["date"]) - pd.to_timedelta(x["cday"], unit="D")      # 새송이 cday 는 입상일=0
        parts.append(x)
    if PE is not None and not PE.empty:
        x = PE[PE["phase"].isin(["발이실", "생육실"])].copy()
        x["묶음"] = "팽이 " + x["phase"]
        x["cyc"] = x["bid"] + "|" + x["phase"]
        x["시작"] = pd.to_datetime(x["date"]) - pd.to_timedelta(x["cday"] - 1, unit="D")   # 팽이 cday 는 입상일=1
        if K is not None and not K.empty and "권지일" in K:                               # 생육실 주기는 권지일부터
            kw = K.drop_duplicates("bid").set_index("bid")["권지일"]
            g_ = x["phase"] == "생육실"
            x.loc[g_, "시작"] = pd.to_datetime(x.loc[g_, "bid"].map(kw)).fillna(x.loc[g_, "시작"])
        parts.append(x)
    if not parts:
        return pd.DataFrame()
    cols = ["묶음", "room", "cyc", "시작", "date", "run"] + [c for c, _ in EQUIP]
    D = pd.concat([p[[c for c in cols if c in p]] for p in parts], ignore_index=True)
    D["date"] = pd.to_datetime(D["date"])
    D["시작"] = pd.to_datetime(D["시작"]).dt.normalize()
    D["cday"] = (D["date"] - D["시작"]).dt.days + 1
    low = D["run"].fillna(0) < 0.1 if "run" in D else pd.Series(False, index=D.index)       # 거의 안 돌린 날(비우는 날 등)은 뺀다
    for c, _ in EQUIP:
        D[c] = (pd.to_numeric(D[c], errors="coerce") * 100).where(~low)
        D[c + "_exp"] = np.nan
    for (_, _), g in D.groupby(["묶음", "cday"]):
        if len(g) < EQUIP_MIN_PEERS + 1:
            continue
        st_ = g["시작"].to_numpy(dtype="datetime64[D]").astype(np.int64)
        rm = g["room"].to_numpy()
        near = (np.abs(st_[:, None] - st_[None, :]) <= EQUIP_NEAR_DAYS) & (rm[:, None] != rm[None, :])
        for c, _ in EQUIP:
            v = g[c].to_numpy(dtype=float)
            M = np.where(near & ~np.isnan(v)[None, :], v[None, :], np.nan)
            cnt = (~np.isnan(M)).sum(axis=1)
            with np.errstate(all="ignore"):
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    med = np.nanmedian(M, axis=1)
            D.loc[g.index, c + "_exp"] = np.where(cnt >= EQUIP_MIN_PEERS, med, np.nan)
    for c, _ in EQUIP:
        D[c + "_ex"] = D[c] - D[c + "_exp"]
    return D


def equip_cycles(D):
    """주기 하나당 한 줄: 묶음, 방, 주기, 시작, 끝, 일수, 설비별 평균 가동률·기대값·차이(%p). 차이는 기대값이 있는 날이 5일 이상일 때만"""
    if D is None or D.empty:
        return pd.DataFrame()
    rows = []
    for cyc, g in D.groupby("cyc"):
        r = {"묶음": g["묶음"].iloc[0], "방": g["room"].iloc[0], "주기": cyc.split("|")[0], "cyc": cyc,
             "시작": g["시작"].min(), "끝": g["date"].max(), "일수": int(g["date"].nunique())}
        for c, n in EQUIP:
            ok = g[c + "_ex"].notna()
            r[n] = g[c].mean()
            r[n + " 기대"] = g.loc[ok, c + "_exp"].mean() if ok.sum() >= EQUIP_MIN_DAYS else np.nan
            r[n + " 차이"] = g.loc[ok, c + "_ex"].mean() if ok.sum() >= EQUIP_MIN_DAYS else np.nan
        rows.append(r)
    return pd.DataFrame(rows).sort_values(["묶음", "방", "시작"]).reset_index(drop=True)


def equip_rooms(C_, group, n_cycles=1, end=None):
    """묶음 하나의 방별 비교: 방마다 최근 n_cycles 주기(시작일 기준, 기대값이 있는 주기만)의 설비별 차이 평균(%p)·평균 가동률,
    '유독 높은 항목'(차이 ≥ EQUIP_HIGH 이고 그 주기들 대부분에서 높음)"""
    X = C_[C_["묶음"] == group]
    if end is not None:
        X = X[X["시작"] <= pd.Timestamp(end)]
    ex_cols = [n + " 차이" for _, n in EQUIP]
    X = X[X[ex_cols].notna().any(axis=1)]
    if X.empty:
        return pd.DataFrame()
    rows = []
    for room, g in X.groupby("방"):
        g = g.sort_values("시작").tail(n_cycles)
        r = {"방": room, "주기": ", ".join(g["주기"]), "기간": f"{g['시작'].min():%m/%d}~{g['끝'].max():%m/%d}", "주기 수": len(g)}
        high = []
        for _, n in EQUIP:
            r[n] = g[n].mean()
            r[n + " 차이"] = g[n + " 차이"].mean()
            k = int((g[n + " 차이"] >= EQUIP_HIGH / 2).sum())
            if pd.notna(r[n + " 차이"]) and r[n + " 차이"] >= EQUIP_HIGH and k >= max(1, (len(g) + 1) // 2):
                high.append((r[n + " 차이"], n))
        r["유독 높은 항목"] = " · ".join(f"{n} +{v:.0f}%p" for v, n in sorted(high, reverse=True)) or "—"
        r["_n_high"] = len(high)
        r["_sum"] = np.nansum([max(0, r[n + " 차이"]) if pd.notna(r[n + " 차이"]) else 0 for _, n in EQUIP])
        rows.append(r)
    S = pd.DataFrame(rows)
    S["_o"] = S["방"].map(room_sort_key)
    return S.sort_values("_o").drop(columns="_o").reset_index(drop=True)


# ───────────────────────── 종균·액탱크 ─────────────────────────
def tank_batches():
    """액탱크 배치 하나당 한 줄: 종균 관리 대장(계통·세대·pH·접종일) + 배양실 작업 일지(사용 시 pH·배지·비고)
    + 살균솥 액탱크 사이클(솥·2차유지·F0) + 균긁기 결과(불량률) + 팽이 사슬(병당중량·QC)."""
    import re
    from . import etl_retort
    S = etl_logs.load("spawn_tanks")
    if S.empty:
        return pd.DataFrame()
    S = S.dropna(subset=["일시"]).copy()
    S["탱크NO"] = S["탱크NO"].astype("Int64")
    # 작업 일지: 접종에 쓴 날(코드 일치) → 사용 시 종균pH·접종배지·살균후pH / 만든 날(일시) → 비고
    D = etl_logs.load("tank_daily")
    if not D.empty:
        use = D.dropna(subset=["접종탱크코드"]).drop_duplicates("접종탱크코드", keep="last")
        S = S.merge(use[["접종탱크코드", "date", "종균pH", "접종배지", "살균후pH"]].rename(columns={"접종탱크코드": "코드", "date": "사용일"}), on="코드", how="left")
        ok = S["사용일"].notna() & (S["사용일"] >= S["일시"]) & (S["사용일"] <= S["일시"] + pd.Timedelta(days=14))
        S["접종일"] = S["접종일"].fillna(S["사용일"].where(ok))
        S["액배양일"] = (S["접종일"] - S["일시"]).dt.days
        S.loc[(S["액배양일"] < 0) | (S["액배양일"] > 30), "액배양일"] = np.nan
        mk = D[["date", "비고"]].dropna().rename(columns={"date": "일시", "비고": "생성일비고"}).drop_duplicates("일시")
        S = S.merge(mk, on="일시", how="left")
    # 살균솥: 탱크 접종(일시) 전에 끝난 가장 최근 액탱크 사이클 = 이 탱크 배지를 살균한 회차.
    # 액탱크는 오후에 살균·냉각하고 다음 날(주말이면 사흘 뒤) 플라스크 균을 넣으므로, 같은 날 사이클이 아니라 그 전 사이클이다.
    # 살균후대기일 = 살균 끝난 날부터 탱크 접종까지 달력 일수(평소 1~2일, 주말 3일). 길어지면 냉각된 배지에 잡균이 먼저 자랄 수 있다.
    try:
        CY = etl_retort.load_cycles()
    except Exception:
        CY = pd.DataFrame()
    if not CY.empty and "구분" in CY:
        C = CY[CY["구분"] == "액탱크"].dropna(subset=["end"]).copy()
        C["살균일"] = C["end"].dt.normalize()
        agg = C.groupby("살균일").agg(살균솥=("unit", lambda x: ",".join(sorted(set(x)))), 살균시작=("start", "min"), 살균종료=("end", "max"),
                                    살균2차유지분=("2차유지분", "mean"), 살균최고온도=("최고온도", "mean"), 살균F0=("F0", "mean"),
                                    살균중단=("중단횟수", "sum")).reset_index().sort_values("살균일")
        S = S.sort_values("일시")
        S = pd.merge_asof(S, agg, left_on="일시", right_on="살균일", direction="backward", allow_exact_matches=False)
        S["살균후대기일"] = (S["일시"].dt.normalize() - S["살균일"]).dt.days
        far = S["살균후대기일"] > getattr(C, "TANK_LINK_MAX_DAYS", 10)
        for c in ["살균일", "살균솥", "살균시작", "살균종료", "살균2차유지분", "살균최고온도", "살균F0", "살균중단", "살균후대기일"]:
            S.loc[far, c] = np.nan if c not in ("살균일", "살균시작", "살균종료") else pd.NaT
        # 같은 살균분(같은 사이클 배지)으로 만든 탱크 수 — 대기일만 다른 형제 탱크끼리 비교할 수 있다
        S["같은살균탱크"] = S.groupby("살균일")["코드"].transform("size")
    # 균긁기 결과: 팽이는 (접종일, 탱크NO), 새송이는 접종일
    P = etl_logs.load("cult_scrape_paengi")
    if not P.empty and "탱크" in P:
        rows = []
        for _, r in P.dropna(subset=["접종일"]).iterrows():
            tanks = [int(t) for t in re.split(r"[,/. ]+", str(r["탱크"])) if t.strip().isdigit()]
            for t in tanks:
                rows.append({"접종일": r["접종일"], "탱크NO": t, "배양출고": (r["배양출고"] or 0) / len(tanks), "긁기불량": (r["불량"] or 0) / len(tanks)})
        if rows:
            PT = pd.DataFrame(rows).groupby(["접종일", "탱크NO"]).sum().reset_index(); PT["탱크NO"] = PT["탱크NO"].astype("Int64")
            S = S.merge(PT, on=["접종일", "탱크NO"], how="left")
    Q = etl_logs.load("cult_scrape_ss")
    if not Q.empty:
        QT = Q.dropna(subset=["접종일"]).groupby("접종일").agg(배양출고_ss=("배양출고", "sum"), 긁기불량_ss=("불량", "sum")).reset_index()
        S = S.merge(QT, on="접종일", how="left")
        ss = S["품종"] == "새송이"
        for a, b in (("배양출고", "배양출고_ss"), ("긁기불량", "긁기불량_ss")):
            if a not in S:
                S[a] = np.nan
            S.loc[ss, a] = S.loc[ss, b]
        S = S.drop(columns=["배양출고_ss", "긁기불량_ss"])
    if "배양출고" in S:
        S["긁기불량률%"] = (100 * S["긁기불량"] / S["배양출고"]).round(2)
    # 팽이 사슬: 접종일·탱크가 맞는 배치의 병당중량·QC
    try:
        K = paengi_chain()
    except Exception:
        K = pd.DataFrame()
    if not K.empty and "접종일" in K:
        rows = []
        for _, r in K.dropna(subset=["접종일"]).iterrows():
            tanks = [int(t) for t in re.split(r"[,/. ]+", str(r.get("탱크") or "")) if t.strip().isdigit()]
            for t in tanks:
                rows.append({"접종일": r["접종일"], "탱크NO": t, "병당중량": r.get("병당중량_표시"), "QC": r.get("QC")})
        if rows:
            KT = pd.DataFrame(rows).groupby(["접종일", "탱크NO"]).agg(병당중량=("병당중량", "mean"), QC=("QC", "mean"), 수확배치=("병당중량", "size")).reset_index()
            KT["탱크NO"] = KT["탱크NO"].astype("Int64")
            S = S.merge(KT, on=["접종일", "탱크NO"], how="left")
    S["유래"] = S["유래"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip().replace({"None": None, "nan": None})
    return S.sort_values("일시", ascending=False).reset_index(drop=True)

# ───────────────────────── 배치 이력서 · 병목 찾기 ─────────────────────────
# 단계별 지표 정의: (열 이름, 단계, 표시 이름, 단위, 방향) — 방향 +1 = 클수록 좋음, -1 = 작을수록 좋음, 0 = 기준값과 가까울수록 좋음
def weather_daily():
    """외부 날씨 일별 자료 (없으면 빈 표). weather.update() 가 채운다."""
    from . import weather
    return weather.load()


def _phase_weather(PE, pairs):
    """배치×구간별 외기온 평균·비 온 일수. pairs = [(PE의 phase 값, 붙일 이름), ...]"""
    W = weather_daily()
    if PE.empty or W.empty:
        return pd.DataFrame(columns=["bid"])
    w = W[["date", "외기온", "비"]].copy()
    e = PE[["bid", "phase", "date"]].merge(w, on="date", how="left")
    out = None
    for ph, name in pairs:
        g = e[e.phase == ph].groupby("bid").agg(**{f"{name}외기": ("외기온", "mean"), f"{name}비일수": ("비", "sum")})
        out = g if out is None else out.join(g, how="outer")
    return (out.reset_index() if out is not None else pd.DataFrame(columns=["bid"]))


STAGE_METRICS = [
    ("종균pH", "종균", "탱크 pH", "", 0), ("액배양일", "종균", "액배양 일수", "일", 0), ("종균세대", "종균", "종균 세대", "", -1),
    ("배지pH", "배지", "배지 pH", "", 0), ("배지수분", "배지", "배지 수분", "%", 0), ("배지품온", "배지", "입병 시 배지 품온", "℃", 0), ("충진시간", "배지", "충진 시간", "", 0), ("배지함량", "배지", "영양원 함량", "g", 0),
    ("살균F0", "살균", "살균 F0(최소)", "", 1), ("살균2차유지분", "살균", "2차 유지(최소)", "분", 1), ("살균최고온도", "살균", "최고온도(최소)", "℃", 1), ("살균중단", "살균", "살균 중단", "회", -1),
    ("냉각temp_max", "냉각", "냉각실 최고(입고 시)", "℃", 0), ("냉각temp", "냉각", "냉각실 평균온도", "℃", 0), ("냉각temp_min", "냉각", "냉각실 최저(접종 아침)", "℃", 0),
    ("냉각hum_ex", "냉각", "냉각실 습도(바깥 대비)", "%p", -1), ("접종온도", "냉각", "접종 시 배지 온도", "℃", 0),
    ("접종손실률%", "접종", "접종 손실률", "%", -1),
    ("배양외기", "배양", "배양기간 바깥 기온", "℃", 0),
    ("배양일수", "배양", "배양 일수", "일", 0), ("배양temp", "배양", "배양실 온도", "℃", 0), ("배양hum", "배양", "배양실 습도", "%", -1), ("배양co2", "배양", "배양실 CO2", "ppm", -1),
    ("긁기불량률%", "배양", "균긁기 불량률", "%", -1),
    ("발이외기", "발이", "발이기간 바깥 기온", "℃", 0), ("발이비일수", "발이", "발이기간 비 온 날", "일", 0),
    ("발이실체류", "발이", "발이실 체류", "일", 0), ("발이temp", "발이", "발이실 온도", "℃", 0), ("발이hum", "발이", "발이실 습도", "%", 0), ("발이co2", "발이", "발이실 CO2", "ppm", 0),
    ("발이track", "발이", "발이실 추종오차", "℃", -1), ("억제12_14", "발이", "12~14일차 온도", "℃", -1), ("권지불량률%", "발이", "권지 불량률", "%", -1),
    ("생육외기", "생육", "생육기간 바깥 기온", "℃", 0), ("생육비일수", "생육", "생육기간 비 온 날", "일", 0),
    ("생육실체류", "생육", "생육실 체류", "일", 0), ("생육temp", "생육", "생육실 온도", "℃", 0), ("생육hum", "생육", "생육실 습도", "%", 0), ("생육co2", "생육", "생육실 CO2", "ppm", 0),
    ("생육track", "생육", "생육실 추종오차", "℃", -1),
    ("총일수", "수확", "입상→수확 일수", "일", 0), ("병당중량_표시", "수확", "병당중량", "g", 1), ("QC", "수확", "QC 점수", "점", 1), ("상등급비중", "수확", "상등급 비중", "%", 1),
]
STAGE_ENV_COLS = {"냉각temp_max", "냉각temp", "냉각temp_min", "냉각hum", "냉각hum_ex",
            "배양temp", "배양hum", "배양co2",
            "발이temp", "발이hum", "발이co2", "발이track", "억제12_14",
            "생육temp", "생육hum", "생육co2", "생육track"}


def env_start():
    """유니팜 서버가 보관 중인 환경 데이터의 시작일(보관 기간 6개월)."""
    try:
        e = env_daily()
        return pd.Timestamp(e.date.min()) if not e.empty else pd.NaT
    except Exception:
        return pd.NaT


OUTCOMES = {"병당중량_표시": ("병당중량", 1), "QC": ("QC 점수", 1), "긁기불량률%": ("균긁기 불량률", -1), "권지불량률%": ("권지 불량률", -1)}


def cooling_hum_model(env=None):
    """냉각실(두 방 평균) 하루 습도 ~ 그날 바깥 공기 수분량(g/m³). 1 g/m³ 구간별 중앙값을 이은 곡선 (x, y).
    바깥이 8 g/m³ 넘으면 냉각기 제습 한계로 66~68% 에서 평평해진다."""
    env = env_daily() if env is None else env
    w = weather_daily()
    if env.empty or w is None or w.empty:
        return None
    w = w.set_index("date")
    T, RH = w["외기온"], w["외기습도"]
    ah = 6.112 * np.exp(17.67 * T / (T + 243.5)) * RH * 2.1674 / (273.15 + T)
    h = env[env.room.isin(["냉각1실", "냉각실2"])].groupby("date").hum.mean()
    d = pd.DataFrame({"hum": h, "ah": ah}).dropna()
    if len(d) < 60:
        return None
    d["b"] = pd.cut(d.ah, np.arange(0, 40, 1.0))
    cur = d.groupby("b", observed=True).agg(x=("ah", "median"), y=("hum", "median"), n=("hum", "size"))
    cur = cur[cur.n >= 3]
    return (cur.x.values, cur.y.values, ah, h)


def cooling_hum_excess(env=None):
    """날짜별 '냉각실 습도 − 바깥 수분량으로 예상한 습도'(%p). +면 바깥에 비해 습함."""
    m = cooling_hum_model(env)
    if m is None:
        return pd.Series(dtype=float)
    x, y, ah, h = m
    d = pd.DataFrame({"hum": h, "ah": ah}).dropna()
    return (d.hum - np.interp(d.ah, x, y)).round(1)


def paengi_stage_table(K=None, PE=None, env=None):
    """팽이 배치마다 전 공정 지표를 한 줄로: 종균(탱크)·살균·접종·배양·발이·생육·수확."""
    import re
    from . import etl_retort
    K = paengi_chain() if K is None else K.copy()
    if K.empty:
        return pd.DataFrame()
    env = env_daily() if env is None else env
    PE = paengi_batch_env(K, env) if PE is None else PE
    # 종균: 접종일 + 탱크 번호
    try:
        S = tank_batches()
    except Exception:
        S = pd.DataFrame()
    if not S.empty:
        S = S.dropna(subset=["접종일"]).copy(); S["탱크NO"] = pd.to_numeric(S["탱크NO"], errors="coerce")
        rows = []
        for _, r in K.iterrows():
            tanks = [int(t) for t in re.split(r"[,/. ]+", str(r.get("탱크") or "")) if t.strip().isdigit()]
            m = S[(S["접종일"] == r["접종일"]) & (S["탱크NO"].isin(tanks))] if tanks else S.iloc[0:0]
            if m.empty and pd.notna(r["접종일"]):
                m = S[(S["접종일"] == r["접종일"]) & (S["품종"] == "팽이")]
            rows.append({"bid": r.bid, "종균코드": ", ".join(m["코드"].astype(str)) or None, "종균유래": m["유래"].iloc[0] if len(m) else None,
                         "종균세대": m["세대"].mean() if len(m) else np.nan, "종균pH": m["pH"].mean() if len(m) else np.nan,
                         "액배양일": m["액배양일"].mean() if len(m) else np.nan, "종균교반": m["교반"].iloc[0] if len(m) else None,
                         "탱크대기일": m["살균후대기일"].max() if len(m) and "살균후대기일" in m else np.nan})
        K = K.merge(pd.DataFrame(rows), on="bid", how="left")
    # 살균: 접종 전날 배지 사이클
    try:
        CY = etl_retort.load_cycles()
    except Exception:
        CY = pd.DataFrame()
    if not CY.empty and "구분" in CY:
        C = CY[CY["구분"] == "배지"].copy(); C["d"] = C.start.dt.normalize()
        g = C.groupby("d").agg(살균솥수=("unit", "size"), 살균솥=("unit", lambda x: ",".join(sorted(set(x)))), 살균F0=("F0", "min"), 살균2차유지분=("2차유지분", "min"),
                              살균최고온도=("최고온도", "min"), 살균중단=("중단횟수", "sum")).reset_index()
        K["살균일"] = K["접종일"] - pd.Timedelta(days=1)
        K = K.merge(g, left_on="살균일", right_on="d", how="left").drop(columns=["d"])
    # 솥 단위 입병 기록 (배지 pH·수분·품온, 살균기별 사이클)
    try:
        K = loads_stage_for_paengi(K)
    except Exception:
        pass
    # 냉각: 살균일 저녁 입고 → 접종일 아침까지 냉각1실·냉각실2 (방 배정 기록 없음 → 두 방 평균)
    if "살균일" not in K:
        K["살균일"] = K["접종일"] - pd.Timedelta(days=1)
    cool = env[env.room.isin(["냉각1실", "냉각실2"])] if not env.empty else pd.DataFrame()
    if not cool.empty:
        c = cool.groupby("date").agg(t=("temp", "mean"), tmin=("temp_min", "min"), tmax=("temp_max", "max"), h=("hum", "mean"))
        rows = []
        for _, r in K.iterrows():
            d0, d1 = r.get("살균일"), r.get("접종일")
            if pd.isna(d0) or pd.isna(d1):
                continue
            a, b = c.loc[d0] if d0 in c.index else None, c.loc[d1] if d1 in c.index else None
            rows.append({"bid": r.bid, "냉각temp": np.nanmean([x.t for x in (a, b) if x is not None]) if (a is not None or b is not None) else np.nan,
                         "냉각temp_max": a.tmax if a is not None else np.nan, "냉각temp_min": b.tmin if b is not None else np.nan,
                         "냉각hum": np.nanmean([x.h for x in (a, b) if x is not None]) if (a is not None or b is not None) else np.nan})
        if rows:
            K = K.merge(pd.DataFrame(rows), on="bid", how="left")
        # 바깥 대비 냉각실 습도: 냉각실은 헤파로 바깥 공기를 계속 넣는 양압 구조라 습도가 바깥 수분량을 따라간다.
        #  그날 바깥 수분량으로 예상한 습도와 실제의 차이(%p)를 살균일·접종일 평균으로 → 계절 효과를 뺀 냉각실 상태
        try:
            ex = cooling_hum_excess(env)
            if not ex.empty:
                K["냉각hum_ex"] = [np.nanmean([ex.get(d) for d in (r.get("살균일"), r.get("접종일")) if pd.notna(d) and d in ex.index] or [np.nan])
                                  for _, r in K.iterrows()]
        except Exception:
            pass
    # 환경 요약: 구간별 평균
    if not PE.empty:
        def summ(ph, name):
            e = PE[PE.phase == ph]
            if e.empty:
                return pd.DataFrame(columns=["bid"])
            a = e.groupby("bid").agg(**{f"{name}temp": ("temp", "mean"), f"{name}hum": ("hum", "mean"), f"{name}co2": ("co2", "mean"), f"{name}track": ("track_err", "mean")})
            return a.reset_index()
        for ph, name in (("배양", "배양"), ("발이실", "발이"), ("생육실", "생육")):
            K = K.merge(summ(ph, name), on="bid", how="left")
        sup = PE[(PE.phase == "발이실") & PE.cday.between(12, 14)].groupby("bid").temp.mean().rename("억제12_14").reset_index()
        K = K.merge(sup, on="bid", how="left")
        WX = _phase_weather(PE, [("배양", "배양"), ("발이실", "발이"), ("생육실", "생육")])
        if not WX.empty:
            K = K.merge(WX, on="bid", how="left")
    return K


def dossier_group(ST, b, ref="같은 계절"):
    """배치 이력서의 비교 배치(수확 완료). ref: '같은 계절'(입상 계절) | '전체' | '같은 달'(입상 월).
    배치가 8개 미만이면 한 단계 넓힌다(같은 달 → 같은 계절 → 전체). 반환 (DataFrame, 실제로 쓴 기준 설명)"""
    done = ST[ST["완결"]] if "완결" in ST else ST
    if ref == "같은 달" and "month" in done and pd.notna(b.get("month")):
        g = done[done.month == b.month]
        if len(g) >= 8:
            return g, f"같은 달({b.month} 입상)"
        ref, note = "같은 계절", " — 그 달 배치가 적어 같은 계절로"
    else:
        note = ""
    if ref == "같은 계절" and "season" in done and pd.notna(b.get("season")):
        g = done[done.season == b.season]
        if len(g) >= 8:
            return g, f"같은 계절({b.season} 입상){note}"
        note = " — 같은 계절 배치가 적어 전체로"
    return done, f"전체{note}"


def batch_dossier(bid, ST, ref="같은 계절"):
    """배치 하나의 단계별 지표 표: 항목, 값, 평균(비교군), 차이, 판정, n(비교 배치 수). ref: '같은 계절' | '전체' | '같은 달'.
    QC 점수는 서식(2026-06-19 A→B)이 바뀌면 점수 수준이 달라 같은 서식 배치끼리만 견준다."""
    if ST.empty or bid not in set(ST.bid):
        return pd.DataFrame(), None
    b = ST[ST.bid == bid].iloc[0]
    done, _ = dossier_group(ST, b, ref)
    rows = []
    e0 = env_start()
    for col, stage, name, unit, direction in STAGE_METRICS:
        if col not in ST:
            continue
        grp = done
        if col == "QC" and "QC서식" in done and pd.notna(b.get("QC서식")):
            grp = done[done["QC서식"] == b["QC서식"]]
        v = b.get(col); mu = grp[col].mean(); sd = grp[col].std(); nn = int(grp[col].notna().sum())
        if pd.isna(v):
            why = "자료 없음"
            if col in STAGE_ENV_COLS and pd.notna(e0):
                bd = b.get("입상일") if pd.notna(b.get("입상일")) else b.get("접종일")
                if pd.notna(bd) and pd.Timestamp(bd) < e0:
                    why = "환경 기록 이전"
            rows.append({"단계": stage, "항목": name, "값": None, "평균": mu, "차이": None, "z": None, "판정": why, "단위": unit, "n": nn}); continue
        z = (v - mu) / sd if sd and sd > 0 else 0
        # v1.22.0: 주의 z 1.5·나쁨 2.0·평소와 다름 2.0 (예전 1.0/1.5/1.5 는 배치마다 7~8개씩 표시돼 산만했음). '약간 다름'은 없앰
        zw, zb, zd = C.RULES.get("z_warn", 1.0), C.RULES.get("z_bad", 1.5), C.RULES.get("z_diff", 1.5)
        if direction == 0:
            verdict = "평소와 다름" if abs(z) >= zd else "보통"
        else:
            good = z * direction
            verdict = "나쁨" if good <= -zb else ("주의" if good <= -zw else ("좋음" if good >= zw else "보통"))
        rows.append({"단계": stage, "항목": name, "값": v, "평균": mu, "차이": v - mu, "z": z, "판정": verdict, "단위": unit, "n": nn})
    return pd.DataFrame(rows), b


# ───────────────────────── 배치 순위 (비교 기준별, v1.23.2) ─────────────────────────
RANK_BASES = ("같은 계절", "전체", "같은 달")
# (열, 이름, 단위, 방향 +1 높을수록 좋음 / -1 낮을수록 좋음, 소수 자리)
RANK_METRICS = (("QC", "QC 점수", "점", 1, 1), ("_불량", "불량률 (긁기+권지)", "%", -1, 2), ("병당중량_표시", "병당중량", "g", 1, 0))


def _rank_frame(ST):
    d = ST.copy()
    num = lambda c: pd.to_numeric(d[c], errors="coerce") if c in d else pd.Series(np.nan, index=d.index)
    b1, b2 = num("긁기불량률%"), num("권지불량률%")
    bad = b1.fillna(0) + b2.fillna(0)
    bad[b1.isna() & b2.isna()] = np.nan
    d["_불량"], d["QC"], d["병당중량_표시"] = bad, num("QC"), num("병당중량_표시")
    d["_fmt"] = d["QC서식"].fillna("").astype(str) if "QC서식" in d else ""
    d["_done"] = d["완결"].fillna(False).astype(bool) if "완결" in d else True
    return d


def _rank_members(d, b, basis):
    """순위를 매기는 비교군(수확 완료). 같은 계절 = 공식 종합평가와 같은 묶음, 전체 = 같은 QC 서식 전부, 같은 달 = 입상 월·QC 서식이 같은 배치"""
    if not b.get("_fmt"):                       # QC 기록이 아직 없는 배치: 서식을 따지지 않고 묶음만 (순위는 매기지 않음)
        if basis == "같은 계절":
            return d["_done"] & (d["season"] == b.get("season"))
        if basis == "같은 달":
            return d["_done"] & (d["month"] == b.get("month"))
        return d["_done"]
    if basis == "같은 계절":
        keys = str(b.get("비교군키") or f"{b.get('season')}|{b.get('_fmt')}").split("+")
        return d["_done"] & (d["season"].fillna("") + "|" + d["_fmt"]).isin(keys)
    if basis == "같은 달":
        return d["_done"] & (d["month"] == b.get("month")) & (d["_fmt"] == b.get("_fmt"))
    return d["_done"] & (d["_fmt"] == b.get("_fmt"))


def _rank_scores(m):
    """비교군 m 안에서 지표별 백분위 점수(100 = 가장 좋음)와 종합 점수(셋의 평균, 셋 다 있어야)"""
    p = {}
    for col, _, _, hi, _ in RANK_METRICS:
        v = m[col]
        r = v.rank(pct=True) * 100
        p[col] = (r if hi > 0 else 100 - r) if v.notna().sum() >= 5 else pd.Series(np.nan, index=m.index)
    return p, (p["QC"] + p["_불량"] + p["병당중량_표시"]) / 3


def _grade_of(top):
    return np.where(pd.isna(top), "—", np.where(top <= 25, "좋음", np.where(top > 75, "미흡", "보통")))


def pos_text(rank, n):
    """순위 → 위쪽 절반은 '상위 n%', 아래쪽 절반은 '하위 n%' (꼴찌를 '상위 100%'라고 쓰지 않게). 1% 아래는 1%"""
    try:
        rank, n = int(rank), int(n)
    except (TypeError, ValueError):
        return ""
    if n <= 0:
        return ""
    if 100 * rank / n <= 50:
        return f"상위 {max(1, round(100 * rank / n))}%"
    return f"하위 {max(1, round(100 * (n - rank + 1) / n))}%"


RANK_MIN_N = 8      # 같은 달 묶음(순위가 매겨지는 배치)이 이보다 적으면 같은 계절(공식)로 — 배치 이력서 비교 배치(dossier_group)와 같은 규칙


def rank_table(ST, basis="같은 계절"):
    """비교 기준별 배치 순위 (팽이). 같은 계절은 공식 종합평가(grade_batches) 그대로.
    반환 DataFrame(index=bid): 점수(0~100), 순위, 배치수, 상위%, 등급, 기준(실제로 쓴 비교 기준 — 같은 달이 적으면 '같은 계절')"""
    if ST is None or ST.empty:
        return pd.DataFrame(columns=["점수", "순위", "배치수", "상위%", "등급", "기준"])
    d = _rank_frame(ST)
    official = pd.DataFrame({"점수": d.get("종합점수"), "순위": d.get("계절순위"), "배치수": d.get("계절배치수"),
                             "상위%": d.get("계절상위%"), "등급": d.get("종합평가")}, index=d.index)
    if basis == "같은 계절":
        out = official.assign(기준="같은 계절")
    else:
        key = d["_fmt"] if basis == "전체" else d["month"].fillna("").astype(str) + "|" + d["_fmt"]
        sc_all, rk, nn = (pd.Series(np.nan, index=d.index) for _ in range(3))
        for k in key[d["_done"]].unique():
            m = d[d["_done"] & (key == k)]
            _, sc = _rank_scores(m)
            ok = sc.notna()
            if ok.any():
                r = sc[ok].rank(ascending=False, method="min")
                sc_all[r.index], rk[r.index], nn[r.index] = sc[ok], r, int(ok.sum())
        top = (100 * rk / nn).round(0).clip(lower=1)
        out = pd.DataFrame({"점수": sc_all.round(0), "순위": rk, "배치수": nn, "상위%": top, "등급": _grade_of(top), "기준": basis}, index=d.index)
        if basis == "같은 달":
            small = d["_done"] & official["순위"].notna() & ~(nn >= RANK_MIN_N)
            out.loc[small, ["점수", "순위", "배치수", "상위%", "등급"]] = official.loc[small, ["점수", "순위", "배치수", "상위%", "등급"]].values
            out.loc[small, "기준"] = "같은 계절"
    out.index = d["bid"].values
    return out


def rank_detail(ST, bid, basis="같은 계절"):
    """화면 설명용 종합평가 근거. 반환 dict — label(비교군 설명), n, rank, top, grade, score,
    rows(지표별 이 배치·비교군 중앙값·비교군 안 위치·백분위 점수), worst/best(가장 깎인·좋은 지표 문장)"""
    if ST is None or ST.empty or bid not in set(ST.bid):
        return None
    d = _rank_frame(ST)
    b = d[d.bid == bid].iloc[0]
    used = basis
    mem = d[_rank_members(d, b, used)]
    p, sc = _rank_scores(mem) if len(mem) else ({}, pd.Series(dtype=float))
    if basis == "같은 달" and int(sc.notna().sum()) < RANK_MIN_N and b["_fmt"]:      # 그 달 배치가 적으면 같은 계절(공식)로 — rank_table 과 같은 규칙
        used = "같은 계절"
        mem = d[_rank_members(d, b, used)]
        p, sc = _rank_scores(mem) if len(mem) else ({}, pd.Series(dtype=float))
    fmt_txt = f" · QC 서식 {b['_fmt']}" if b["_fmt"] else ""
    if used == "같은 계절":
        keys = str(b.get("비교군키") or "").split("+")
        seasons = [k.split("|")[0] for k in keys if k]
        label = (f"같은 계절({'·'.join(seasons)} 합쳐서 — 이번 계절 배치가 아직 적음)" if len(seasons) > 1 and b["_fmt"] else f"같은 계절({b.get('season')} 입상)") + fmt_txt
        if used != basis:
            label += f" — 그 달({b.get('month')}) 배치가 적어 같은 계절로"
    elif used == "같은 달":
        label = f"같은 달({b.get('month')} 입상){fmt_txt}"
    else:
        label = f"전체 기간{fmt_txt}"
    ok = sc.notna()
    rank = int(sc[ok].rank(ascending=False, method="min").get(b.name)) if ok.any() and b.name in sc[ok].index else None
    n = int(ok.sum())
    score = float(sc.get(b.name)) if b.name in sc.index and pd.notna(sc.get(b.name)) else np.nan
    top = max(1, round(100 * rank / n)) if rank and n else np.nan
    rows, pv = [], {}
    for col, name, unit, hi, nd in RANK_METRICS:
        v = b[col]; s = mem[col].dropna(); med = float(s.median()) if len(s) else np.nan
        u = "" if unit == "점" else unit
        if col == "_불량" and pd.notna(v):
            parts = lambda x: f"{float(x):.2f}" if pd.notna(pd.to_numeric(x, errors="coerce")) else "—"
            vt = f"{v:.{nd}f}% (긁기 {parts(b.get('긁기불량률%'))} + 권지 {parts(b.get('권지불량률%'))})"
        elif col == "병당중량_표시" and pd.notna(v):
            vt = f"{v:.{nd}f}g" + (f" ({b.get('중량기준')})" if str(b.get("중량기준") or "—") not in ("—", "nan") else "")
        elif col == "QC" and pd.notna(v):
            vt = f"{v:.{nd}f}" + (" (점검수확)" if b.get("QC종류") == "점검수확" else "")
        else:
            vt = "—"
        if pd.isna(v) or s.empty:
            pos, pc = "—", np.nan
        else:
            worse = int((s < v).sum() if hi > 0 else (s > v).sum())
            better = int((s > v).sum() if hi > 0 else (s < v).sum())
            pos = f"{len(s)}개 중 나쁜 쪽 {worse + 1}번째" if worse <= better else f"{len(s)}개 중 좋은 쪽 {better + 1}번째"
            pc = float(p[col].get(b.name)) if col in p and pd.notna(p[col].get(b.name)) else np.nan
        pv[col] = pc
        rows.append({"지표": name, "이 배치": vt, "비교군 중앙값": (f"{med:.{nd}f}{u}" if pd.notna(med) else "—"), "비교군 안 위치": pos,
                     "백분위 점수": (round(pc) if pd.notna(pc) else None)})

    def _gap(col):
        v, s = b[col], mem[col].dropna()
        med = s.median() if len(s) else np.nan
        if pd.isna(v) or pd.isna(med):
            return ""
        if col == "_불량":
            return f"비교군 중앙값 {med:.2f}%의 {v / med:.1f}배" if med > 0 and v > med else f"비교군 중앙값 {med:.2f}%보다 {abs(v - med):.2f}%p {'낮음' if v < med else '높음'}"
        if col == "QC":
            return f"비교군 중앙값 {med:.1f}보다 {abs(v - med):.1f}점 {'높음' if v > med else '낮음'}"
        return f"비교군 중앙값 {med:.0f}g보다 {abs(v - med):.0f}g {'무거움' if v > med else '가벼움'}"
    have = {k: v for k, v in pv.items() if pd.notna(v)}
    names = {c: nm for c, nm, *_ in RANK_METRICS}
    worst = best = ""
    if have:
        kw = min(have, key=have.get); kb = max(have, key=have.get)
        if have[kw] < 50:
            worst = f"{names[kw]} — {_gap(kw)}"
        if have[kb] >= 50:
            best = f"{names[kb]} — {_gap(kb)}"
    return {"label": label, "basis_used": used, "n": n, "rank": rank, "top": top, "grade": str(_grade_of(top)) if pd.notna(top) else "—",
            "score": score, "rows": rows, "worst": worst, "best": best, "official": (b.get("종합평가"), b.get("계절상위%"), b.get("계절순위"), b.get("계절배치수")),
            "qc_band": b.get("QC판정")}


def bottleneck(ST, outcome="병당중량_표시", worst_frac=0.25, period=None, side="나쁨"):
    """성적이 나쁜(side='나쁨', 하위 worst_frac) 또는 좋은(side='좋음', 상위 worst_frac) 배치들과 나머지의 단계별 지표 차이.
    반환: (지표 차이 표, 대상 배치 표). 표의 '대상 평균' = 고른 쪽 평균."""
    if ST.empty or outcome not in ST:
        return pd.DataFrame(), pd.DataFrame()
    D = ST[ST["완결"]].dropna(subset=[outcome]).copy() if "완결" in ST else ST.dropna(subset=[outcome]).copy()
    if period:
        D = D[D["입상일"] >= D["입상일"].max() - pd.Timedelta(days=period)]
    if len(D) < 8:
        return pd.DataFrame(), D
    name, direction = OUTCOMES[outcome]
    d_eff = direction if side == "나쁨" else -direction          # 좋은 쪽을 고를 땐 방향을 뒤집는다
    q = D[outcome].quantile(worst_frac if d_eff == 1 else 1 - worst_frac)
    bad = D[outcome] <= q if d_eff == 1 else D[outcome] >= q
    D["구분"] = np.where(bad, side, "나머지")
    rows = []
    for col, stage, nm, unit, d_ in STAGE_METRICS:
        if col not in D or col == outcome:
            continue
        a, c = D.loc[bad, col].dropna(), D.loc[~bad, col].dropna()
        if len(a) < 3 or len(c) < 3:
            continue
        sd = D[col].std()
        diff = a.mean() - c.mean()
        eff = diff / sd if sd and sd > 0 else 0
        # 고른 쪽이 이 지표에서 '불리한/유리한 방향'으로 치우쳤는지
        if abs(eff) < 0.5:
            note = "차이 작음"
        elif d_ == 0:
            note = "평소와 다름"
        else:
            note = "불리한 방향" if eff * d_ < 0 else "유리한 방향"
        rows.append({"단계": stage, "항목": nm, "대상 평균": a.mean(), "나머지 평균": c.mean(), "차이": diff, "효과크기": eff, "단위": unit,
                     "n": f"{len(a)}/{len(c)}", "해석": note})
    R = pd.DataFrame(rows)
    if not R.empty:
        R["중요도"] = R["효과크기"].abs()
        R = R.sort_values("중요도", ascending=False)
    return R, D[bad].sort_values(outcome, ascending=(d_eff == 1))

def retort_vs_scrape():
    """살균일(배지 사이클) → 다음날 접종 → 균긁기 불량률. 살균 조건과 배양 결과를 날짜로 잇는다."""
    from . import etl_retort
    CY = etl_retort.load_cycles()
    if CY.empty or "구분" not in CY:
        return pd.DataFrame()
    C = CY[CY["구분"] == "배지"].copy(); C["살균일"] = C.start.dt.normalize()
    g = C.groupby("살균일").agg(솥수=("unit", "size"), 솥=("unit", lambda x: ",".join(sorted(set(x)))), F0최소=("F0", "min"), F0평균=("F0", "mean"),
                              이차유지최소=("2차유지분", "min"), 최고온도최소=("최고온도", "min"), 중단=("중단횟수", "sum"), 총가동최대=("총가동분", "max")).reset_index()
    g["접종일"] = g["살균일"] + pd.Timedelta(days=1)
    J = etl_logs.load("cult_inoc")
    if not J.empty:
        J = J.groupby("일자").agg(품목=("품목", lambda x: "/".join(sorted(set(map(str, x))))), 접종량=("접종량", "sum"), 접종손실=("손실", "sum")).reset_index()
        g = g.merge(J, left_on="접종일", right_on="일자", how="left").drop(columns=["일자"])
    parts = []
    for name, crop in (("cult_scrape_paengi", "팽이"), ("cult_scrape_ss", "새송이")):
        P = etl_logs.load(name)
        if not P.empty:
            q = P.dropna(subset=["접종일"]).groupby("접종일").agg(배양출고=("배양출고", "sum"), 긁기불량=("불량", "sum"), 긁기일=("긁기일", "min")).reset_index()
            q["품목_긁기"] = crop; parts.append(q)
    if parts:
        Q = pd.concat(parts).groupby("접종일").agg(배양출고=("배양출고", "sum"), 긁기불량=("긁기불량", "sum"), 긁기일=("긁기일", "min"), 품목_긁기=("품목_긁기", "first")).reset_index()
        g = g.merge(Q, on="접종일", how="left")
        g["긁기불량률%"] = (100 * g["긁기불량"] / g["배양출고"]).round(2)
        if "품목" in g:
            g["품목"] = g["품목"].fillna(g["품목_긁기"])
        else:
            g["품목"] = g["품목_긁기"]
        g = g.drop(columns=["품목_긁기"])
    return g.sort_values("살균일", ascending=False).reset_index(drop=True)

# ───────────────────────── 일일 작업 확인표 ─────────────────────────
CHECK_ITEMS = [  # (구분, 항목, 데이터셋, 날짜열, 값 만들기, 요일 기대 규칙 키)
    ("배양센터", "입병·살균", "bottling", "date", lambda g: f"{g['crop'].iloc[0]} {g['bottles'].sum():,.0f}병 · {int(g['batches'].sum())}솥" if len(g) else None),
    ("배양센터", "살균솥 기록", "cycles", "d", lambda g: f"배지 {(g['구분']=='배지').sum()} · 액탱크 {(g['구분']=='액탱크').sum()}" + (f" · 이상 {(g['구분']=='이상').sum()}" if (g['구분']=='이상').any() else "") if len(g) else None),
    ("배양센터", "접종", "cult_inoc", "일자", lambda g: " / ".join(f"{r.품목} {r.접종량:,.0f}병(손실 {r.손실:.0f})" for r in g.itertuples()) if len(g) else None),
    ("배양센터", "액탱크 제조", "spawn_tanks", "일시", lambda g: ", ".join(f"{r.품종} 탱크{int(r.탱크NO) if pd.notna(r.탱크NO) else '?'}" for r in g.itertuples()) if len(g) else None),
    ("배양센터", "팽이 균긁기", "cult_scrape_paengi", "긁기일", lambda g: f"{g['배양출고'].sum():,.0f}병 → 발이실 {', '.join(map(str, g['방'].dropna().astype(int).unique())) if '방' in g else ''} · 불량 {100*g['불량'].sum()/g['배양출고'].sum():.2f}%" if len(g) and g['배양출고'].sum() > 0 else None),
    ("배양센터", "새송이 균긁기", "cult_scrape_ss", "긁기일", lambda g: f"{g['배양출고'].sum():,.0f}병 → {', '.join(map(str, g['방_raw'].dropna().unique())) if '방_raw' in g else ''} · 불량 {100*g['불량'].sum()/g['배양출고'].sum():.2f}%" if len(g) and g['배양출고'].sum() > 0 else None),
    ("배양센터", "권지(생육실 입실)", "cult_kwonji", "권지일", lambda g: ", ".join(f"발이실{int(r.발이실)}→생육실{int(r.생육실)} {r.입상량:,.0f}병" for r in g.itertuples() if pd.notna(r.생육실)) if len(g) else None),
    ("팽이", "생산일지(입상·생산)", "paengi_daily", "date", lambda g: f"생산 {g['total_kg'].sum():,.0f}kg · 입상 {g['ipsang'].sum():,.0f}병 (발이실 {g['balsil'].iloc[0]}, 생육실 {g['saengsil'].iloc[0]})" if len(g) and (g['total_kg'].sum() > 0 or g['ipsang'].sum() > 0) else None),
    ("팽이", "수확·QC", "paengi_harvest", "date", lambda g: f"수확방 {g['harvest_rooms'].iloc[0]} · {g['bottles'].sum():,.0f}병 · {g['g_per_bottle'].mean():.0f}g/병" if len(g) else None),
    ("팽이", "입출고 장부", "ledger_p", "date", lambda g: f"입고 {g[g['type'].isin(['입고','재고','매입','실사'])]['kg'].sum():,.0f}kg · 출고 {g[g['type'].isin(['출고','클레임출고','기타출고','폐기'])]['kg'].sum():,.0f}kg ({len(g)}건)" if len(g) else None),
    ("새송이", "작업일지(생산·작업)", "ss_daily", "date", lambda g: f"생산 {g['kg'].sum():,.0f}kg" + "".join(f" · {ev} {g[ev].iloc[0]}" for ev in ("입상", "뒤집기", "솎기", "수확", "폐상", "탈병") if ev in g and pd.notna(g[ev].iloc[0])) if len(g) and g['kg'].sum() > 0 else None),
    ("새송이", "입출고 장부", "ledger_s", "date", lambda g: f"입고 {g[g['type'].isin(['입고','재고','매입','실사'])]['kg'].sum():,.0f}kg · 출고 {g[g['type'].isin(['출고','클레임출고','기타출고','폐기'])]['kg'].sum():,.0f}kg ({len(g)}건)" if len(g) else None),
    ("환경", "유니팜 로그", "env", "date", lambda g: f"기록 {g['room'].nunique()}개 방 · 운전 중 {int((g['run'] > 0.5).sum())}개" if len(g) else None),
]


# 배양소관리·재배일보는 일과 중에 작업이 안 끝나면 다음날이나 이틀 뒤에 입력한다.
# 그 사이에 뜨는 '빠짐'은 진짜 누락이 아니라 아직 안 적은 것이므로 '입력 대기'로 본다.
CHECK_LAG = {"입병·살균": 2, "접종": 2, "팽이 균긁기": 2, "새송이 균긁기": 2, "권지(생육실 입실)": 2}

# 정규철 재배관리(솥 단위 기록)에 같은 날 기록이 있으면 작업 자체는 있었다는 뜻.
# 다만 이 파일은 앞날 예정까지 미리 적히므로, '입력 대기' 판정 근거가 아니라 참고 표시로만 쓴다.
LOADS_COL = {"입병·살균": ("입병일", None), "접종": ("접종일", None),
             "팽이 균긁기": ("균긁기일", "팽이"), "새송이 균긁기": ("균긁기일", "새송이"),
             "권지(생육실 입실)": ("권지일", None)}


def _loads_has(L, item, d):
    """정규철 재배관리에 그 항목·그 날짜 기록이 있나"""
    if L is None or L.empty or item not in LOADS_COL:
        return False
    col, crop = LOADS_COL[item]
    if col not in L:
        return False
    x = L if crop is None or "crop" not in L else L[L["crop"] == crop]
    if x.empty:
        return False
    return bool((pd.to_datetime(x[col], errors="coerce").dt.normalize() == d).any())


def _check_sources():
    from . import etl_retort
    src = {}
    for name in ("bottling", "cult_inoc", "spawn_tanks", "cult_scrape_paengi", "cult_scrape_ss", "cult_kwonji", "paengi_daily", "paengi_harvest", "ss_daily"):
        src[name] = etl_logs.load(name)
    L = etl_logs.load("ledger")
    if not L.empty:
        L = L[L["kg"].fillna(0) > 0]
        src["ledger_p"] = L[L["crop"] == "팽이"]; src["ledger_s"] = L[L["crop"] == "새송이"]
    else:
        src["ledger_p"] = src["ledger_s"] = pd.DataFrame()
    CY = etl_retort.load_cycles()
    if not CY.empty:
        CY = CY.copy(); CY["d"] = CY.start.dt.normalize()
    src["cycles"] = CY
    E = env_daily()
    if not E.empty:
        E = E[E["rows"] > 0] if "rows" in E else E
    src["env"] = E
    src["loads"] = etl_logs.load("loads")
    return src


def daily_checklist(date, days=7, src=None, today=None):
    """date 부터 거꾸로 days일의 항목별 기록 표.
    반환: (표 [구분, 항목, 날짜1..날짜N], 기대여부 dict, 상태 dict)
    기대 규칙: 지난 8주 같은 요일에 기록이 절반 이상 있었으면 '기록이 있어야 하는 날'.
    상태: state[항목][날짜] = (상태, 재배관리에도 기록 있음)
          상태 = "ok" 기록 있음 / "none" 원래 없는 날 / "wait" 입력 대기 / "miss" 빠짐
    '입력 대기'는 오늘 기준 유예 일수(CHECK_LAG) 안이라 아직 안 적었을 수 있는 경우다."""
    src = src or _check_sources()
    end = pd.Timestamp(date).normalize()
    now = pd.Timestamp(today).normalize() if today is not None else pd.Timestamp.today().normalize()
    dates = [end - pd.Timedelta(days=i) for i in range(days)]
    L = src.get("loads")
    rows, expect, state = [], {}, {}
    for grp, item, ds, dcol, fn in CHECK_ITEMS:
        D = src.get(ds)
        rec = {"구분": grp, "항목": item}
        if D is None or D.empty or dcol not in D:
            for d in dates:
                rec[d] = None
            rows.append(rec); expect[item] = {d: False for d in dates}
            state[item] = {d: ("none", False) for d in dates}; continue
        dd = pd.to_datetime(D[dcol]).dt.normalize()
        # 요일별 기록 빈도 (최근 8주, 대상일 이전)
        hist = D[(dd < end - pd.Timedelta(days=0)) & (dd >= end - pd.Timedelta(weeks=8))]
        hd = pd.to_datetime(hist[dcol]).dt.normalize()
        wk = {}
        for w in range(7):
            n_weeks = 8
            has = hd[hd.dt.weekday == w].nunique()
            wk[w] = has >= n_weeks * 0.5
        expect[item], state[item] = {}, {}
        lag = CHECK_LAG.get(item, 0)
        for d in dates:
            g = D[dd == d]
            try:
                v = fn(g) if len(g) else None
            except Exception:
                v = f"{len(g)}건"
            rec[d] = v
            exp = wk.get(d.weekday(), False)
            expect[item][d] = exp
            corro = _loads_has(L, item, d) if v is None else False
            if v is not None:
                st = "ok"
            elif not exp:
                st = "none"
            elif (now - d).days <= lag:
                st = "wait"
            else:
                st = "miss"
            state[item][d] = (st, corro)
        rows.append(rec)
    return pd.DataFrame(rows), expect, state

# ───────────────────────── 솥 단위 입병 기록 ↔ 살균 사이클 ─────────────────────────
def _fix_load_rates(L):
    """종합관리 loads 의 불량률을 기록 행 단위로 다시 계산 (제자리). 균긁기 칸은 솥별이 아니라 '그날 병을 어느 발이실에 몇 병 넣었나'이다."""
    if "긁기불량" not in L:
        return L
    b, n = pd.to_numeric(L["긁기불량"], errors="coerce"), pd.to_numeric(L.get("입상수량"), errors="coerce")
    tot = b.fillna(0) + n
    L["긁기불량률%"] = (100 * b / tot.where(tot > 0)).where(n.notna())
    if "권지불량" in L:
        kb, kn = pd.to_numeric(L["권지불량"], errors="coerce"), pd.to_numeric(L.get("권지수량"), errors="coerce")
        done = (L["권지일"].notna() if "권지일" in L else kb.notna()) & kb.notna() & kn.notna()
        kt = kb + kn                                   # 권지 기록 자체의 병수(불량+정상). 입상수량과 다른 묶음으로 적힌 행이 있어 이것을 분모로
        L["권지불량률%"] = (100 * kb / kt.where(kt > 0)).where(done)
    return L


def loads_daily(L):
    """종합관리 loads → 입병일(하루, 보통 4솥) 단위 표. 균긁기·권지 불량은 솥별이 아니라 입병일 단위로만 정확하다 (v1.24.8).
    긁기불량률 = Σ긁기불량 ÷ Σ(긁기불량+입상수량), 권지불량률 = Σ권지불량 ÷ Σ(권지불량+권지수량)(권지까지 끝난 기록), 합계 = 1 − (1−긁기)(1−권지).
    '기록률' = (긁기불량+입상수량) ÷ 접종수량 — 0.95 미만이면 그날 병이 아직 다 입상되지 않은 것(진행 중)."""
    if L is None or L.empty:
        return pd.DataFrame()
    L = _fix_load_rates(L.copy())
    num = lambda c: pd.to_numeric(L[c], errors="coerce") if c in L else pd.Series(np.nan, index=L.index)
    L["_b"], L["_n"], L["_kb"] = num("긁기불량"), num("입상수량"), num("권지불량")
    kdone = L["권지불량률%"].notna() if "권지불량률%" in L else pd.Series(False, index=L.index)
    L["_kn"] = (L["_kb"] + num("권지수량")).where(kdone)
    L["_kbb"] = L["_kb"].where(kdone)
    _s = lambda v: str(int(v)) if isinstance(v, (float, np.floating)) and float(v).is_integer() else str(v)
    joined = lambda s: " · ".join(dict.fromkeys(_s(v) for v in s if pd.notna(v) and str(v) not in ("", "nan", "None")))
    seq = lambda s: "·".join(_s(v) for v in s if pd.notna(v) and str(v) not in ("", "nan", "None"))
    _sum1 = lambda s: s.sum(min_count=1)           # 기록이 하나도 없으면 0 이 아니라 빈칸
    dts = lambda s: " · ".join(dict.fromkeys(pd.to_datetime(s.dropna()).dt.strftime("%m/%d")))
    agg = dict(솥수=("입병일", "size"), 살균기=("살균기NO", seq), 살균수량=("살균수량", "sum"), 접종수량=("접종수량", "sum"),
               pH=("pH", "mean"), 수분=("수분", "mean"), 배지품온=("배지품온", "mean"), 충진시간=("충진시간", "mean"), 함량=("함량", "mean"),
               접종일=("접종일", "min"), 종균=("종균", joined), 접종온도=("접종온도", "mean"), 균긁기일=("균긁기일", dts), 발이실=("발이실", joined),
               긁기불량=("_b", _sum1), 입상수량=("_n", _sum1), 기록행=("_n", "count"), 권지불량=("_kbb", _sum1), 권지대상=("_kn", _sum1), 권지기록=("_kn", "count"),
               비고=("긁기비고", joined))
    if "F0" in L:
        agg.update(F0최소=("F0", "min"), 이차유지최소=("2차유지분", "min"), 중단=("중단횟수", "sum"), 살균연결=("F0", "count"))
    agg = {k: v for k, v in agg.items() if v[0] in L}
    D = L.groupby(["crop", "입병일"]).agg(**agg).reset_index()
    tot = D["긁기불량"].fillna(0) + D["입상수량"]
    D["기록률"] = (tot / D["접종수량"].where(D["접종수량"] > 0)).round(3)
    ok = (D["기록행"] > 0) & (tot > 0)
    D["긁기불량률%"] = (100 * D["긁기불량"] / tot).where(ok).round(2)
    kok = D["권지기록"] > 0
    D["권지불량률%"] = (100 * D["권지불량"] / D["권지대상"].where(D["권지대상"] > 0)).where(kok).round(2)
    # 합계: 접종한 병 중 균긁기 또는 권지에서 버려진 비율 = 1 − (1−긁기)(1−권지)
    D["합계불량률%"] = (100 * (1 - (1 - D["긁기불량률%"] / 100) * (1 - D["권지불량률%"] / 100))).round(2)
    D["진행중"] = ok & (D["기록률"] < 0.95)
    D["상태"] = np.select([D["기록행"] == 0, D["진행중"], (D["권지기록"] == 0) & (D["crop"] == "팽이")], ["균긁기 전", "균긁기 진행 중", "권지 전"], "")
    return D


def loads_with_cycles():
    """정규철 종합관리의 솥 단위 입병 기록(loads)에 같은 날·같은 살균기의 살균 사이클을 붙인다.
    한 살균기가 하루 두 번 돌면 입병 종료 시각 뒤에 시작한 사이클 중 가장 가까운 것을 짝짓는다."""
    from . import etl_retort
    L = etl_logs.load("loads")
    if L.empty:
        return pd.DataFrame()
    L = L.copy()
    L["unit"] = L["살균기NO"].astype(str).str.extract(r"(\d)")[0].map(lambda x: f"{x}호기" if pd.notna(x) else None)
    # 불량률은 균긁기 기록 행 단위(그날 병 중 그 발이실로 간 몫)로: 긁기불량 ÷ (긁기불량+입상수량) (v1.24.8)
    # 권지불량률 = 권지불량 ÷ (권지불량+권지수량). 예전에는 긁기불량 ÷ 그 행 솥의 살균수량이라, 4솥치 불량이 한 행에 적힌 날은 181%처럼 나왔다.
    _fix_load_rates(L)
    CY = etl_retort.load_cycles()
    if CY.empty:
        return L
    C = CY[CY.get("구분", "배지") != "액탱크"].copy(); C["d"] = C.start.dt.normalize()
    # 예열 때문에 스팀은 입병이 끝나기 전에 켜질 수 있다 → 100℃ 도달 시각(시작+승온)이 입병 종료 뒤인 사이클을 짝짓는다
    C["cross"] = C.start.dt.hour * 60 + C.start.dt.minute + C["승온분"].fillna(0)
    C = C.sort_values("cross")
    used = set(); rows = []
    for i, r in L.sort_values(["입병일", "입병종료"]).iterrows():
        cand = C[(C.d == r["입병일"]) & (C.unit == r["unit"]) & (~C.index.isin(used))]
        pick = None
        if len(cand):
            after = cand[cand.cross >= (r["입병종료"] if pd.notna(r["입병종료"]) else 0) - 15]
            pick = after.iloc[0] if len(after) else cand.iloc[-1]
        if pick is not None:
            used.add(pick.name)
            rows.append({"idx": i, "살균시작": pick.start, "살균완료": pick.end, "F0": pick.F0, "2차유지분": pick["2차유지분"], "1차유지분": pick["1차유지분"],
                         "최고온도": pick.최고온도, "총가동분": pick.총가동분, "중단횟수": pick.get("중단횟수", 0), "살균구분": pick.get("구분", "배지")})
    M = pd.DataFrame(rows).set_index("idx") if rows else pd.DataFrame()
    return L.join(M) if not M.empty else L


def paengi_batch_pots(L, ipsang, balsil):
    """배치(입상일=균긁기일, 발이실) ↔ 솥 단위 기록(정규철) 잇기.
    기록 구조: 하루 4솥이 각각 한 행(살균수량·접종수량은 솥별로 정확). 균긁기 칸은 솥별이 아니라
    '그날 병 전체를 어느 방에 며칠에 나눠 넣었나'를 그날 행들 중 아무 행에나 적은 것이다.
    그래서 한 방(배치)은 보통 두 입병일의 병으로 채워진다(전날 남은 것 + 그날 것).
    반환 (pots, events):
      events — 이 방·이 날짜의 균긁기 기록 행: 입병일별 입상수량·긁기불량·권지수량·권지불량
      pots   — 기여한 입병일의 솥 전부(4솥씩) + 기여병수(그날 중 이 방으로 간 병 수)·기여비율"""
    if L is None or L.empty:
        return pd.DataFrame(), pd.DataFrame()
    P = L[L.crop == "팽이"].copy()
    P["발이실n"] = pd.to_numeric(P["발이실"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    ev = P[(pd.to_datetime(P["균긁기일"]) == pd.Timestamp(ipsang)) & (P["발이실n"] == balsil)].copy()
    if ev.empty:
        return pd.DataFrame(), pd.DataFrame()
    contrib = ev.groupby("입병일")["입상수량"].sum()
    pots = P[P["입병일"].isin(contrib.index)].copy().sort_values(["입병일", "입병순번"] if "입병순번" in P else ["입병일"])
    day_tot = pots.groupby("입병일")["접종수량"].transform("sum")
    pots["기여병수"] = pots["입병일"].map(contrib)
    pots["기여비율"] = pots["기여병수"] / day_tot.where(day_tot > 0)
    return pots, ev


def loads_stage_for_paengi(K):
    """팽이 배치마다 기여한 입병일의 솥 전부를 모아 배지·살균·접종 지표를 붙인다 (paengi_batch_pots 기준).
    배지 pH·수분·품온·함량·충진·접종온도는 솥 평균을 입병일 기여비율로 가중, 살균 F0·유지분·최고온도는 최소, 중단은 합."""
    L = loads_with_cycles()
    if L.empty or K.empty:
        return K
    P = L[L.crop == "팽이"].copy()
    P["발이실n"] = pd.to_numeric(P["발이실"].astype(str).str.extract(r"(\d+)")[0], errors="coerce")
    P["_d"] = pd.to_datetime(P["균긁기일"])
    rows = []
    for _, b in K.iterrows():
        pots, ev = paengi_batch_pots(P, b["입상일"], b["발이실"])
        if pots.empty:
            continue
        w = pots["기여비율"].fillna(0)
        wm = lambda c: float(np.average(pd.to_numeric(pots[c], errors="coerce").fillna(pots[c].astype(float).mean()), weights=w)) if c in pots and pots[c].notna().any() and w.sum() > 0 else np.nan
        joined = lambda c: ",".join(sorted({str(int(v)) if isinstance(v, (int, float, np.integer, np.floating)) and float(v).is_integer() else str(v)
                                            for v in pots[c] if pd.notna(v) and str(v) not in ("", "nan", "None")})) or None
        r = {"bid": b.bid, "배지pH": wm("pH"), "배지수분": wm("수분"), "배지품온": wm("배지품온"), "충진시간": wm("충진시간"), "배지함량": wm("함량"),
             "접종온도": wm("접종온도"), "솥수": len(pots), "살균기": joined("살균기NO"), "혼합기": joined("혼합기"),
             "입병일": pots["입병일"].min(), "입병일수": pots["입병일"].nunique(), "입병수량": pots["살균수량"].sum(),
             "접종수량_솥": pots["접종수량"].sum(), "접종LOSS_솥": pots["접종LOSS"].sum(), "기여병수": ev["입상수량"].sum(),
             "종균_솥": joined("종균") if "종균" in pots else None}
        for src, dst in (("설정1차온도", "살균1차온도"), ("설정1차분", "살균1차분"), ("설정2차온도", "살균2차온도"), ("설정2차분", "살균2차분")):
            if src in pots:
                r[dst] = pots[src].dropna().iloc[0] if pots[src].notna().any() else np.nan
        if "F0" in pots:
            r.update({"살균F0_솥": pots["F0"].min(), "살균2차유지_솥": pots["2차유지분"].min(), "살균최고온도_솥": pots["최고온도"].min(), "살균중단_솥": pots["중단횟수"].sum()})
        rows.append(r)
    if not rows:
        return K
    G = pd.DataFrame(rows)
    K = K.merge(G, on="bid", how="left")
    # 솥 단위 살균값이 있으면 날짜 기준값을 덮어쓴다
    for a, b in (("살균F0", "살균F0_솥"), ("살균2차유지분", "살균2차유지_솥"), ("살균최고온도", "살균최고온도_솥"), ("살균중단", "살균중단_솥")):
        if b in K:
            if a not in K:
                K[a] = np.nan
            K[a] = K[b].where(K[b].notna(), K[a]); K = K.drop(columns=[b])
    return K


def batch_materials(month, crop="팽이"):
    """원재료입고.xlsx 월 블록에서 그 달 작물의 재료 구성을 한 줄로. 반환 (구성 문자열(비율, 총 kg), 병당 재료비 원, 입병수) — 자료 없으면 (None, nan, nan).
    배치 단위 재료 기록은 없으므로 '입병한 달' 기준이다."""
    M = etl_logs.load("materials"); B = etl_logs.load("materials_block")
    if M.empty or "month" not in M:
        return None, np.nan, np.nan
    kg, won = f"{crop}_kg", f"{crop}_원"
    m = M[(M["month"] == month) & (pd.to_numeric(M.get(kg), errors="coerce") > 0)].copy()
    if m.empty:
        return None, np.nan, np.nan
    m[kg] = pd.to_numeric(m[kg], errors="coerce"); m = m.sort_values(kg, ascending=False)
    tot = m[kg].sum()
    parts = [f"{r['item']} {100 * r[kg] / tot:.0f}%" for _, r in m.iterrows() if 100 * r[kg] / tot >= 0.5]
    cost = pd.to_numeric(m.get(won), errors="coerce").sum() if won in m else np.nan
    n = np.nan
    if not B.empty and "month" in B and f"{crop}_입병수" in B:
        bb = B[B["month"] == month]
        if len(bb):
            n = pd.to_numeric(bb[f"{crop}_입병수"], errors="coerce").iloc[0]
    per = cost / n if (pd.notna(n) and n > 0 and pd.notna(cost)) else np.nan
    return " · ".join(parts) + f" (총 {tot:,.0f}kg)", per, n


def sales_monthly():
    """월별 매출·출고 (정규철 매출분석) + 원가(cost_monthly) → 병당 매출·원가·마진."""
    S = etl_logs.load("sales")
    if S.empty:
        return pd.DataFrame()
    S = S.rename(columns={"month": "월", "crop": "작물"})
    try:
        cost, _ = cost_monthly()
    except Exception:
        cost = pd.DataFrame()
    if cost is not None and not cost.empty:
        S = S.merge(cost[["월", "작물", "입병수", "합계"]].rename(columns={"합계": "병당원가"}), on=["월", "작물"], how="left")
        S["병당매출"] = S["매출원"] / S["입병수"]
        S["병당마진"] = S["병당매출"] - S["병당원가"]
        S["월원가"] = S["병당원가"] * S["입병수"]
    S["연"] = S["월"].str[:4]; S["월n"] = S["월"].str[5:].astype(int)
    return S.sort_values(["작물", "월"]).reset_index(drop=True)


# ───────────────────────── 냉각실 1분 곡선: 입고 → 식는 시간 ─────────────────────────
COOL_TARGET = 20.0      # '식었다'로 보는 온도. 설정(14~15℃)+2 는 만솥일 때 밤새 16.x℃에 머물러 기준이 흔들려서 20℃로 잰다
COOL_HOT = 22.0
COOL_RISE = 8.0
COOL_UNDER = C.RULES.get("overcool_c", 3.0)               # 설정보다 이만큼 낮으면 과냉
COOL_UNDER_MIN = int(C.RULES.get("overcool_min", 30))     # 과냉이 하루(또는 냉각 창) 이 분 이상이면 경고
COOL_SLOW_RATIO = C.RULES.get("cool_slow_ratio", 1.3)     # 냉각 느림: 평소의 이 배이면서
COOL_SLOW_ADD = C.RULES.get("cool_slow_add_min", 60)      #           평소보다 이 분 넘게 더 걸릴 때


def cooling_slow(minutes, med):
    """냉각 소요분이 평소(med)보다 뚜렷이 긴가 — 이상 감시·배치 보고서가 같은 기준을 쓴다."""
    return bool(pd.notna(med) and pd.notna(minutes) and minutes > max(COOL_SLOW_RATIO * med, med + COOL_SLOW_ADD))


def env_minute(rooms=None, d1=None, d2=None):
    return etl_env.load_minute(rooms, d1, d2)


def cooling_events(M=None, target=COOL_TARGET, min_peak=28.0):
    """냉각실 1분 곡선에서 '뜨거운 솥 입고' 사건을 찾는다.
    사건 = 30분 안에 8℃ 넘게 올라 22℃ 이상이 되면 시작(그 30분의 최저점) → 20℃ 아래로 10분 연속이면 끝.
    끝나기 전에 또 오르면(다음 솥) 같은 사건으로 묶고 솥수를 센다. 소요분 = 마지막 솥이 들어온 뒤 최고점 → 20℃."""
    M = env_minute() if M is None else M
    out = []
    if M is None or M.empty:
        return pd.DataFrame(columns=["room", "입고일", "시작", "최고시각", "최고온도", "솥수", "도달", "소요분", "완료", "설정"])
    for room, g in M.dropna(subset=["temp"]).groupby("room"):
        g = g.sort_values("ts").reset_index(drop=True)
        ts = g.ts.values; T = pd.Series(g.temp.values.astype(float)).rolling(3, center=True, min_periods=1).median().values
        sp = pd.Series(g.sp_temp.values.astype(float)).ffill().values
        n, i = len(T), 30
        while i < n:
            if (ts[i] - ts[i - 30]) / np.timedelta64(1, "m") > 90:   # 기록이 끊긴 구간은 건너뛴다
                i += 1; continue
            lo = i - 30 + int(np.argmin(T[i - 30:i + 1]))
            if T[i] >= COOL_HOT and T[i] - T[lo] >= COOL_RISE:
                s, pk, ipk, pots, j = lo, T[i], i, 1, i
                armed, runmin, lastmax, ilast = False, T[i], T[i], i
                end, done = None, False
                while j < n - 1:
                    j += 1
                    if T[j] > pk:
                        pk, ipk = T[j], j
                    if not armed:
                        if T[j] > lastmax:
                            lastmax, ilast = T[j], j          # 마지막 솥 들어온 뒤의 최고점
                        if T[j] <= lastmax - 3:
                            armed, runmin = True, T[j]
                    else:
                        runmin = min(runmin, T[j])
                        if T[j] >= COOL_HOT and T[j] - runmin >= COOL_RISE:
                            pots += 1; armed, lastmax, ilast = False, T[j], j
                    if T[j] <= target and (T[j:j + 10] <= target).all():
                        end, done = j, True; break
                    if (ts[j] - ts[s]) / np.timedelta64(1, "h") > 30 or (ts[j] - ts[j - 1]) / np.timedelta64(1, "m") > 90:
                        end = j; break
                if end is None or (not done and end >= n - 1):
                    i = n; continue                       # 기록 끝에 걸린 사건은 아직 판정 못 함
                t0 = pd.Timestamp(ts[s])
                out.append({"room": room, "입고일": (t0 - pd.Timedelta(hours=4)).normalize(), "시작": t0, "최고시각": pd.Timestamp(ts[ipk]),
                            "최고온도": round(float(pk), 1), "솥수": pots, "마지막최고시각": pd.Timestamp(ts[ilast]), "마지막최고": round(float(lastmax), 1),
                            "도달": pd.Timestamp(ts[end]), "소요분": int((ts[end] - ts[ilast]) / np.timedelta64(1, "m")),     # 마지막 솥 최고점 → 20℃
                            "완료": done, "설정": float(sp[end]) if pd.notna(sp[end]) else np.nan})
                i = end + 1
            else:
                i += 1
    E = pd.DataFrame(out)
    return E[E["최고온도"] >= min_peak].reset_index(drop=True) if len(E) else E     # 문 여닫이 정도의 작은 봉우리는 뺀다


def cooling_typical(E, room, peak, before, pots=None, days=90, tol=6.0, min_n=5):
    """같은 방·같은 솥수·비슷한 최고온도(±tol)·직전 days일 사건의 소요분 중앙값/75%. 부족하면 조건을 하나씩 푼다."""
    if E is None or E.empty:
        return np.nan, np.nan, 0
    e = E[(E.room == room) & E["완료"] & (E["시작"] < before)]
    for d, t, use_pots in ((days, tol, True), (365, tol, True), (365, 99, True), (365, tol, False), (365, 99, False)):
        s = e[(e["시작"] >= before - pd.Timedelta(days=d)) & ((e["최고온도"] - peak).abs() <= t)]
        if use_pots and pots is not None:
            s = s[s["솥수"] == pots]
        if len(s) >= min_n:
            return float(s["소요분"].median()), float(s["소요분"].quantile(0.75)), int(len(s))
    return np.nan, np.nan, 0


def batch_cooling(pairs, M=None, E=None, rooms=("냉각1실", "냉각실2")):
    """배치의 (살균일, 접종일) 쌍마다: 냉각실 곡선 창(살균일 12:00 → 접종일 10:00), 그 안의 입고 사건과 판정, 접종 아침 온도.
    반환 [(쌍, 창 1분표, 사건표, 아침온도 dict)]"""
    out = []
    if not pairs:
        return out
    d_lo = min(a for a, _ in pairs) - pd.Timedelta(days=1); d_hi = max(c for _, c in pairs) + pd.Timedelta(days=1)
    M = env_minute(list(rooms), d_lo - pd.Timedelta(days=100), d_hi) if M is None else M
    if M is None or M.empty:
        return out
    E = cooling_events(M) if E is None else E
    for a, c in pairs:
        w0, w1 = a + pd.Timedelta(hours=12), c + pd.Timedelta(hours=10)
        W = M[(M.ts >= w0) & (M.ts <= w1)]
        ev = E[(E["시작"] >= w0) & (E["시작"] <= w1)].copy() if not E.empty else pd.DataFrame()
        rows = []
        for _, r in ev.iterrows():
            med, p75, nref = cooling_typical(E, r.room, r["최고온도"], r["시작"], pots=int(r["솥수"]))
            slow = bool(r["완료"]) and cooling_slow(r["소요분"], med)
            rows.append({**r.to_dict(), "평소분": med, "평소75분": p75, "기준수": nref, "느림": bool(slow)})
        morning = {}
        for room in rooms:
            g = W[W.room == room]
            m = g[(g.ts >= c + pd.Timedelta(hours=6)) & (g.ts < c + pd.Timedelta(hours=8))]["temp"]
            # 과냉: 설정보다 COOL_UNDER ℃ 넘게 낮은 분이 COOL_UNDER_MIN 분 이상 (배지가 너무 차가워도 문제)
            spv = g["sp_temp"].ffill()
            under = g[(g["temp"] < spv - COOL_UNDER)]
            morning[room] = {"아침": float(m.mean()) if len(m) else np.nan, "설정": float(spv.median()) if spv.notna().any() else np.nan,
                             "최저": float(g["temp"].min()) if len(g) else np.nan, "최저시각": g.loc[g["temp"].idxmin(), "ts"] if g["temp"].notna().any() else pd.NaT,
                             "과냉분": int(len(under)), "과냉": bool(len(under) >= COOL_UNDER_MIN)}
        out.append(((a, c), W, pd.DataFrame(rows), morning))
    return out


def sterilization_delays(P, L=None, CY=None):
    """솥별 '입병 끝 → 살균 시작' 대기분과 '살균 시작 → 100℃' 승온분을 평소와 비교. P: 배치 솥 표(loads 행).
    반환 DataFrame [입병일, 입병순번, unit, 대기분, 승온분, 승온평소, 대기이슈, 승온이슈]"""
    if P is None or P.empty:
        return pd.DataFrame()
    CY = etl_retort.load_cycles() if CY is None else CY
    med_rise = CY[CY["구분"] == "배지"].groupby("unit")["승온분"].median() if not CY.empty else pd.Series(dtype=float)
    rows = []
    for _, r in P.iterrows():
        end_t = pd.to_datetime(r["입병일"]) + pd.to_timedelta(r["입병종료"], unit="m") if pd.notna(r.get("입병종료")) else pd.NaT
        st = pd.to_datetime(r.get("살균시작")) if pd.notna(r.get("살균시작")) else pd.NaT
        wait = (st - end_t).total_seconds() / 60 if pd.notna(st) and pd.notna(end_t) else np.nan
        rise = np.nan; typ = np.nan
        if pd.notna(st) and not CY.empty:
            c = CY[(CY.unit == r.get("unit")) & (CY.start == st)]
            if len(c):
                rise = float(c.iloc[0]["승온분"]); typ = float(med_rise.get(r.get("unit"), np.nan))
        rows.append({"입병일": r["입병일"], "입병순번": r.get("입병순번"), "unit": r.get("unit"), "입병종료": end_t, "살균시작": st,
                     "대기분": wait, "승온분": rise, "승온평소": typ,
                     "대기이슈": bool(pd.notna(wait) and wait > 60), "승온이슈": bool(pd.notna(rise) and pd.notna(typ) and rise > typ + 25)})
    return pd.DataFrame(rows)


def process_alerts(d1, d2, E=None, L=None):
    """냉각·살균·접종 공정 경고 (d1~d2 날짜, 양끝 포함): 냉각 느림 / 냉각실 과냉(낮음) / 살균 지연(입병 끝→살균 시작, 승온 늦음) / 접종 배지온도.
    반환 DataFrame [날짜, 분류, 방, 내용, 값, 심각도]"""
    d1, d2 = pd.Timestamp(d1).normalize(), pd.Timestamp(d2).normalize()
    out = []
    # 냉각실 1분 곡선
    M = env_minute(d1=d1 - pd.Timedelta(days=100), d2=d2 + pd.Timedelta(days=1))
    if M is not None and not M.empty:
        E = cooling_events(M) if E is None else E
        for _, r in E[(E["입고일"] >= d1) & (E["입고일"] <= d2)].iterrows():
            med, p75, n = cooling_typical(E, r.room, r["최고온도"], r["시작"], pots=int(r["솥수"]))
            if r["완료"] and cooling_slow(r["소요분"], med):
                out.append({"날짜": r["입고일"].date(), "분류": "냉각 느림", "방": r.room, "심각도": "중간",
                            "내용": f"{r['시작']:%H:%M} 입고분 {r.get('마지막최고', r['최고온도']):.0f}℃ → 20℃ {r['소요분'] / 60:.1f}시간 (평소 {med / 60:.1f}시간). 냉각 느림.", "값": int(r["소요분"])})
            elif not r["완료"]:
                out.append({"날짜": r["입고일"].date(), "분류": "냉각 느림", "방": r.room, "심각도": "중간",
                            "내용": f"{r['시작']:%H:%M} 입고분이 기록 안에서 20℃ 아래로 내려오지 않음", "값": None})
        Mw = M[(M.ts >= d1) & (M.ts < d2 + pd.Timedelta(days=1))].copy()
        Mw["sp"] = Mw.groupby("room")["sp_temp"].ffill()
        u = Mw[Mw["temp"] < Mw["sp"] - COOL_UNDER]
        for (room, d), g in u.groupby(["room", u.ts.dt.normalize()]):
            if len(g) >= COOL_UNDER_MIN:
                i = g["temp"].idxmin()
                # 냉각실은 자체 시스템이 제어하고 uni-farm 센서는 모니터링용 — 실제 문제는 접종 때 배지온도로 본다(아래 '접종 배지온도') (v1.30.0)
                out.append({"날짜": d.date(), "분류": "냉각실 과냉", "방": room, "심각도": "낮음",
                            "내용": f"설정 {g['sp'].median():.0f}℃보다 {COOL_UNDER:.0f}℃ 넘게 낮은 시간 {len(g) // 60}시간 {len(g) % 60:02d}분 (최저 {g.loc[i, 'temp']:.1f}℃ {g.loc[i, 'ts']:%H:%M}) — uni-farm 모니터링 센서 기준, 참고용.", "값": int(len(g))})
    # 살균 지연 (솥 단위 기록)
    try:
        L = loads_with_cycles() if L is None else L
    except Exception:
        L = pd.DataFrame()
    if L is not None and not L.empty:
        P = L[(pd.to_datetime(L["입병일"]) >= d1) & (pd.to_datetime(L["입병일"]) <= d2)]
        D = sterilization_delays(P) if len(P) else pd.DataFrame()
        for _, r in (D.iterrows() if len(D) else []):
            unit = r["unit"] if isinstance(r["unit"], str) else "살균솥"
            crop = P.loc[P["입병일"].eq(r["입병일"]) & P["입병순번"].eq(r["입병순번"]), "crop"]
            crop = f"{crop.iloc[0]} " if len(crop) else ""
            if r["대기이슈"]:
                out.append({"날짜": pd.Timestamp(r["입병일"]).date(), "분류": "살균 지연", "방": unit, "심각도": "높음" if r["대기분"] > 180 else "중간",
                            "내용": f"{crop}순번{int(r['입병순번']) if pd.notna(r['입병순번']) else '?'}: 입병 {r['입병종료']:%H:%M} 완료 후 살균 {r['살균시작']:%H:%M} 시작 — {r['대기분'] / 60:.1f}시간 대기 (평소 10분 안). 살균 대기 지연.", "값": int(r["대기분"])})
            if r["승온이슈"]:
                out.append({"날짜": pd.Timestamp(r["입병일"]).date(), "분류": "살균 지연", "방": unit, "심각도": "중간",
                            "내용": f"{crop}순번{int(r['입병순번']) if pd.notna(r['입병순번']) else '?'}: 100℃까지 {r['승온분']:.0f}분 (평소 {r['승온평소']:.0f}분). 승온 지연(밀폐·압력 의심).", "값": int(r["승온분"])})
        out += inoc_temp_alerts(L, d1, d2)
    A = pd.DataFrame(out, columns=["날짜", "분류", "방", "심각도", "내용", "값"])
    return A.sort_values(["날짜", "분류"], ascending=[False, True]).reset_index(drop=True) if len(A) else A


def inoc_temp_alerts(L, d1, d2):
    """접종 시 배지온도 (종합관리 '접종온도', 솥 단위) — 너무 차갑거나(과냉) 덜 식은 채 접종한 솥을 접종일·작물별 한 줄로 (v1.30.0).
    기준: RULES inoc_temp_low~high 밖이면 중간, inoc_temp_low_hi 미만·inoc_temp_high_hi 초과면 높음."""
    R = C.RULES
    lo, hi = R.get("inoc_temp_low", 13.0), R.get("inoc_temp_high", 16.0)
    lo2, hi2 = R.get("inoc_temp_low_hi", 12.0), R.get("inoc_temp_high_hi", 17.0)
    if L is None or L.empty or "접종온도" not in L or "접종일" not in L:
        return []
    X = L.copy()
    X["접종일"] = pd.to_datetime(X["접종일"], errors="coerce")
    X["접종온도"] = pd.to_numeric(X["접종온도"], errors="coerce")
    X = X[(X["접종일"] >= d1) & (X["접종일"] <= d2) & X["접종온도"].notna()]
    out = []
    for (crop, d), g in X.groupby(["crop", "접종일"]):
        bad = g[(g["접종온도"] < lo) | (g["접종온도"] > hi)].sort_values("입병순번")
        if bad.empty:
            continue
        cold, hot = bad[bad["접종온도"] < lo], bad[bad["접종온도"] > hi]
        sev = "높음" if ((bad["접종온도"] < lo2) | (bad["접종온도"] > hi2)).any() else "중간"
        sq = lambda b: "·".join(f"{int(n)}" if pd.notna(n) else "?" for n in b["입병순번"])
        tv = lambda b: "·".join(f"{t:.1f}" for t in b["접종온도"])
        parts = []
        if len(hot):
            parts.append(f"덜 식음 — 순번 {sq(hot)} 배지 {tv(hot)}℃")
        if len(cold):
            parts.append(f"과냉 — 순번 {sq(cold)} 배지 {tv(cold)}℃")
        inb = pd.to_datetime(g["입병일"]).min()
        val = float(hot["접종온도"].max()) if len(hot) else float(cold["접종온도"].min())
        out.append({"날짜": d.date(), "분류": "접종 배지온도", "방": f"{crop} 접종", "심각도": sev,
                    "내용": f"{inb:%m/%d} 입병분 {len(g)}솥 중 {len(bad)}솥: " + " / ".join(parts)
                            + f" (기준 {lo:g}~{hi:g}℃, 그날 범위 {g['접종온도'].min():.1f}~{g['접종온도'].max():.1f}℃)", "값": val})
    return out
