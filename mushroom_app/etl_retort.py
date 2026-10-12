# -*- coding: utf-8 -*-
"""살균솥 기록(.cld) 읽기.
- 파일 형식: 16바이트 고정 레코드 반복 = [예약 int32(0)][유닉스 시각 int32][값 float64]. 값이 없으면 float 최댓값(1.8e308).
- 폴더 이름 = 태그: 살균기N_현재온도(℃) / 살균N현재압력(kPa) / 살균기N_스팀밸브·상부밸브·하부밸브(0/1)
                  / 살균기N_상한경보·하한경보·냉각완료·냉각시작(설정값). 파일 이름 = 날짜(YYYYMMDD.cld).
- 캐시: retort_1min.parquet (솥·1분 평균: 온도·압력·밸브), retort_daily.parquet (솥·태그·일별 요약),
        retort_cycles.parquet (온도 곡선에서 찾은 살균 사이클: 시작·1차유지·2차유지·냉각·F0).
  10초 원본 곡선은 필요할 때 파일을 직접 읽는다(load_day).
"""
import os, re, glob, json
import numpy as np
import pandas as pd
from . import config as C

STATE = os.path.join(C.CACHE_DIR, "retort_state.json")
SCHEMA = 4                      # 캐시 구조 번호. 바뀌면 전부 다시 읽는다
MIN1 = os.path.join(C.CACHE_DIR, "retort_1min.parquet")
MINW = os.path.join(C.CACHE_DIR, "retort_1min_wide.parquet")    # 솥별 넓은 표 (앱이 읽는 것)
DAILY = os.path.join(C.CACHE_DIR, "retort_daily.parquet")
CYCLES = os.path.join(C.CACHE_DIR, "retort_cycles.parquet")
_REC = np.dtype([("r", "<i4"), ("t", "<i4"), ("v", "<f8")])
_NA = 1e300
TAG_KO = {"현재온도": "온도", "현재압력": "압력", "스팀밸브": "스팀", "상부밸브": "상부", "하부밸브": "하부",
          "상한경보": "상한경보", "하한경보": "하한경보", "냉각완료": "냉각완료온도", "냉각시작": "설정"}
SERIES_TAGS = ("온도", "압력", "스팀", "상부", "하부")        # 1분 시계열로 저장
_TAG_RE = re.compile(r"살균기?\s*(\d+)\s*_?\s*(현재온도|현재압력|스팀밸브|상부밸브|하부밸브|상한경보|하한경보|냉각완료|냉각시작)")


def base_dir():
    return C.log_dir("retort")


def read_cld(path):
    """한 파일 → DataFrame[ts(한국시각), val]. 결측은 NaN."""
    with open(path, "rb") as fh:
        d = fh.read()
    n = len(d) // 16
    if n == 0:
        return pd.DataFrame({"ts": pd.Series(dtype="datetime64[ns]"), "val": pd.Series(dtype=float)})
    a = np.frombuffer(d[:n * 16], dtype=_REC)
    ts = pd.to_datetime(a["t"].astype("int64"), unit="s", utc=True).tz_convert("Asia/Seoul").tz_localize(None)
    v = a["v"].astype(float)
    v[(v > _NA) | ~np.isfinite(v)] = np.nan
    df = pd.DataFrame({"ts": ts, "val": v})
    return df[df.ts.dt.year >= 2000].sort_values("ts").reset_index(drop=True)


def classify(path, base=None):
    """경로 → (솥 이름 '1호기', 태그 '온도'). 폴더 이름을 먼저, 없으면 파일 이름(최상위 낱개 파일)을 본다."""
    base = base or base_dir()
    rel = os.path.relpath(path, base).replace("\\", "/")
    for part in reversed(rel.split("/")):
        m = _TAG_RE.search(part)
        if m:
            return f"{int(m.group(1))}호기", TAG_KO[m.group(2)]
    return None, None


def files():
    base = base_dir()
    out = []
    for p in sorted(glob.glob(os.path.join(base, "**", "*.cld"), recursive=True)):
        if "#recycle" in p:
            continue
        unit, tag = classify(p, base)
        if unit:
            out.append((unit, tag, p))
    return out


def _date_of(path, df):
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", os.path.basename(path))
    if m:
        return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return df.ts.iloc[0].normalize() if len(df) else pd.NaT


