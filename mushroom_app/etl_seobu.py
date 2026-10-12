# -*- coding: utf-8 -*-
"""서부청과 출하주 판매내역 엑셀 (v1.27.2)

서부청과는 하루 출하분이 그날 경매에서 다 안 팔리고 며칠에 걸쳐 팔리는 일이 많아, 경리(최 과장)가 서부청과 홈페이지의
'출하주 판매내역'(경매일별 줄: 생산자·품종·단량·단위·등급·수량·단가·판매액)을 달마다 엑셀에 붙여 넣고 오른쪽에
출하일·출고 박스·어느 경매일에 몇 박스가 얼마에 팔렸는지 손으로 맞춰 둔다.
  파일: SynologyDrive 루트  진성)서부청과_1월 - 복사본.xls · 연우)서부청과_1월 - 복사본.xls  (시트 '8월'·'9월'·'1월'·'출하주 판매내역')
여기서는 왼쪽(홈페이지 줄)만 읽어 팩스 판매원표와 같은 모양(etl_fax.tables 의 docs/lines)으로 만든다.
팩스는 하루치 판매원표가 빠지거나 일부만 오지만 이 표는 경매일별로 빠짐없이 있어, 서부청과는 이 표를 경매 자료로 쓴다.
"""
import os, re, glob, json
import datetime as dt
import numpy as np
import pandas as pd
from . import config as C

MARKET = "서부청과"
SHIPPER_CROP = {"진성": "팽이", "연우": "새송이", "피어리스": "새송이"}


def files():
    try:
        base = C.log_dir("office")
    except Exception:
        return []
    fs = glob.glob(os.path.join(base, "*서부청과*.xls*")) + glob.glob(os.path.join(base, "최", "시장", "*서부청과*복사본*.xls*"))
    return sorted({f for f in fs if not os.path.basename(f).startswith("~$")}, key=os.path.getmtime, reverse=True)


def _shipper(path):
    b = os.path.basename(path)
    for k in ("진성", "연우", "피어리스"):
        if k in b:
            return k
    return "?"


def _parse(path):
    """→ (docs, lines) : 한 문서 = (출하자, 경매일)"""
    shipper = _shipper(path)
    crop = SHIPPER_CROP.get(shipper, "?")
    x = pd.ExcelFile(path)
    D, L = [], []
    seen = set()
    for sh in x.sheet_names:
        if not (re.match(r"^\d{1,2}월$", sh) or "판매내역" in sh):
            continue
        d = x.parse(sh, header=None)
        if d.shape[1] < 10:
            continue
        day, cur = None, []

        def flush():
            if day is None or not cur:
                return
            key = (shipper, day)
            if key in seen:                       # 같은 날이 두 시트에(월 시트와 누적 시트) 있으면 먼저 읽은 것만
                return
            seen.add(key)
            q = sum(c["수량"] for c in cur); a = sum(c["금액"] for c in cur)
            fname = f"{os.path.basename(path)}#{sh}"
            sig = json.dumps(["판매원표", [[c["수량"], c["단가"], c["금액"]] for c in cur], q, a, None, None, None, None], ensure_ascii=False)
            D.append({"파일": fname, "장": len(D), "날짜": day, "시장": MARKET, "출하자": shipper, "문서": "판매원표", "표기합계수량": float(q), "표기합계금액": float(a),
                      "검산": "맞음", "지급검산": "", "비고": "서부청과 출하주 판매내역(엑셀)", "읽은곳": "서부청과 엑셀", "서명": sig, "작물": crop,
                      "줄수": len(cur), "수량": float(q), "금액": float(a), "수수료": np.nan, "운임": np.nan, "기타공제": np.nan, "지급액": np.nan})
            for c in cur:
                L.append({"파일": fname, "장": len(D) - 1, "날짜": day, "시장": MARKET, "작물": crop, "출하자": shipper, "문서": "판매원표",
                          "등급": c["등급"], "박스당kg": c["단량"], "수량": float(c["수량"]), "단가": float(c["단가"]), "금액": float(c["금액"]), "줄검산": True})

        for i in range(len(d)):
            v0 = d.iat[i, 0]
            if (isinstance(v0, (pd.Timestamp, dt.datetime, dt.date)) and pd.notna(v0)) or (isinstance(v0, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v0)):
                flush(); cur = []
                day = pd.Timestamp(v0).normalize()
            q, p, a = d.iat[i, 7], d.iat[i, 8], d.iat[i, 9]
            try:
                q, p, a = float(q), float(p), float(a)
            except (TypeError, ValueError):
                continue
            if day is None or not (q > 0) or pd.isna(a):
                continue
            unit = f"{d.iat[i, 3]}{d.iat[i, 4]}".replace("nan", "").strip()
            grade = " ".join(str(d.iat[i, k]) for k in (2, 5, 6) if pd.notna(d.iat[i, k]) and str(d.iat[i, k]).strip())
            try:
                kg = float(d.iat[i, 3])
            except (TypeError, ValueError):
                kg = np.nan
            cur.append({"수량": q, "단가": p, "금액": a, "등급": f"{grade} {unit}".strip(), "단량": kg})
        flush()
    return pd.DataFrame(D), pd.DataFrame(L)


def load():
    """모든 서부청과 엑셀 → (docs, lines, 파일 목록). 없으면 빈 표."""
    Ds, Ls, used = [], [], []
    for f in files():
        try:
            D, L = _parse(f)
        except Exception:
            continue
        if len(D):
            Ds.append(D); Ls.append(L); used.append(f)
    if not Ds:
        return pd.DataFrame(), pd.DataFrame(), []
    D = pd.concat(Ds, ignore_index=True)
    L = pd.concat(Ls, ignore_index=True)
    return D, L, used


def merge_into_fax(FXD, FXL):
    """팩스 표에 서부청과 엑셀을 합친다: 엑셀이 있는 (출하자, 경매일 범위)의 서부청과 팩스 '판매원표'는 빼고(정산서는 둔다) 엑셀 줄을 넣는다."""
    SD, SL, used = load()
    if SD.empty:
        return FXD, FXL, []
    FXD = FXD.copy() if FXD is not None else pd.DataFrame()
    FXL = FXL.copy() if FXL is not None else pd.DataFrame()
    drop_files = set()
    if len(FXD):
        for sh, g in SD.groupby("출하자"):
            lo, hi = g["날짜"].min(), g["날짜"].max()
            m = (FXD["시장"] == MARKET) & (FXD["문서"] == "판매원표") & (FXD["출하자"] == sh) & (FXD["날짜"] >= lo) & (FXD["날짜"] <= hi)
            drop_files |= set(FXD.loc[m, "파일"] + "#" + FXD.loc[m, "장"].astype(str))
        kd = FXD["파일"] + "#" + FXD["장"].astype(str)
        FXD = FXD[~kd.isin(drop_files)]
        if len(FXL):
            kl = FXL["파일"] + "#" + FXL["장"].astype(str)
            FXL = FXL[~kl.isin(drop_files)]
    FXD = pd.concat([FXD, SD], ignore_index=True)
    FXL = pd.concat([FXL, SL], ignore_index=True)
    return FXD, FXL, used
