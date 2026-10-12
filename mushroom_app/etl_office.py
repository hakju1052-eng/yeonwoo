# -*- coding: utf-8 -*-
"""경리(사무실) 자료 읽기 — '경영 자료' 페이지(관리자 전용, v1.25.0).

원본: Z:\\사무실\\SynologyDrive (없으면 Downloads\\SynologyDrive_사본). config.LOG_SOURCES["office"].
읽는 파일(이름 패턴, 같은 종류가 여럿이면 가장 최근 수정본):
  월별 매출,물량.xlsx          — 'YYYY년 매출' 시트: 팽이·새송이 월별 물량(kg)·박스·매출(원) / '대양청과 단가차액' 일별 박스 단가
  YYYY년 매입매출.xlsx          — '매입매출' 시트: 사업장(연우본점·연우지점·진성)별 월 매출·매입(과세·면세)
  M월 팽이시장단가.xlsx / M월 새송이시장단가.xlsx — '단가&출고량'·'누적' 시트: 거래처(·품목)별 월 출고량·평균단가
  거래내역 카톡\\(YYYY\\)M월 거래명세표_*.xls  — 경리나라 매출 거래명세표(거래일·거래처·품목·공급가액·재배사)
  YYYY)수도전기 사용내역.xlsx   — '전기'·'수도' 시트: 사용 장소별 월 고지 사용량·금액
계좌·잔액·개인 입출금·아이디/비밀번호 파일은 읽지 않는다.
"""
import os, re, glob
import datetime as dt
import numpy as np
import pandas as pd
from . import config as C

_CACHE = {}


def root():
    return C.log_dir("office")


def _cached(path, fn):
    """(경로, 수정시각)이 같으면 다시 읽지 않는다."""
    try:
        key = (path, os.path.getmtime(path), fn.__name__)
    except OSError:
        return pd.DataFrame()
    if key not in _CACHE:
        try:
            _CACHE[key] = fn(path)
        except Exception as e:                       # 양식이 바뀌어 못 읽으면 빈 표 + 사유
            _CACHE[key] = pd.DataFrame({"_error": [f"{os.path.basename(path)}: {e}"]})
    return _CACHE[key]


def _newest(patterns, base=None):
    base = base or root()
    fs = []
    for p in patterns:
        fs += [f for f in glob.glob(os.path.join(base, p)) if not os.path.basename(f).startswith("~$")]
    fs = [f for f in fs if "복사본" not in os.path.basename(f)]
    return max(fs, key=os.path.getmtime) if fs else None