def _summ(df, unit, tag, path):
    ok = df.dropna(subset=["val"])
    row = {"unit": unit, "tag": tag, "date": _date_of(path, df), "file": path, "n": int(len(df)), "na": int(df.val.isna().sum()),
           "first": df.ts.iloc[0] if len(df) else pd.NaT, "last": df.ts.iloc[-1] if len(df) else pd.NaT,
           "min": np.nan, "max": np.nan, "mean": np.nan, "values": ""}
    if not ok.empty:
        row.update({"min": float(ok.val.min()), "max": float(ok.val.max()), "mean": float(ok.val.mean())})
        if tag not in SERIES_TAGS:
            row["values"] = ",".join(f"{v:g}" for v in sorted(ok.val.unique())[:12])
    return row


def run(progress=None, force=False):
    """바뀐 .cld 파일만 다시 읽어 캐시에 합친다. 반환: 읽은 파일 수."""
    C.ensure_dirs()
    st = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    if st.get("_schema") != SCHEMA:          # 예전 구조(채널 이름 기반) 캐시는 버리고 새로 만든다
        force = True
    if force:
        st = {}
        for f_ in (DAILY, MIN1, MINW, CYCLES):
            if os.path.exists(f_):
                os.remove(f_)
    st["_schema"] = SCHEMA
    daily = pd.read_parquet(DAILY) if os.path.exists(DAILY) else pd.DataFrame()
    m1 = pd.read_parquet(MIN1) if os.path.exists(MIN1) else pd.DataFrame()
    if not daily.empty and "unit" not in daily:
        daily = pd.DataFrame()
    if not m1.empty and "unit" not in m1:
        m1 = pd.DataFrame()
    d_rows, m_parts, live, n = [], [], set(), 0
    for unit, tag, p in files():
        live.add(p)
        mt = os.path.getmtime(p)
        if st.get(p, {}).get("mtime") == mt:
            continue
        try:
            df = read_cld(p)
            d_rows.append(_summ(df, unit, tag, p))
            if tag in SERIES_TAGS and not df.empty:
                s = df.set_index("ts").val.resample("1min").mean().dropna()
                m_parts.append(pd.DataFrame({"unit": unit, "tag": tag, "ts": s.index, "val": s.values, "file": p}))
            st[p] = {"mtime": mt, "rows": int(len(df))}
        except Exception as e:
            st[p] = {"mtime": mt, "error": f"{type(e).__name__}: {e}"}
            if progress:
                progress(f"오류 {os.path.basename(p)}: {e}")
            continue
        n += 1
        if progress and n % 100 == 0:
            progress(f"살균솥 기록 {n}개 파일 읽음…")
    gone = [k for k in st if k not in live and not k.startswith("_")]
    for k in gone:
        st.pop(k, None)
    changed = bool(d_rows) or bool(gone) or force
    if changed:
        new_d = pd.DataFrame(d_rows)
        if not daily.empty:
            daily = daily[daily.file.isin(live) & ~daily.file.isin(new_d.file if not new_d.empty else [])]
        daily = pd.concat([daily, new_d], ignore_index=True) if not new_d.empty else daily
        new_m = pd.concat(m_parts, ignore_index=True) if m_parts else pd.DataFrame()
        if not m1.empty:
            m1 = m1[m1.file.isin(live) & ~m1.file.isin(new_m.file if not new_m.empty else [])]
        m1 = pd.concat([m1, new_m], ignore_index=True) if not new_m.empty else m1
        if not daily.empty:
            daily = daily.sort_values(["unit", "tag", "date"]).reset_index(drop=True)
            daily.to_parquet(DAILY + ".tmp", index=False); os.replace(DAILY + ".tmp", DAILY)
        if not m1.empty:
            m1 = m1.drop_duplicates(["unit", "tag", "ts"], keep="last").sort_values(["unit", "tag", "ts"]).reset_index(drop=True)
            m1.to_parquet(MIN1 + ".tmp", index=False); os.replace(MIN1 + ".tmp", MIN1)
            if progress:
                progress("살균 사이클 찾는 중…")
            W = wide(m1)
            W.to_parquet(MINW + ".tmp", index=False); os.replace(MINW + ".tmp", MINW)
            cy = find_cycles(W)
            cy.to_parquet(CYCLES + ".tmp", index=False); os.replace(CYCLES + ".tmp", CYCLES)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    return n


