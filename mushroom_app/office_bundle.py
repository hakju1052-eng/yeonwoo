# -*- coding: utf-8 -*-
"""경영 자료 묶음 — 경리 자료·팩스·입금·3단 대조를 한 번에 계산해 디스크에 저장 (v1.27.1)

계산(etl_match.match·unified·fill_by_ledger 등)에 20초 넘게 걸려 화면에서 매번 하면 앱이 느려진다.
→ 원본(경리 엑셀·app_cache parquet·팩스 index.json·가계부 입금)의 수정시각으로 서명을 만들고, 서명이 같으면
  app_cache\\office_bundle.pkl 을 그대로 읽는다(1초 안). 갱신(etl_cli 09:00·15:00)과 팩스 판독 뒤에 미리 계산해 두면
  화면에서는 기다릴 일이 없다. 서명이 다르면(자료가 바뀌면) 그때만 다시 계산한다.
"""
import os, glob, time, pickle
import pandas as pd
from . import config as C
from . import etl_office as O

PKL = os.path.join(C.CACHE_DIR, "office_bundle.pkl")


def _mt(p):
    try:
        return int(os.path.getmtime(p))
    except OSError:
        return 0


def signature():
    """자료 원본들의 (이름, 수정시각) — 하나라도 바뀌면 다시 계산"""
    sig = [("version", getattr(C, "VERSION", ""))]            # 앱이 올라가면(계산 규칙이 바뀌었을 수 있음) 다시 계산
    for f in glob.glob(os.path.join(C.CACHE_DIR, "*.parquet")):
        sig.append((os.path.basename(f), _mt(f)))
    sig.append(("fax/index.json", _mt(os.path.join(C.CACHE_DIR, "fax", "index.json"))))
    sig.append(("market_deposits.src", _mt(os.path.join(C.CACHE_DIR, "market_deposits.parquet.src"))))
    try:
        base = O.root()
    except Exception:
        base = None
    if base and os.path.isdir(base):
        fs = glob.glob(os.path.join(base, "*.xls*")) + glob.glob(os.path.join(base, "*", "*.xls*"))
        for f in fs:
            if not os.path.basename(f).startswith("~$"):
                sig.append((os.path.relpath(f, base), _mt(f)))
    return tuple(sorted(sig))


def compute():
    """page_office._load 의 본체 — 스트림릿 없이도 돈다."""
    t0 = time.time()
    M, pm = O.monthly_sales()
    S, fs = O.statements()
    I, pi = O.inout()
    MP, pp = O.market_prices()
    U, pu = O.utilities()
    DY = O.daeyang_prices()
    try:
        MD = O.market_daily()
    except Exception:
        MD = pd.DataFrame()
    try:                                     # 팩스 판매원표 (v1.26.0)
        from . import etl_fax
        FXD, FXL = etl_fax.load()
        FXS = etl_fax.status()
    except Exception:
        FXD, FXL, FXS = pd.DataFrame(), pd.DataFrame(), {}
    SBF = []
    try:                                     # 서부청과는 경리의 '출하주 판매내역' 엑셀을 경매 자료로 (v1.27.2)
        from . import etl_seobu
        FXD, FXL, SBF = etl_seobu.merge_into_fax(FXD, FXL)
    except Exception:
        pass
    try:
        FX = etl_fax.daily(FXD)
    except Exception:
        FX = pd.DataFrame()
    try:                                     # 3단 대조: 일지 출고 → 경매 → 정산 (v1.26.2)
        from . import etl_match
        TW = etl_match.match(MD, FXD, FXL) if len(FXD) and "서명" in FXD else {}
        ST = etl_match.settle(FXD)
    except Exception as e:
        TW, ST = {"_error": str(e)}, pd.DataFrame()
    try:                                     # 통장 입금 (가계부 백업) → 정산 지급액 (v1.26.2)
        from . import etl_bank
        DEP, DEPSRC = etl_bank.load()
        ST, DEPM = etl_match.deposits(ST, DEP)
        EST, DEPM = etl_match.deposits_by_auction(FXD, ST, DEPM)        # 정산서 없는 날은 경락액으로 추정 (v1.26.6)
        if len(EST):
            ST = pd.concat([ST, EST], ignore_index=True).sort_values("날짜").reset_index(drop=True)
    except Exception as e:
        DEP, DEPSRC, DEPM = pd.DataFrame(), f"오류: {e}", pd.DataFrame()
    try:
        ME = O.monthly_extend(M, S)
    except Exception:
        ME = pd.DataFrame()
    try:                                     # 출하일 한 줄 요약 (v1.26.5)
        J0 = O.reconcile(MD, S, FX=FX if len(FX) else None)
        UN = etl_match.unified(J0, TW, ST, MD)
        UN, DEPM = etl_match.fill_by_ledger(UN, DEPM)            # 팩스 없는 날은 출고장 금액으로 입금 추정 (v1.26.10)
    except Exception:
        UN = pd.DataFrame()
    used = [p for p in [pm, pi, pu] + list(pp) + list(fs) if p]
    return dict(M=M, ME=ME, UN=UN, S=S, I=I, MP=MP, U=U, DY=DY, MD=MD, FX=FX, FXD=FXD, FXS=FXS, TW=TW, ST=ST, DEPM=DEPM, DEPSRC=DEPSRC, used=used, SBF=SBF,
                made=time.strftime("%Y-%m-%d %H:%M:%S"), secs=round(time.time() - t0, 1))


def get(force=False, progress=None):
    """서명이 같으면 저장본, 아니면 새로 계산해 저장. 반환 dict (+ 'cached' True/False)"""
    sig = signature()
    if not force and os.path.exists(PKL):
        try:
            with open(PKL, "rb") as fh:
                saved = pickle.load(fh)
            if saved.get("sig") == sig and isinstance(saved.get("data"), dict):
                d = saved["data"]
                d["cached"] = True
                return d
        except Exception:
            pass
    if progress:
        progress("경영 자료 다시 계산 중…")
    d = compute()
    d["cached"] = False
    try:
        os.makedirs(C.CACHE_DIR, exist_ok=True)
        tmp = PKL + ".tmp"
        with open(tmp, "wb") as fh:
            pickle.dump({"sig": sig, "data": d}, fh, protocol=4)
        os.replace(tmp, PKL)
    except Exception:
        pass
    return d