def _num(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    if isinstance(v, (int, float, np.integer, np.floating)):
        return float(v)
    s = str(v).replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def _txt(v):
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()


# ───────────────────────── 월별 매출·물량 ─────────────────────────
def _parse_monthly(path):
    rows = []
    x = pd.ExcelFile(path)
    for sh in x.sheet_names:
        m = re.match(r"\s*(\d{4})년\s*매출", sh)
        if not m:
            continue
        year = int(m.group(1))
        d = x.parse(sh, header=None)
        hdr = next((i for i in range(len(d)) if any(_txt(v) == "물량(kg)" for v in d.iloc[i])), None)
        if hdr is None or hdr == 0:
            continue
        crops = [(j, _txt(v)) for j, v in enumerate(d.iloc[hdr - 1]) if _txt(v) in ("팽이", "새송이", "합계")]
        for k, (c0, crop) in enumerate(crops):
            c1 = crops[k + 1][0] if k + 1 < len(crops) else d.shape[1]
            cols = {_txt(d.iat[hdr, j]): j for j in range(c0, c1) if _txt(d.iat[hdr, j])}
            for i in range(hdr + 1, len(d)):
                lab = next((_txt(v) for v in d.iloc[i, :c0 + 1] if re.match(r"^\d{1,2}월$", _txt(v))), None)
                if not lab:
                    continue
                kg, box, won = (_num(d.iat[i, cols[c]]) if c in cols else np.nan for c in ("물량(kg)", "박스수량", "매출(원)"))
                if all(pd.isna(v) or v == 0 for v in (kg, box, won)):
                    continue
                rows.append({"연": year, "월": int(lab[:-1]), "작물": crop, "물량kg": kg, "박스": box, "매출": won})
    D = pd.DataFrame(rows)
    if len(D):
        D["연월"] = pd.to_datetime(dict(year=D["연"], month=D["월"], day=1))
        D["kg당단가"] = D["매출"] / D["물량kg"].where(D["물량kg"] > 0)
    return D


def monthly_sales():
    p = _newest(["월별 매출*물량*.xlsx", "월별 매출*.xlsx"])
    return (_cached(p, _parse_monthly), p) if p else (pd.DataFrame(), None)


def monthly_extend(M, S):
    """월별 매출,물량.xlsx 에 아직 없는 달을 채운다 (v1.26.4, 잠정).
    물량kg·박스 = 포장실 일지 출고(입출고내역) 합 — 지난달들은 월별 표와 같은 값(2026-01~08 확인).
    매출 = 경리나라 거래명세표(작물별 공급가액, 시장 출하는 명세표가 있는 마지막 날까지)
           + 그 뒤 날짜는 포장실 일지 출고 금액(시장 경락가 × 박스, 단가 미정 박스는 0원).
    반환 [연, 월, 작물, 물량kg, 박스, 매출, 연월, kg당단가, 잠정, 마지막날, 미정박스, 명세표몫, 일지몫]"""
    from . import etl_logs
    if M is None or M.empty or "_error" in M:
        return pd.DataFrame()
    last = M["연월"].max()
    L = etl_logs.load("ledger")
    if L is None or L.empty:
        return pd.DataFrame()
    L = L[L["type"] == "출고"].copy()
    L["날짜"] = pd.to_datetime(L["date"]).dt.normalize()
    L = L[L["날짜"] >= last + pd.offsets.MonthBegin(1)]
    if L.empty:
        return pd.DataFrame()
    for c in ("box", "kg", "amt", "price"):
        L[c] = pd.to_numeric(L[c], errors="coerce")
    L["연월"] = L["날짜"].dt.to_period("M").dt.to_timestamp()
    L["시장"] = L["cust"].astype(str).str.strip() == "시장"
    has_s = S is not None and not S.empty and "_error" not in S
    cut_m = S.loc[S["채널"] == "도매시장", "거래일"].max() if has_s else pd.Timestamp("1900-01-01")
    cut_a = S["거래일"].max() if has_s else pd.Timestamp("1900-01-01")
    if pd.isna(cut_m):
        cut_m = cut_a
    rows = []
    for (ym, crop), g in L.groupby(["연월", "crop"]):
        sp = 0.0
        if has_s:
            q = S[(S["연월"] == ym) & (S["작물"] == crop)]
            q = q[((q["채널"] == "도매시장") & (q["거래일"] <= cut_m)) | ((q["채널"] != "도매시장") & (q["거래일"] <= cut_a))]
            sp = float(q["공급가액"].sum())
        lg = g[(g["시장"] & (g["날짜"] > cut_m)) | (~g["시장"] & (g["날짜"] > cut_a))]
        lp = float(lg["amt"].sum(min_count=1) or 0)
        und = float(g.loc[g["시장"] & (g["price"].fillna(0) <= 0), "box"].sum())
        rows.append({"연": ym.year, "월": ym.month, "작물": crop, "물량kg": g["kg"].sum(), "박스": g["box"].sum(), "매출": sp + lp,
                     "연월": ym, "잠정": True, "마지막날": g["날짜"].max(), "미정박스": und, "명세표몫": sp, "일지몫": lp})
    E = pd.DataFrame(rows)
    if E.empty:
        return E
    T = E.groupby(["연", "월", "연월"]).agg(물량kg=("물량kg", "sum"), 박스=("박스", "sum"), 매출=("매출", "sum"), 마지막날=("마지막날", "max"),
                                          미정박스=("미정박스", "sum"), 명세표몫=("명세표몫", "sum"), 일지몫=("일지몫", "sum")).reset_index()
    T["작물"], T["잠정"] = "합계", True
    E = pd.concat([E, T], ignore_index=True)
    E["kg당단가"] = E["매출"] / E["물량kg"].where(E["물량kg"] > 0)
    return E


def _parse_daeyang(path):
    """'대양청과 단가차액' 시트: 새송이·팽이 블록이 옆으로 나란히, 달마다 '일자' 머리줄이 반복된다."""
    x = pd.ExcelFile(path)
    sh = next((s for s in x.sheet_names if "단가차액" in s), None)
    if not sh:
        return pd.DataFrame()
    d = x.parse(sh, header=None)
    rows = []
    starts = [j for j in range(d.shape[1]) if any(_txt(v) == "일자" for v in d.iloc[:, j])]
    for s in starts:
        title = " ".join(_txt(v) for v in d.iloc[:4, s])
        crop = "팽이" if "팽이" in title else ("새송이" if "새송이" in title else "")
        names = {}
        for i in range(len(d)):
            v = d.iat[i, s]
            if _txt(v) == "일자":
                names = {j: _txt(d.iat[i, j]) for j in range(s + 1, min(s + 6, d.shape[1])) if _txt(d.iat[i, j]) and "차액" not in _txt(d.iat[i, j])}
            elif isinstance(v, (dt.datetime, pd.Timestamp)) and names:
                for j, nm in names.items():
                    p_ = _num(d.iat[i, j])
                    if pd.notna(p_) and p_ > 0:
                        rows.append({"날짜": pd.Timestamp(v).normalize(), "작물": crop, "시장": nm, "박스단가": p_})
    return pd.DataFrame(rows)


def daeyang_prices():
    p = _newest(["월별 매출*.xlsx"])
    return _cached(p, _parse_daeyang) if p else pd.DataFrame()


# ───────────────────────── 사업장별 매입·매출 ─────────────────────────
def _parse_inout(path):
    x = pd.ExcelFile(path)
    sh = "매입매출" if "매입매출" in x.sheet_names else x.sheet_names[0]
    d = x.parse(sh, header=None)
    rows = []
    ent = None
    for i in range(len(d) - 1):
        for j in range(d.shape[1]):
            t = _txt(d.iat[i, j])
            if not t:
                continue
            nxt = " ".join(_txt(v) for v in d.iloc[i + 1])
            if j <= 2 and "매출" in nxt and "매입" in nxt and "매출" not in t and "매입" not in t and not re.search(r"\d", t):
                ent = t
            if ent and ("매출" in t or "매입" in t) and i + 2 < len(d) and _txt(d.iat[i + 1, j]) == "일자":
                kind = re.sub(r"^(본점|지점)\s*", "", t)
                for r in range(i + 2, min(i + 20, len(d))):
                    v = d.iat[r, j]
                    if isinstance(v, (dt.datetime, pd.Timestamp)):
                        tot, sup, tax = (_num(d.iat[r, j + k]) for k in (1, 2, 3))
                        if pd.notna(tot) or pd.notna(sup):
                            rows.append({"사업장": ent, "구분": kind, "연월": pd.Timestamp(v).normalize().replace(day=1),
                                         "합계금액": tot, "공급가액": sup, "세액": tax})
    return pd.DataFrame(rows).drop_duplicates(["사업장", "구분", "연월"]) if rows else pd.DataFrame()


def inout():
    p = _newest(["*년 매입매출.xlsx", "*매입매출*.xlsx"])
    return (_cached(p, _parse_inout), p) if p else (pd.DataFrame(), None)


# ───────────────────────── 시장 출고량·평균단가 ─────────────────────────
def _parse_market_sheet(d, crop):
    hdr = next((i for i in range(len(d)) if any(_txt(v) == "거래처" for v in d.iloc[i])), None)
    if hdr is None or hdr == 0:
        return []
    H = [_txt(v) for v in d.iloc[hdr]]
    c_cust = H.index("거래처")
    c_item = H.index("품목") if "품목" in H else None
    c_kind = H.index("구분") if "구분" in H else None
    yrow = [_txt(v) for v in d.iloc[hdr - 1]]
    ycol, y = {}, None
    for j in range(d.shape[1]):
        m = re.match(r"^(\d{4})년$", yrow[j])
        if m:
            y = int(m.group(1))
        if re.match(r"^\d{1,2}월$", H[j]) and y:
            ycol[j] = (y, int(H[j][:-1]))
    rows, cust, item = [], None, None
    for i in range(hdr + 1, len(d)):
        c = _txt(d.iat[i, c_cust])
        if c:
            cust = c; item = None
        if c_item is not None and _txt(d.iat[i, c_item]):
            item = _txt(d.iat[i, c_item])
        kind = _txt(d.iat[i, c_kind]) if c_kind is not None else ""
        if not cust or kind not in ("출고량", "평균단가"):
            continue
        for j, (yy, mm) in ycol.items():
            v = _num(d.iat[i, j])
            if pd.notna(v) and v != 0:
                rows.append({"작물": crop, "거래처": cust, "품목": item or crop, "연월": pd.Timestamp(yy, mm, 1), "항목": kind, "값": v})
    return rows


def _parse_market(path):
    crop = "새송이" if "새송이" in os.path.basename(path) else "팽이"
    x = pd.ExcelFile(path)
    rows = []
    for sh in x.sheet_names:                    # 앞 시트(단가&출고량)가 우선, '누적'은 그 앞 기간과 소매 거래처를 채운다
        if sh.startswith("단가") or sh.startswith("누적"):
            rows += [dict(r, _sheet=sh) for r in _parse_market_sheet(x.parse(sh, header=None), crop)]
    if not rows:
        return pd.DataFrame()
    D = pd.DataFrame(rows)
    D["_p"] = np.where(D["_sheet"].str.startswith("단가"), 0, 1)
    D = D.sort_values("_p").drop_duplicates(["작물", "거래처", "품목", "연월", "항목"]).drop(columns=["_p", "_sheet"])
    W = D.pivot_table(index=["작물", "거래처", "품목", "연월"], columns="항목", values="값", aggfunc="first").reset_index()
    W.columns.name = None
    return W.rename(columns={"출고량": "출고량kg", "평균단가": "평균단가"})


def market_prices():
    out, paths = [], []
    for crop in ("팽이", "새송이"):
        p = _newest([f"*{crop}시장단가*.xlsx", os.path.join("**", f"*{crop}시장단가*.xlsx")])
        if p:
            out.append(_cached(p, _parse_market)); paths.append(p)
    out = [o for o in out if len(o) and "_error" not in o]
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame()), paths


