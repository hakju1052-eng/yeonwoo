# -*- coding: utf-8 -*-
"""도매시장 입금 — 가계부 앱(Money Manager, realbyte) 백업 파일에서 시장이 보낸 입금만 뽑는다 (v1.26.2)

백업 파일(MMGF(…).mmbak · MMAuto[…](…).mmbak)은 SQLite. 문자(SMS)로 들어온 입출금이 INOUTCOME 표에 있다.
여기서 '받는 쪽 표시 이름'이 도매시장인 입금(DO_TYPE 0)만 남기고, 개인 거래·잔액·계좌 정보는 읽지도 저장하지도 않는다.
결과 app_cache\\market_deposits.parquet [일시, 시장, 구분(''/장려금/보전/송금), 금액, 꼬리(이름 끝 숫자 — 동화청과18 → 18일 판매분)]
"""
import os, re, glob, sqlite3, shutil, tempfile
import pandas as pd
from . import config as C

# 통장에 찍히는 보낸 사람 이름 → 시장 (2026-10 백업에서 확인: 서청=서울청과, 한국=한국청과, 대전중=대전중앙청과, 광중청물대=광주중앙청과, NN대전공판장=대전농협)
RULES = [("서울청과", r"^서청|서울청과"), ("동화청과", r"^동화청과"), ("한국청과", r"^한국\d*$|^한국청과"), ("대전청과", r"^대전청과"),
         ("대전중앙청과", r"^대전중"), ("서부청과", r"^서부청과"), ("구리청과", r"^구리청과"), ("경인농산", r"^경인농산"),
         ("인천농산물", r"^인천농"), ("광주중앙청과", r"^광중"), ("대양청과", r"^대양"), ("대전농협", r"대전공판장")]
SINCE = "2025-01-01"


def sources():
    home = os.path.expanduser("~")
    dirs = getattr(C, "DEPOSIT_DIRS", None) or [os.path.join(home, "Downloads"), os.path.join(home, "Downloads", "가계부"),
                                                  r"G:\내 드라이브\MoneyManager", os.path.join(home, "Google Drive", "MoneyManager")]
    files = []
    for d in dirs:
        files += glob.glob(os.path.join(d, "MMGF*.mmbak")) + glob.glob(os.path.join(d, "MMAuto*.mmbak"))
    return sorted(files, key=lambda f: os.path.getmtime(f), reverse=True)


def _market(name):
    s = str(name or "").strip()
    for m, p in RULES:
        if re.search(p, s):
            return m
    return None


def _kind(name):
    s = str(name or "")
    return "장려금" if "장려" in s else ("보전" if "보전" in s else ("송금" if "송금" in s else ""))


def _tail(name):
    s = str(name or "").strip()
    m = re.search(r"(\d{1,2})$", s) or re.match(r"^(\d{1,2})", s)
    return int(m.group(1)) if m else None


def _out():
    return os.path.join(C.CACHE_DIR, "market_deposits.parquet")


def build(path=None):
    """가장 최근 백업에서 시장 입금만 뽑아 저장. 반환 (표, 원본 파일)"""
    path = path or (sources() or [None])[0]
    if not path:
        return pd.DataFrame(), None
    tmp = os.path.join(tempfile.gettempdir(), "mm_read.db")
    shutil.copyfile(path, tmp)                       # 원본은 건드리지 않음
    try:
        con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        raw = pd.read_sql("select ZDATE, ZCONTENT, ZMONEY from INOUTCOME where DO_TYPE='0'", con)
        con.close()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    raw["시장"] = raw["ZCONTENT"].map(_market)
    raw = raw[raw["시장"].notna()].copy()
    raw["일시"] = pd.to_datetime(pd.to_numeric(raw["ZDATE"], errors="coerce"), unit="ms") + pd.Timedelta(hours=9)
    raw = raw[raw["일시"] >= SINCE]
    out = pd.DataFrame({"일시": raw["일시"], "시장": raw["시장"], "구분": raw["ZCONTENT"].map(_kind),
                        "금액": pd.to_numeric(raw["ZMONEY"], errors="coerce"), "꼬리": raw["ZCONTENT"].map(_tail)})
    out = out.sort_values("일시").reset_index(drop=True)
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    out.attrs = {}
    out.to_parquet(_out(), index=False)
    with open(_out() + ".src", "w", encoding="utf-8") as fh:
        fh.write(f"{os.path.basename(path)}|{os.path.getmtime(path)}")
    return out, path


def load(refresh=True):
    """저장된 표. 더 새 백업이 있으면 다시 뽑는다. 반환 (표, 원본 파일 이름 또는 None)"""
    src = (sources() or [None])[0]
    p = _out()
    stamp = ""
    if os.path.exists(p + ".src"):
        stamp = open(p + ".src", encoding="utf-8").read()
    if refresh and src and stamp != f"{os.path.basename(src)}|{os.path.getmtime(src)}":
        try:
            return build(src)[0], os.path.basename(src)
        except Exception:
            pass
    if os.path.exists(p):
        return pd.read_parquet(p), stamp.split("|")[0] or None
    return pd.DataFrame(), None
