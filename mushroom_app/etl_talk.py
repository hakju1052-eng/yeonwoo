# -*- coding: utf-8 -*-
"""카카오톡 팀 채팅방(연우/진성 관리자 업무방) 사진 자료 (v1.33.0).

- 원본: 톡클라우드(talkcloud.kakao.com) 팀 데이터 → Claude 예약 작업이 메인PC 크롬에서 '저장하기'로 받아
  Downloads\\unifarm\\톡클라우드\\ 에 둔다 (파일 이름 = 올린 시각 YYYYMMDD_HHMMSS.jpg|png).
- 판독: talk_direct.py 가 읽은 결과를 app_cache\\talk\\index.json 에 둔다 (파일 이름 → {sender, uploaded, kind, title, result, md5, ...}).
  kind: 출하지시서(손글씨 사진 — 시장별 출하 수량) / 캡처(엑셀 화면 캡처 — 작업일지·균긁기·반가공 등, 숫자는 안 옮김) / 기타
- 쓰임: 출하 대사의 '지시 박스'(출하지시서 ↔ 장부·팩스 박스 대조), 업무일지 페이지의 '톡방 자료' 목록.
"""
import os, json, re
import pandas as pd
from . import config as C

MARKETS = ["서울청과", "동화청과", "한국청과", "대전청과", "대전농협", "대전중앙청과", "서부청과", "구리청과", "경인농산",
           "인천농산물", "광주중앙청과", "대양청과", "가락시장"]
_MK_ALIAS = {"인농": "인천농산물", "인천": "인천농산물", "경인": "경인농산", "서부": "서부청과", "서울": "서울청과", "구리": "구리청과",
             "광주": "광주중앙청과", "광중": "광주중앙청과", "중앙청과": "광주중앙청과", "대전": "대전청과", "대청": "대전청과",
             "대농": "대전농협", "대전공판장": "대전농협", "대전농협공판장": "대전농협", "대중": "대전중앙청과", "동화": "동화청과",
             "한국": "한국청과", "대양": "대양청과", "가락": "가락시장"}


def index_path():
    return os.path.join(C.CACHE_DIR, "talk", "index.json")


def photo_dir():
    return os.path.join(C.DATA_DIR, "톡클라우드")


def load_index():
    p = index_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def norm_market(s):
    s = re.sub(r"\s+", "", str(s or ""))
    if s in MARKETS:
        return s
    for k, v in _MK_ALIAS.items():
        if k in s:
            return v
    for m in MARKETS:
        if m[:2] in s:
            return m
    return s or None


def crop_of(item):
    t = str(item or "")
    if "팽" in t:
        return "팽이"
    if "새송" in t or "미니" in t or "에린" in t or "송이" in t:
        return "새송이"
    return None


def _uploaded_ts(v):
    try:
        return pd.to_datetime(v)
    except Exception:
        return pd.NaT


def docs():
    """읽은 사진 목록: 파일, 올린사람, 올린시각, 종류, 제목, 메모."""
    rows = []
    for f, e in load_index().items():
        rows.append({"파일": f, "올린사람": e.get("sender") or "", "올린시각": _uploaded_ts(e.get("uploaded")),
                     "종류": e.get("kind") or "", "제목": e.get("title") or "", "메모": e.get("note") or "",
                     "읽은때": e.get("read_at") or ""})
    D = pd.DataFrame(rows)
    return D.sort_values("올린시각", ascending=False).reset_index(drop=True) if len(D) else D


def orders():
    """출하지시서 줄 단위: 날짜(출하일), 시장, 작물, 출하자, 품목, 규격kg, 등급, 수량(박스), 파일."""
    rows = []
    for f, e in load_index().items():
        if e.get("kind") != "출하지시서":
            continue
        for o in (e.get("result") or {}).get("orders", []):
            d = pd.to_datetime(o.get("ship_date"), errors="coerce")
            if pd.isna(d):
                continue
            mk = norm_market(o.get("market"))
            for ln in o.get("lines", []):
                q = ln.get("qty")
                if q is None:
                    continue
                rows.append({"날짜": d, "시장": mk, "작물": crop_of(ln.get("item")) or crop_of(o.get("crop")), "출하자": o.get("shipper") or "",
                             "품목": ln.get("item") or "", "규격kg": ln.get("unit_kg"), "등급": ln.get("grade") or "", "수량": float(q), "파일": f})
    O = pd.DataFrame(rows)
    return O.sort_values(["날짜", "시장"]).reset_index(drop=True) if len(O) else O


def orders_by_day():
    """출하 대사용: 작물, 시장, 날짜(출하일), 지시박스, 지시파일."""
    O = orders()
    if O.empty:
        return pd.DataFrame(columns=["작물", "시장", "날짜", "지시박스", "지시파일"])
    O = O.dropna(subset=["작물", "시장"])
    G = O.groupby(["작물", "시장", "날짜"]).agg(지시박스=("수량", "sum"), 지시파일=("파일", lambda s: ", ".join(sorted(set(s))))).reset_index()
    return G