# ───────────────────────── 매출 거래명세표 (경리나라) ─────────────────────────
ONLINE = ("11번가", "쿠팡", "G마켓", "지마켓", "옥션", "네이버", "스마트스토어", "토스", "카카오")
MARKET_HINT = ("청과", "공판장", "농산물", "경인농산", "도매")    # 도매시장(경매) 거래처 — 아이쿱농산 같은 납품처는 직거래


def clean_name(s):
    s = _txt(s)
    for t in ("농업회사법인", "주식회사", "(주)", "㈜", "(유)", "협동조합"):
        s = s.replace(t, " ")
    s = re.sub(r"\s+주$", "", s.strip())
    return re.sub(r"\s+", " ", s).strip() or _txt(s)


def _channel(cust, item):
    if any(k.lower() in cust.lower() for k in ONLINE):
        return "온라인"
    if any(k in cust for k in MARKET_HINT):
        return "도매시장"
    return "직거래·납품"


def _parse_statement(path):
    T = pd.read_excel(path, header=1)
    if "거래일" not in T or "공급가액" not in T:
        return pd.DataFrame()
    T["거래일"] = pd.to_datetime(T["거래일"], errors="coerce")
    T = T[T["거래일"].notna()].copy()
    site = T.get("프로젝트/현장", pd.Series("", index=T.index)).fillna("").astype(str)
    item = T.get("품목/규격", pd.Series("", index=T.index)).fillna("").astype(str)
    crop = np.where(site.str.contains("팽이"), "팽이", np.where(site.str.contains("새송이"), "새송이",
                    np.where(item.str.contains("팽이"), "팽이", np.where(item.str.contains("새송이"), "새송이", "기타"))))
    cust = T["공급받는자상호"].fillna("").astype(str)
    out = pd.DataFrame({"거래일": T["거래일"].dt.normalize(), "거래처원본": cust, "거래처": cust.map(clean_name),
                        "품목": item, "작물": crop, "공급가액": pd.to_numeric(T["공급가액"], errors="coerce"),
                        "합계금액": pd.to_numeric(T.get("합계금액"), errors="coerce")})
    out["채널"] = [_channel(c, i) for c, i in zip(out["거래처"], out["품목"])]
    out["파일"] = os.path.basename(path)
    # 파일이 맡은 달: 'M월 거래명세표' 의 M (연도는 그 파일 마지막 거래일의 연도). 앞달 말일 시장 출하분도 들어 있다
    m = re.match(r"^(\d{1,2})월", os.path.basename(path))
    if m and len(out):
        last = out["거래일"].max()
        out["파일월"] = pd.Timestamp(last.year - (1 if int(m.group(1)) > last.month else 0), int(m.group(1)), 1)
    return out