# ───────────────────────── 사이클 ─────────────────────────
def wide(m1):
    """긴 형식(unit, tag, ts, val) → 솥별 넓은 표 [unit, ts, 온도, 압력, 스팀, 상부, 하부]."""
    if m1.empty:
        return pd.DataFrame()
    w = m1.pivot_table(index=["unit", "ts"], columns="tag", values="val", aggfunc="mean").reset_index()
    w.columns.name = None
    for t in SERIES_TAGS:
        if t not in w:
            w[t] = np.nan
    return w.sort_values(["unit", "ts"]).reset_index(drop=True)


def _segments(mask, ts, max_gap_min=10):
    """True 구간 목록 [(i0, i1)] (인덱스 폐구간). 짧은 끊김은 이어 붙인다."""
    idx = np.flatnonzero(mask)
    if len(idx) == 0:
        return []
    segs, s0, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if (ts[i] - ts[prev]) / np.timedelta64(1, "m") > max_gap_min or i - prev > max_gap_min:
            segs.append((s0, prev)); s0 = i
        prev = i
    segs.append((s0, prev))
    return segs


def find_cycles(W, hi=100.0, hold1=(102.0, 109.0), z=10.0, tref=121.1):
    """1분 온도 곡선에서 살균 사이클을 찾는다.
    사이클 = 온도가 100℃ 이상인 연속 구간(30분 이상). 시작 = 그 앞 4시간 안에서 스팀밸브가 켜진 시점(없으면 온도 최저점),
    끝 = 100℃ 아래로 내려온 시점(냉각완료). 1차 유지 = 102~109℃ 구간, 2차 유지 = 최고온도−1.5℃ 이상 구간, F0(z=10, 121.1℃)."""
    out = []
    if W is None or W.empty:
        return pd.DataFrame()
    for unit, g in W.groupby("unit"):
        g = g.dropna(subset=["온도"]).sort_values("ts").reset_index(drop=True)
        if g.empty:
            continue
        ts = g.ts.values; T = g["온도"].values.astype(float)
        P = g["압력"].values.astype(float) if "압력" in g else np.full(len(g), np.nan)
        S = g["스팀"].values.astype(float) if "스팀" in g else np.full(len(g), np.nan)
        # 100℃ 이상 구간. 앞 구간이 2차 유지를 끝내지 못한 채(패킹 불량·문제 발생) 100℃ 아래로 떨어졌다가
        # 90분 안에 다시 오르면 같은 사이클(중단 1회)로 본다. 아주 짧은 끊김(15분 이내)도 이어 붙인다.
        raw = _segments(T >= hi, ts)
        segs = []
        for i0, i1 in raw:
            if segs:
                a0, a1, _ = segs[-1]
                gap = (ts[i0] - ts[a1]) / np.timedelta64(1, "m")
                pa = np.nanmax(T[a0:a1 + 1]); held = int((T[a0:a1 + 1] >= pa - 1.5).sum())
                incomplete = pa < 112 or held < 20
                if gap <= 15 or (gap <= 90 and incomplete):
                    segs[-1] = (a0, i1, segs[-1][2] + 1)
                    continue
            segs.append((i0, i1, 0))
        for i0, i1, dips in segs:
            dur = (ts[i1] - ts[i0]) / np.timedelta64(1, "m")
            if dur < 30:
                continue
            t_cross = ts[i0]
            w0 = np.searchsorted(ts, t_cross - np.timedelta64(4, "h"))
            # 시작: 교차 전 4시간 안에서, 15분 이상 꺼져 있던 스팀밸브가 켜진 시점. 없으면 온도 최저점
            si = None
            son = S[w0:i0 + 1]
            if np.isfinite(son).any() and (son > 0.5).any():
                on = son > 0.5
                k = len(on) - 1
                while k > 0 and on[k]:
                    k -= 1
                # k = 마지막 꺼짐. 그 뒤 첫 켜짐이 시작. 앞쪽에 더 긴 켜짐 블록이 있으면(초기 예열) 첫 켜짐을 택한다
                first_on = np.flatnonzero(on)
                si = w0 + int(first_on[0]) if len(first_on) else None
                # 첫 켜짐 이전 30분 이내에 100℃ 이상 기록이 있으면(직전 사이클) 마지막 꺼짐 다음으로
                if si is not None and si > 0 and (T[max(0, si - 30):si] >= hi).any():
                    si = w0 + k + 1
            if si is None:
                si = w0 + int(np.nanargmin(T[w0:i0 + 1]))
            t_start = ts[si]
            seg = slice(si, i1 + 1)
            Ts, tss = T[seg], ts[seg]
            peak = float(np.nanmax(T[i0:i1 + 1])); ipk = i0 + int(np.nanargmax(T[i0:i1 + 1]))
            # 2차 유지: 최고온도−1.5 이상인 분
            hold2 = T[i0:i1 + 1] >= peak - 1.5
            h2_idx = np.flatnonzero(hold2)
            h2_start, h2_end = i0 + int(h2_idx[0]), i0 + int(h2_idx[-1])
            # 1차 유지: 2차 승온 전(첫 ≥ hold1[1]+3 이전)의 102~109 구간
            rise2 = np.flatnonzero(T[i0:i1 + 1] >= hold1[1] + 3.0)
            r2 = i0 + int(rise2[0]) if len(rise2) else h2_start
            m1mask = (T[i0:r2 + 1] >= hold1[0]) & (T[i0:r2 + 1] <= hold1[1])
            hold1_min = float(m1mask.sum()); hold1_t = float(np.nanmean(T[i0:r2 + 1][m1mask])) if m1mask.any() else np.nan
            f0 = float(np.nansum(10 ** ((T[si:i1 + 1] - tref) / z)))   # 1분 간격이므로 그대로 분 단위
            out.append({"unit": unit, "start": pd.Timestamp(t_start), "end": pd.Timestamp(ts[i1]), "date": pd.Timestamp(t_start).normalize(),
                        "승온분": round((t_cross - t_start) / np.timedelta64(1, "m")), "1차유지분": hold1_min, "1차유지온도": round(hold1_t, 1) if hold1_t == hold1_t else np.nan,
                        "2차승온분": round((ts[h2_start] - ts[r2]) / np.timedelta64(1, "m")) if r2 <= h2_start else 0,
                        "2차유지분": float(hold2.sum()), "2차유지온도": round(float(np.nanmean(T[i0:i1 + 1][hold2])), 1), "최고온도": round(peak, 1),
                        "최고시각": pd.Timestamp(ts[ipk]), "냉각분": round((ts[i1] - ts[h2_end]) / np.timedelta64(1, "m")),
                        "총가동분": round((ts[i1] - t_start) / np.timedelta64(1, "m")), "F0": round(f0, 1),
                        "최고압력": round(float(np.nanmax(P[si:i1 + 1])), 1) if np.isfinite(P[si:i1 + 1]).any() else np.nan,
                        "중단횟수": int(dips), "완료": bool(peak >= 112 and hold2.sum() >= 20)})
    cy = pd.DataFrame(out)
    if not cy.empty:
        cy = cy.sort_values(["unit", "start"]).reset_index(drop=True)
        cy["번호"] = cy.groupby("unit").cumcount() + 1
        cy["구분"] = [cycle_kind(r) for _, r in cy.iterrows()]
    return cy