def statement_files():
    base = os.path.join(root(), "거래내역 카톡")
    fs = [f for f in glob.glob(os.path.join(base, "*거래명세표*.xls*")) + glob.glob(os.path.join(base, "*", "*거래명세표*.xls*"))
          if not os.path.basename(f).startswith("~$")]
    # 같은 폴더·같은 달 파일이 둘이면(다시 받은 경우) 최근 것만
    keep = {}
    for f in fs:
        m = re.match(r"^(\d{1,2})월", os.path.basename(f))
        key = (os.path.dirname(f), m.group(1) if m else os.path.basename(f))
        if key not in keep or os.path.getmtime(f) > os.path.getmtime(keep[key]):
            keep[key] = f
    return sorted(keep.values())


def statements():
    fs = statement_files()
    if any(f.lower().endswith(".xls") for f in fs):
        from . import etl_logs
        if etl_logs._xlrd() is None:                 # 옛 엑셀(.xls)은 xlrd 가 필요 (없으면 하루 한 번 설치 시도)
            return pd.DataFrame({"_error": ["거래명세표(.xls)를 읽으려면 xlrd 패키지가 필요합니다 — 명령창에서 pip install xlrd"]}), fs
    parts = [_cached(f, _parse_statement) for f in fs]
    parts = [p for p in parts if len(p) and "_error" not in p]
    if not parts:
        return pd.DataFrame(), fs
    D = pd.concat(parts, ignore_index=True)
    D["연월"] = D["거래일"].dt.to_period("M").dt.to_timestamp()
    if "파일월" in D:                              # 파일이 없는 달의 조각(예: 5월 파일 첫날의 4/30 출하분)은 뺀다
        D = D[D["연월"].isin(set(D["파일월"].dropna()))]
    return D, fs


# ───────────────────────── 전기·수도 ─────────────────────────
def _parse_util_sheet(d, kind):
    hdr = next((i for i in range(len(d)) if any(re.match(r"^\d{1,2}월고지$", _txt(v)) for v in d.iloc[i])), None)
    if hdr is None:
        return []
    H = [_txt(v) for v in d.iloc[hdr]]
    name_col = H.index("실제 사용장소") if "실제 사용장소" in H else (H.index("성명") if "성명" in H else None)
    ent_col = H.index("성명") if "성명" in H and kind == "전기" else None
    ycol, y = {}, None
    for j in range(d.shape[1]):
        yv = _txt(d.iat[hdr - 1, j]) if hdr > 0 else ""
        m = re.match(r"^'?(\d{2})(\.0)?$", yv)
        if m:
            y = 2000 + int(m.group(1))
        m2 = re.match(r"^(\d{1,2})월고지$", H[j])
        if m2 and y:
            ycol[j] = pd.Timestamp(y, int(m2.group(1)), 1)
    lab_col = next((j for j in range(d.shape[1]) if any(_txt(v) in ("사용량", "금액") for v in d.iloc[hdr + 1:, j])), None)
    if lab_col is None or name_col is None:
        return []
    rows, seen = [], {}
    for i in range(hdr + 1, len(d) - 1):
        if _txt(d.iat[i, lab_col]) != "사용량" or _txt(d.iat[i + 1, lab_col]) != "금액":
            continue
        place = _txt(d.iat[i, name_col])
        ent = _txt(d.iat[i, ent_col]) if ent_col is not None else ""
        seen[place] = seen.get(place, 0) + 1
        if seen[place] > 1:                          # 같은 이름의 계량기가 둘 이상이면 (2), (3)…
            place = f"{place} ({seen[place]})"
        for j, ym in ycol.items():
            u, w = _num(d.iat[i, j]), _num(d.iat[i + 1, j])
            if pd.notna(u) or pd.notna(w):
                rows.append({"종류": kind, "장소": place, "사업장": ent, "고지월": ym, "사용량": u, "금액": w})
    return rows