def cycle_kind(r):
    """사이클 종류. 배지: 1차(105℃)·2차(118℃) 유지가 모두 있는 일반 살균 / 액탱크: 1차 유지 없이 119℃를 2시간 이상 유지 / 이상: 그 외(중단·미완료)."""
    if not r["완료"]:
        return "이상"
    if r["1차유지분"] < 15 and r["2차유지분"] >= 100:
        return "액탱크"
    if r["1차유지분"] >= 30 and r["2차유지분"] >= 40:
        return "배지"
    return "이상"


# ───────────────────────── 읽기 ─────────────────────────
def load_daily():
    if not os.path.exists(DAILY):
        return pd.DataFrame()
    df = pd.read_parquet(DAILY)
    if "unit" not in df:
        return pd.DataFrame()
    df = df.dropna(subset=["unit"])
    for c in ("date", "first", "last"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def load_1min(units=None, d1=None, d2=None):
    """솥별 1분 넓은 표 [unit, ts, 온도, 압력, 스팀, 상부, 하부]. 인자를 주면 그 범위만."""
    if not os.path.exists(MINW):
        return pd.DataFrame()
    filt = []
    if units:
        filt.append(("unit", "in", list(units)))
    if d1 is not None:
        filt.append(("ts", ">=", pd.Timestamp(d1)))
    if d2 is not None:
        filt.append(("ts", "<", pd.Timestamp(d2) + pd.Timedelta(days=1)))
    df = pd.read_parquet(MINW, filters=filt or None)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def load_cycles():
    if not os.path.exists(CYCLES):
        return pd.DataFrame()
    df = pd.read_parquet(CYCLES)
    for c in ("start", "end", "date", "최고시각"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def load_day(unit, date, tags=SERIES_TAGS, daily=None):
    """한 솥의 하루치 10초 원본 [ts, 온도, 압력, 스팀, 상부, 하부] (파일 직접 읽기)."""
    daily = load_daily() if daily is None else daily
    d = pd.Timestamp(date).normalize()
    sel = daily[(daily.unit == unit) & (daily.date == d) & (daily.tag.isin(tags))]
    parts = {}
    for _, r in sel.iterrows():
        if os.path.exists(r.file):
            s = read_cld(r.file).set_index("ts").val
            s.index = s.index.floor("10s")                 # 태그마다 기록 시각이 몇 초씩 다르므로 10초 격자에 맞춘다
            parts[r.tag] = s[~s.index.duplicated()]
    if not parts:
        return pd.DataFrame()
    df = pd.DataFrame(parts).sort_index()
    if "온도" in df:
        # 밸브 상태는 온도 시각에 맞춰 최근값으로 채운다
        for t in ("스팀", "상부", "하부", "압력"):
            if t in df:
                df[t] = df[t].ffill(limit=3)
    return df.reset_index().rename(columns={"index": "ts"})


def status():
    return json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}


# ── 살균 2차 유지 압력 점검 (v1.24.6, v1.24.7 에 batch_report 에서 옮김 — 알림·살균 페이지도 씀) ──
# 포화증기: 게이지 압력(kPa) → 절대압(MPa) → 포화온도(℃). 솥 안이 순수 증기면 온도 ≈ 포화온도.
_PT = np.array([0.101325, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.24])
_TT = np.array([100.0, 104.8, 109.3, 113.3, 116.9, 120.2, 123.3, 126.1])
_H2 = {}
SUSPECT = "임의 설정 변경 의심"      # 2차 온도·압력이 함께 낮고 스팀은 평소대로 → 조작판에서 2차 설정을 낮춘 것으로 보임 (v1.24.7)


def _tsat(kpa):
    return float(np.interp((float(kpa) + 101.325) / 1000, _PT, _TT))


def hold2_stats():
    """사이클별 2차 유지 구간(최고온도−1.5℃ 이상인 분)의 압력 중앙값(kPa)·스팀밸브 열림 비율. 캐시 파일 시각이 같으면 다시 계산하지 않는다."""
    try:
        key = (os.path.getmtime(MINW), os.path.getmtime(CYCLES))
    except OSError:
        return pd.DataFrame()
    if _H2.get("key") == key:
        return _H2["df"]
    CY, W = load_cycles(), load_1min()
    rows = []
    if len(CY) and len(W):
        for u, g in W.groupby("unit"):
            g = g.sort_values("ts")
            ts = g["ts"].values
            for _, r in CY[CY.unit == u].iterrows():
                i0, i1 = np.searchsorted(ts, np.datetime64(r.start)), np.searchsorted(ts, np.datetime64(r.end), "right")
                s_ = g.iloc[i0:i1]
                h = s_[s_["온도"] >= r["최고온도"] - 1.5]
                rows.append((u, r.start, h["압력"].median() if len(h) else np.nan, h["스팀"].mean() if len(h) else np.nan))
    R = pd.DataFrame(rows, columns=["unit", "start", "2차압력", "2차스팀"])
    if len(R):
        R = R.merge(CY[["unit", "start", "구분", "2차유지온도"]], on=["unit", "start"], how="left")
    _H2.update(key=key, df=R)
    return R


def hold2_ref(H, unit, start, days=30):
    """같은 솥의 정상 배지 사이클(2차 유지온도 평소대로, 압력 기록 정상) — 앞뒤 30일, 모자라면 전체. (평소 압력, 평소 온도, 평소 스팀 열림, 개수)"""
    if H is None or H.empty:
        return None
    S = H[(H.unit == unit) & (H["구분"] == "배지") & (H.start != start) & (H["2차압력"] > 30)]
    if S.empty:
        return None
    S = S[(S["2차유지온도"] - S["2차유지온도"].median()).abs() < 0.8]
    N = S[(S.start - pd.Timestamp(start)).abs() <= pd.Timedelta(days=days)]
    if len(N) < 5:
        N = S
    if len(N) < 5:
        return None
    return N["2차압력"].median(), N["2차유지온도"].median(), N["2차스팀"].median(), len(N)


def hold2_diag(r):
    """2차 유지온도가 낮은 사이클의 압력 진단. 반환 (짧은 표시, 설명 문장). r: 사이클 행(2차압력·평소2차압력 등 포함)."""
    P, T = r.get("2차압력"), r.get("2차유지온도")
    Pr, Tr, Sr, stm = r.get("평소2차압력"), r.get("평소2차온도"), r.get("평소2차스팀"), r.get("2차스팀")
    if pd.isna(P) or P < 30:
        return "압력 기록 없음", "압력 값이 0 근처라(센서 이상·미연결) 압력으로는 원인을 가릴 수 없음."
    if pd.isna(Pr) or pd.isna(T):
        return "", f"2차 유지 압력 {P:.0f}kPa (포화증기 {_tsat(P):.1f}℃) — 비교할 평소 기록이 모자람."
    Ts, Tsr = _tsat(P), _tsat(Pr)
    dT, dTs = T - Tr, Ts - Tsr
    stm_t = f"스팀밸브 열림 {stm:.0%}(평소 {Sr:.0%})" if pd.notna(stm) and pd.notna(Sr) else ""
    if abs(dTs - dT) <= 1.0 and dTs < -0.8:
        if pd.notna(stm) and stm >= max(0.6, (Sr if pd.notna(Sr) else 0.25) * 2.5):
            return "압력도 낮음·스팀 부족", (f"압력도 {P:.0f}kPa(평소 {Pr:.0f}kPa)로 같이 낮음. {stm_t} — 밸브를 거의 계속 열고도 못 올림 "
                                        f"→ 스팀 공급 부족(보일러 압력·배관) 의심.")
        return SUSPECT, (f"압력도 {P:.0f}kPa(평소 {Pr:.0f}kPa)로 같이 낮음 — 그 압력의 포화증기 온도 {Ts:.1f}℃와 실제 {T:.1f}℃가 맞음. "
                                     f"{stm_t} — 밸브가 평소처럼 여닫으며 유지했으니 스팀 부족은 아님 → 솥이 낮은 목표값에 맞춰 제어된 것: "
                                     f"설정 변경 기록 없이 2차 유지 설정(온도 또는 압력)을 낮춘 것으로 보임(임의 설정 변경 의심). "
                                     f"2차 유지 목표값은 살균솥 기록 파일에 남지 않으니 조작판의 2차 압력·온도 설정을 확인.")
    if dTs >= -0.5:
        return "압력 정상·온도만 낮음", (f"압력은 {P:.0f}kPa(평소 {Pr:.0f}kPa)로 그대로인데 온도만 {T:.1f}℃ — 포화증기라면 {Ts:.1f}℃여야 함 "
                                     f"→ 솥 안 공기 배출 불량(공기가 남아 온도가 덜 오름) 또는 온도 센서 이상 의심.")
    return "압력·온도 안 맞음", (f"압력 {P:.0f}kPa(평소 {Pr:.0f}kPa, 포화증기 {Ts:.1f}℃)와 온도 {T:.1f}℃의 내려간 폭이 맞지 않음 — 곡선 확인 필요.")


def hold2_all(margin=1.5):
    """모든 배지 사이클 중 2차 유지온도가 같은 솥 평소보다 margin℃ 넘게 낮은 것 + 압력 진단.
    반환 [unit, start, 2차유지온도, 2차압력, 2차스팀, 평소2차압력, 평소2차온도, 평소2차스팀, 평소n, 진단, 설명, 설정의심]"""
    H = hold2_stats()
    cols = ["unit", "start", "2차유지온도", "2차압력", "2차스팀", "평소2차압력", "평소2차온도", "평소2차스팀", "평소n", "진단", "설명", "설정의심"]
    if H is None or H.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for _, r in H[(H["구분"] == "배지") & H["2차유지온도"].notna()].iterrows():
        ref = hold2_ref(H, r.unit, r.start)
        if ref is None or not (r["2차유지온도"] < ref[1] - margin):
            continue
        d = dict(r)
        d.update(zip(["평소2차압력", "평소2차온도", "평소2차스팀", "평소n"], ref))
        short, long_ = hold2_diag(d)
        rows.append({**{c: d.get(c) for c in cols[:9]}, "진단": short, "설명": long_, "설정의심": short == SUSPECT})
    return pd.DataFrame(rows, columns=cols)