def _parse_util(path):
    x = pd.ExcelFile(path)
    rows = []
    for sh in ("전기", "수도"):
        if sh in x.sheet_names:
            rows += _parse_util_sheet(x.parse(sh, header=None), sh)
    return pd.DataFrame(rows)


def utilities():
    p = _newest(["*수도전기*.xlsx", "*전기*사용내역*.xlsx"])
    return (_cached(p, _parse_util), p) if p else (pd.DataFrame(), None)


# ───────────────────────── 자료 위치 (정리 목록) ─────────────────────────
CATALOG = [
    # (분류, 위치(원본 폴더 기준), 내용, 앱 반영)
    ("매출", r"월별 매출,물량.xlsx", "연도별 팽이·새송이 월 물량(kg)·박스·매출(경리나라 기준), 대양청과 단가 비교", "매출 현황 · 시장 단가"),
    ("매출", r"거래내역 카톡\M월 거래명세표_*.xls (2025 폴더 포함)", "경리나라 매출 거래명세표 — 거래일·거래처·품목·공급가액·재배사", "거래처별"),
    ("매출", r"거래내역 카톡\M월 남선지티엘·음성살림·행복한농부들.xls", "직거래처 월 거래내역(위 거래명세표에 포함)", "—"),
    ("매출", r"(포장실) Z:\안석구\…\팽이일지\YYYY년\YYYY년 M월 팽이 생산 상세일지.xlsx · 새송이 상세일지.xlsx '입출고내역'", "매일 도매시장별 출하 박스·kg·경락 단가·금액 (2025-01~, 매일 갱신)", "시장 단가 (일별)"),
    ("매출", r"M월 팽이시장단가.xlsx · M월 새송이시장단가.xlsx", "도매시장 거래처별 월 출고량·평균단가 (2024-06~, 경리가 월별로 정리)", "시장 단가 (월별 정리표)"),
    ("매출", r"최\시장\YY.MM\연우)·진성)○○청과_M월.xls", "도매시장 정산서 원본(시장·월별)", "— (시장단가 파일로 요약됨)"),
    ("매출", r"온라인정산\쿠팡·11번가·G마켓·옥션·토스·네이버_N분기", "온라인몰 분기별 정산(부가세 신고용)", "— (거래명세표의 온라인 거래처로 대신)"),
    ("매입·매출", r"2026년 매입매출.xlsx", "연우본점·연우지점·진성 월 매출·매입(과세·면세) 세금계산서 합계", "매입·매출"),
    ("매입·매출", r"최\허부장님\연도별 매입매출·매출처별 합계표", "2023~2025년 연도 자료(매출처별 전자계산서 합계표 등)", "—"),
    ("매입·매출", r"에코팜 경리나라\*.xls", "에코팜 경리나라 내보내기(2025-05 한 번): 매출·매입 거래명세표, 세금계산서, 미수금", "—"),
    ("비용", r"2026)수도전기 사용내역.xlsx", "전기(사용 장소별 kWh·금액, '25.8~)·수도 월 고지", "전기·수도"),
    ("비용", r"최\2025)한전온 전기요금.xlsx", "2025년 한전ON 청구 내역(고객번호별)", "—"),
    ("비용", "면세유 폴더", "면세유 신청·사용 자료", "—"),
    ("운영", r"최\부자재 관리대장.xlsx", "필름·박스·테이프 입고·사용·잔량 (팽이·새송이)", "—"),
    ("운영", r"26년 M월 ○○팀 근무 시간표.xlsx · 최\부서별 근무현황표 폴더", "부서별 근무 시간표", "—"),
    ("운영", r"최\연우)·진성)수출실적증명 엑셀_YYYY년.xlsx", "연·월 수출 건수·중량·금액", "—"),
    ("손익", r"최\외국인관리\★손익\YYYY-MM 폴더", "2020-06~2023-07 월 손익 자료(사업장별·매출분리) — 폴더 이름과 달리 손익 자료", "—"),
]

# ───────────────────────── 일별 시장 출하·단가 (포장실 입출고내역) ─────────────────────────
MARKET_ALIAS = {"대전중앙": "대전중앙청과", "광주중앙": "광주중앙청과"}   # 포장실 장부의 줄임 이름 → 시장단가 표와 같은 이름


def market_daily():
    """포장실 '생산 상세일지'(팽이) · '새송이 상세일지'의 '입출고내역' 시트 중 거래처 '시장' 출고 행 = 매일 도매시장 출하.
    etl_logs 의 'ledger' 자료(매일 09:00·15:00 갱신)를 그대로 쓴다. 단가는 박스당 원(경락 후 입력 — 비어 있으면 아직 정산 전).
    반환 [날짜, 작물, 시장, 품목, 등급, 규격, 박스당kg, 박스, kg, 박스단가, 금액, 비고]"""
    from . import etl_logs
    L = etl_logs.load("ledger")
    if L is None or L.empty:
        return pd.DataFrame()
    M = L[(L["type"] == "출고") & (L["cust"].astype(str).str.strip() == "시장")].copy()
    if M.empty:
        return pd.DataFrame()
    sub = M["sub"].fillna("").astype(str).str.strip()
    name = M["name"].fillna("").astype(str).str.strip()
    spec = M["spec"].fillna("").astype(str).str.strip()
    grade = M["grade"].fillna("").astype(str).str.strip()
    item = np.where(M["crop"] == "새송이", np.where(name != "", name, spec), spec + np.where(grade != "", " " + grade, ""))
    out = pd.DataFrame({"날짜": pd.to_datetime(M["date"]).dt.normalize(), "작물": M["crop"], "시장": sub.map(lambda x: MARKET_ALIAS.get(x, x)),
                        "품목": item, "등급": grade, "규격": spec, "박스당kg": pd.to_numeric(M["unit"], errors="coerce"),
                        "박스": pd.to_numeric(M["box"], errors="coerce"), "kg": pd.to_numeric(M["kg"], errors="coerce"),
                        "박스단가": pd.to_numeric(M["price"], errors="coerce"), "금액": pd.to_numeric(M["amt"], errors="coerce"),
                        "비고": M["note"].fillna("").astype(str)})
    out.loc[out["박스단가"] <= 0, "박스단가"] = np.nan        # 0원 = 아직 경락가 안 들어온 것
    out.loc[out["박스단가"].isna(), "금액"] = np.nan
    return out[out["시장"] != ""].reset_index(drop=True)


# ───────────────────────── 출하 대사: 포장실 시장 출하 ↔ 경리나라 매출 거래명세표 ─────────────────────────
STMT_MARKET = {"농협경제지주대전공판장": "대전농협", "대전공판장": "대전농협"}   # 거래명세표 거래처 → 포장실 장부 시장 이름


def fax_to_ship_date(FX, A):
    """팩스의 날짜는 경매(판매)일, 장부는 출하일 — 경매는 출하 다음 날(토요일은 출하 없음 → 일요일 출하분은 월요일 경매).
    판매일 하루 전이 출하일. 다만 그날이 농장 전체에 출하가 없던 날(공휴일 등)이면 그 시장의 바로 앞 출하일(3일 안)에 붙인다.
    원래 판매일은 '판매일' 열에 남긴다 (v1.26.1)"""
    F = FX.copy()
    F["판매일"] = F["날짜"]
    k = F["날짜"] - pd.Timedelta(days=1)
    days = set(A["날짜"])
    last = A["날짜"].max()
    L = A[["작물", "시장", "날짜"]].drop_duplicates()
    ship = []
    for (crop, mk), kk in zip(zip(F["작물"], F["시장"]), k):
        if kk in days or kk > last:
            ship.append(kk); continue
        prev = L[(L["작물"] == crop) & (L["시장"] == mk) & (L["날짜"] < kk) & (L["날짜"] >= kk - pd.Timedelta(days=3))]["날짜"]
        ship.append(prev.max() if len(prev) else kk)
    F["날짜"] = ship
    num = [c for c in F.columns if c.startswith("팩스") and c not in ("팩스검산", "팩스파일")]
    if F.duplicated(["작물", "시장", "날짜"]).any():                    # 두 판매일이 한 출하일로 몰리면(드묾) 합친다
        agg = {c: (lambda x: x.sum(min_count=1)) for c in num}
        agg.update({c: (lambda x: "; ".join(map(str, x))) for c in F.columns if c not in num + ["작물", "시장", "날짜", "판매일", "가정산"]})
        agg.update({"판매일": "max", "가정산": "any"})
        F = F.groupby(["작물", "시장", "날짜"]).agg(agg).reset_index()
    return F


def reconcile(MD, S, tol=1000, FX=None):
    """(작물, 시장, 날짜)마다 포장실 장부 금액(경락가 × 박스)과 경리나라 매출 거래명세표 공급가액을 맞춘다 (v1.25.2).
    거래명세표에는 박스 수가 없어 금액으로 대조한다 — 금액이 같으면 수량×단가가 같다는 뜻.
    시장 출하는 출하일 날짜로 명세표에 적히고, 말일 출하분은 다음 달 명세표 파일에 들어간다(그래서 날짜 단위로 맞춘다).
    반환 일별 표 [작물, 시장, 날짜, 장부박스, 장부금액, 미정박스, 명세표, 차이, 구분]"""
    if MD is None or MD.empty or S is None or S.empty or "_error" in S:
        return pd.DataFrame()
    A = MD.assign(_미정=MD["박스"].where(MD["박스단가"].isna(), 0)).groupby(["작물", "시장", "날짜"]).agg(
        장부박스=("박스", "sum"), 장부금액=("금액", "sum"), 미정박스=("_미정", "sum")).reset_index()
    B = S[S["채널"] == "도매시장"].assign(시장=lambda d: d["거래처"].map(lambda x: STMT_MARKET.get(x, x)))
    B = B.groupby(["작물", "시장", "거래일"])["공급가액"].sum().reset_index().rename(columns={"거래일": "날짜", "공급가액": "명세표"})
    J = A.merge(B, on=["작물", "시장", "날짜"], how="outer")
    if FX is not None and len(FX):                                   # 팩스만 있는 날(장부·명세표에 없음)도 잡히게
        FX = fax_to_ship_date(FX, A)
        J = J.merge(FX, on=["작물", "시장", "날짜"], how="outer")
    stmt_last = B["날짜"].max()
    J = J[(J["날짜"] >= max(A["날짜"].min(), B["날짜"].min())) & (J["날짜"] <= A["날짜"].max())]   # 장부·명세표가 겹치는 기간, 장부 마지막 날까지
    for c in ("장부박스", "장부금액", "미정박스", "명세표"):
        J[c] = J[c].fillna(0)
    J["차이"] = J["명세표"] - J["장부금액"]
    J["구분"] = np.select(
        [J["날짜"] > stmt_last, (J["명세표"] == 0) & (J["장부박스"] > 0), (J["장부박스"] == 0) & (J["명세표"] != 0),
         (J["미정박스"] > 0) & (J["차이"].abs() > tol), J["차이"].abs() > tol],
        ["명세표 아직 없음", "명세표에 없음", "장부에 없음", "단가 미정", "금액 다름"], "일치")
    # 팩스 판매원표(시장이 보낸 경락 결과) — 박스 수까지 맞춘다 (v1.26.0)
    if FX is not None and len(FX):
        # 팩스를 보내는 시장만 '팩스 없음'을 따진다 (그 시장 팩스가 있는 날짜 범위 안에서)
        span = FX.groupby(["작물", "시장"])["날짜"].agg(["min", "max"]).reset_index().rename(columns={"min": "_f0", "max": "_f1"})
        J = J.reset_index(drop=True).merge(span, on=["작물", "시장"], how="left")
        inrange = J["_f0"].notna() & (J["날짜"] >= J["_f0"]) & (J["날짜"] <= J["_f1"])
        has = J["팩스박스"].notna()
        qty_ok = (J["팩스박스"] - J["장부박스"]).abs() < 0.5
        amt_ok = (J["팩스금액"] - J["장부금액"]).abs() <= tol
        J["팩스대조"] = np.select(
            [~inrange & ~has, ~has & (J["장부박스"] > 0), has & (J["장부박스"] == 0), has & (J["미정박스"] > 0),
             has & amt_ok & ~qty_ok, has & qty_ok & ~amt_ok, has & ~qty_ok & ~amt_ok],
            ["", "팩스 없음", "장부에 없음", "장부 단가 미정", "일치(박스 단위 다름)", "금액 다름", "수량·금액 다름"], "일치")
        J["팩스차액"] = J["팩스금액"] - J["장부금액"]
        J = J.drop(columns=["_f0", "_f1"])
    return J.sort_values(["날짜", "작물", "시장"]).reset_index(drop=True)

