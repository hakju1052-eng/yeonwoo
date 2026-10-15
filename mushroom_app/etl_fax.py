# -*- coding: utf-8 -*-
"""팩스로 받은 도매시장 판매원표·정산서 읽기 (v1.26.0, v1.27.0 예약 직접 판독 연동).

경리 SynologyDrive\\스캔 폴더에 팩스기가 PDF 로 저장하고, 경리가 'MMDD 팽서울.pdf'·'MMDD 새구리 (2).pdf' 처럼
이름을 붙여 둔다(팽=팽이, 새=새송이, 뒤는 시장 줄임말). 지난 것은 스캔\\보관\\(M월\\) 로 옮긴다.
팩스는 글자가 없는 이미지 PDF 라 Claude(AI 질문창과 같은 API 키)에게 PDF 를 그대로 보내 표를 JSON 으로 받는다.
  - 한 번 읽은 파일은 app_cache\\fax\\index.json 에 (파일 이름, 크기) 로 기억 → 보관 폴더로 옮겨도 다시 읽지 않음
  - 한 번 갱신에 C.FAX_MAX_PER_RUN 개까지(최근 날짜부터) — 나머지는 다음 갱신 때 이어서
  - 읽은 결과는 수량×단가=금액, 줄 합 = 합계 를 스스로 검산해 '검산' 열에 남긴다
결과: app_cache\\fax_docs.parquet (문서 한 장 = 한 줄), fax_lines.parquet (경락 한 줄 = 한 줄)
"""
import os, re, json, time, base64, glob
import datetime as dt
import numpy as np
import pandas as pd
from . import config as C

# 'MMDD 팽서울.pdf' · 'MMDD 새구리 (2).pdf' · 'MMDD 팽대청-2.pdf' · 'MMDD 팽한국(.pdf' · 'MMDD 팽 대농.pdf' (v1.26.2: -2·괄호 변형도)
FAX_RE = re.compile(r"^(\d{2})(\d{2})\s*(팽|새)?\s*([가-힣A-Za-z ]+?)\s*((?:\(\s*\d*\s*\)?)|(?:-\s*\d+))?\s*\.pdf$", re.I)
MARKET_CODE = {"서울": "서울청과", "동화": "동화청과", "한국": "한국청과", "대청": "대전청과", "대농": "대전농협", "대공": "대전농협",
               "대중": "대전중앙청과", "서부": "서부청과", "구리": "구리청과", "경인": "경인농산", "인천": "인천농산물", "광중": "광주중앙청과",
               "대양": "대양청과", "가락": "가락시장", "인농": "인천농산물", "중앙": "광주중앙청과"}
CROP_CODE = {"팽": "팽이", "새": "새송이"}
RAW_RE = re.compile(r"^R25C-1i(\d{2})(\d{2})(\d{2})\d{6}\.pdf$", re.I)        # 팩스기가 붙이는 원본 이름(수신 시각)

SYSTEM = """당신은 버섯 농장 경리 보조입니다. 도매시장에서 팩스로 온 문서(판매원표·수탁판매 정산서·출하현황)를 읽어 JSON 으로만 답합니다.
문서 한 장(같은 출하자·같은 날짜의 표 하나)마다 slips 배열에 하나씩 넣으세요. 페이지가 여러 장이면 모두 읽습니다.
{"slips":[{"doc_type":"판매원표|정산서|기타","market":"도매시장 법인 이름","date":"YYYY-MM-DD","shipper":"출하자 이름(예: 진성, 연우, 피어리스)",
 "lines":[{"item":"품목(예: 팽이버섯, 새송이)","grade":"등급(특·상·파지 등, 없으면 null)","unit_kg":5.0,"qty":60,"price":9500,"amount":570000}],
 "total_qty":360,"total_amount":3060000,"fee":null,"freight":null,"other_deduction":null,"net_amount":null}]}
규칙
- doc_type: 경락 내역(줄마다 수량·경락단가·금액)만 있으면 "판매원표". 수수료·운임·차감(차인)지급액·받으실금액이 있는 수탁판매 정산서·명세서·출하현황은 "정산서".
- date: 판매일자·경매일자·처리일자 (발행일·팩스 수신 시각 아님).
- lines: 경락된 각 줄. 소계·합계 줄은 넣지 않습니다. 한 줄이 두 칸(아래 칸에 하차임·운임·수수료·차인지급액)으로 된 양식은 위 칸의 수량·단가·금액만 쓰고, 아래 칸의 수수료는 fee 합계에 더합니다.
- 숫자는 쉼표 없는 수로. 읽을 수 없거나 없는 값은 null. 추측하지 마세요.
- total_qty·total_amount 는 문서에 적힌 합계(없으면 null). fee=수수료(상장수수료 포함) 합, freight=운임, net_amount=차감 후 지급액(받으실금액·총지급액·차인지급액 합계).
JSON 외의 글은 쓰지 마세요."""


def fax_roots():
    """스캔 폴더 후보 — Downloads 사본(경리 PC 공유에서 복사), 없으면 Z:"""
    office = getattr(C, "LOG_SOURCES", {}).get("office", [])
    # 사본(Downloads\SynologyDrive_사본\스캔)을 먼저 — 경리 PC 공유 \\SERA-DESK\스캔 에서 1시간마다 /MIR (v1.30.4). 없으면 Z:
    return [os.path.join(p, "스캔") for p in office]


def fax_root():
    for d in fax_roots():
        if os.path.isdir(d):
            return d
    return None


def _key(path):
    try:
        return f"{os.path.basename(path)}|{os.path.getsize(path)}"
    except OSError:
        return os.path.basename(path)


def file_info(path):
    """파일 이름 → (날짜, 작물, 시장). 이름 규칙이 아니면 None."""
    m = FAX_RE.match(os.path.basename(path))
    if not m:
        return None
    mm, dd, cc, code = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4).replace(" ", "")
    both = False
    while code[:1] in ("팽", "새") and code[1:] and code not in MARKET_CODE:      # '새팽대양' 같은 이름
        both = both or (cc is not None and code[0] != cc)
        cc, code = cc or code[0], code[1:]
    if both:
        cc = None                                                                  # 두 작물이 한 팩스 — 작물은 줄마다 품목으로 (v1.27.2)
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return None
    try:
        mt = dt.datetime.fromtimestamp(os.path.getmtime(path))
    except OSError:
        mt = dt.datetime.now()
    y = mt.year - (1 if mm - mt.month > 6 else 0)
    try:
        d = pd.Timestamp(y, mm, dd)
    except ValueError:
        return None
    return d, CROP_CODE.get(cc or ""), MARKET_CODE.get(code, code)


def list_files(since=None):
    root = fax_root()
    if not root:
        return []
    since = pd.Timestamp(since or getattr(C, "FAX_SINCE", "2026-08-01"))
    out = []
    for f in glob.glob(os.path.join(root, "*.pdf")) + glob.glob(os.path.join(root, "*", "*.pdf")) + glob.glob(os.path.join(root, "*", "*", "*.pdf")):
        info = file_info(f)
        if info is None and os.path.dirname(f) == root:           # 팩스기 원본 이름(R25C-1i261008110110.pdf) — 예약 직접 판독이 읽는다 (v1.27.0)
            m = RAW_RE.match(os.path.basename(f))
            if m:
                try:
                    info = (pd.Timestamp(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))), None, None)
                except ValueError:
                    info = None
        if info and info[0] >= since:
            out.append((f, info))
    return out


def _index_path():
    return os.path.join(C.CACHE_DIR, "fax", "index.json")


def load_index():
    p = _index_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def _save_index(idx):
    p = _index_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False)
    os.replace(tmp, p)


def _check_fail(rec, data):
    """읽은 결과의 검산 — 틀린 문서가 있으면 그 설명(문자열), 다 맞으면 ''"""
    try:
        D, _ = tables({"_": dict(rec, result=data)})
    except Exception as e:
        return f"표로 바꾸지 못함: {e}"
    if D.empty:
        return ""
    x = D[D["검산"] != "맞음"]
    return "; ".join(f"{r.문서} 합계 {r.표기합계금액} / 줄 합 {r.금액} / 지급액 {r.지급액}" for r in x.itertuples())


def _ask(path, info, retry=""):
    """PDF 를 Claude 에 보내 JSON 을 받는다."""
    import anthropic
    from . import ai_chat
    key = ai_chat.api_key()
    if not key:
        raise RuntimeError("API 키 없음")
    with open(path, "rb") as fh:
        b64 = base64.standard_b64encode(fh.read()).decode()
    d, crop, market = info
    hint = f"파일 이름: {os.path.basename(path)} — 날짜 {d:%Y-%m-%d}, 작물 {crop or '모름'}, 시장 {market}. JSON 만 답하세요."
    if retry:
        hint += (f"\n앞서 읽은 결과가 검산에 맞지 않았습니다({retry[:300]}). 숫자 한 자리씩 다시 확인하세요. "
                 "줄마다 수량×단가=금액(구리청과 콩알·파지는 관당 단가라 ×4·×7), 줄 합=합계, 정산서는 판매대금−수수료−운임−하차비=지급액. "
                 "수수료·지급액이 있으면 정산서입니다.")
    client = anthropic.Anthropic(api_key=key, max_retries=5)
    msg = client.messages.create(model=getattr(C, "FAX_MODEL", "") or C.AI_MODEL, max_tokens=4096, system=SYSTEM,
                                 messages=[{"role": "user", "content": [
                                     {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": b64}},
                                     {"type": "text", "text": hint}]}])
    text = "".join(getattr(b, "text", "") for b in msg.content)
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < s:
        raise ValueError("JSON 없음: " + text[:200])
    data = json.loads(text[s:e + 1])
    usage = getattr(msg, "usage", None)
    return data, {"in": getattr(usage, "input_tokens", None), "out": getattr(usage, "output_tokens", None)}


LOCK_MIN = 15          # 읽는 동안 10장마다 잠금 파일을 새로 쓴다 → 15분 넘게 그대로면 멈춘 것으로 본다


def _lock_path():
    return os.path.join(C.CACHE_DIR, "fax", "run.lock")


def running():
    """다른 곳(뒤에서 모두 읽기·자동 갱신)에서 읽는 중이면 그 정보(dict), 아니면 None"""
    p = _lock_path()
    try:
        age = time.time() - os.path.getmtime(p)
        with open(p, encoding="utf-8") as fh:
            info = json.load(fh)
    except Exception:
        return None
    if age > LOCK_MIN * 60 or (info.get("pid") == -1 and age > 120):     # -1 = 화면에서 막 띄운 자리표시
        return None
    info["age"] = age
    return info


def _write_lock(info):
    p = _lock_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(info, fh, ensure_ascii=False)


def _todo(files, idx):
    todo = [(f, i) for f, i in files if _key(f) not in idx or (idx[_key(f)].get("error") and idx[_key(f)].get("tries", 0) < 3)]
    todo.sort(key=lambda x: (x[1][0], os.path.basename(x[0])), reverse=True)        # 최근 날짜부터
    return todo


def run(progress=None, limit=None, workers=1, take_over=False):
    """새 팩스만 읽어 index 에 더하고 parquet 를 다시 만든다. 반환 (읽은 수, 남은 수)
    limit=0 이면 남은 것 전부. workers>1 이면 여러 장을 동시에 읽는다(API 제한에 걸리면 자동으로 쉬었다 다시)."""
    say = progress or (lambda s: None)
    other = running()
    if other and other.get("pid") not in (os.getpid(), -1 if take_over else os.getpid()):
        say(f"다른 곳에서 팩스를 읽는 중이라 건너뜀 ({other.get('done', 0)}/{other.get('total', 0)})")
        return 0, other.get("total", 0) - other.get("done", 0)
    files = list_files()
    if not files:
        say("팩스 폴더 없음 또는 대상 파일 없음")
        build(); return 0, 0
    idx = load_index()
    todo = _todo(files, idx)
    limit = getattr(C, "FAX_MAX_PER_RUN", 60) if limit is None else limit
    batch = todo if not limit else todo[:limit]
    if not batch:
        build(idx)
        say(f"새 팩스 없음 · 전체 {len(files)}개")
        return 0, 0
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    lock = threading.Lock()
    state = {"n": 0, "done": 0, "stop": False}
    started = dt.datetime.now().isoformat(timespec="seconds")
    _write_lock({"pid": os.getpid(), "start": started, "total": len(batch), "done": 0, "ok": 0})

    def one(f, info):
        if state["stop"]:
            return
        k = _key(f)
        rec = {"file": os.path.basename(f), "path": f, "date": f"{info[0]:%Y-%m-%d}", "crop": info[1], "market": info[2]}
        err = None
        for attempt in range(4):
            try:
                data, usage = _ask(f, info)
                bad = _check_fail(rec, data)
                if bad:                                   # 검산이 안 맞으면 한 번 더 읽힌다(돈 숫자라 두 번 확인)
                    try:
                        d2, u2 = _ask(f, info, retry=bad)
                        usage = {"in": (usage.get("in") or 0) + (u2.get("in") or 0), "out": (usage.get("out") or 0) + (u2.get("out") or 0)}
                        if not _check_fail(rec, d2):
                            data = d2
                    except Exception:
                        pass
                rec.update(result=data, usage=usage, by="API")
                err = None
                break
            except Exception as e:
                err = e
                s = str(e)
                if any(t in s.lower() for t in ("api 키", "authentication", "credit", "no module named", "permission")):
                    state["stop"] = True; break
                if any(t in s.lower() for t in ("rate", "overload", "529", "429", "timeout", "connection")):
                    time.sleep(15 * (attempt + 1)); continue
                break
        if err is not None and state["stop"]:
            state["why"] = str(err)[:200]
            return                                   # 키·잔액 문제는 파일 탓이 아니므로 실패 횟수에 세지 않는다
        rec["read_at"] = dt.datetime.now().isoformat(timespec="seconds")
        with lock:
            if err is not None:
                rec.update(error=str(err)[:300], tries=idx.get(k, {}).get("tries", 0) + 1)
            else:
                state["n"] += 1
            idx[k] = rec
            state["done"] += 1
            if state["done"] % 10 == 0:
                _save_index(idx)
                _write_lock({"pid": os.getpid(), "start": started, "total": len(batch), "done": state["done"], "ok": state["n"]})
                say(f"팩스 {state['done']}/{len(batch)} 읽음")

    try:
        with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
            futs = [ex.submit(one, f, i) for f, i in batch]
            for fu in as_completed(futs):
                fu.result()
    finally:
        with lock:
            _save_index(idx)
        try:
            os.remove(_lock_path())
        except OSError:
            pass
    build(idx)
    if state["stop"]:
        say("API 키·잔액 문제로 팩스 읽기를 멈췄습니다: " + state.get("why", ""))
    left = len(_todo(files, idx))
    n = state["n"]
    say(f"팩스 {n}개 새로 읽음 · 남은 {left}개 · 전체 {len(files)}개")
    return n, left


def start_background(workers=4):
    """앱 화면에서 '남은 팩스 모두 읽기' — 창 없이 따로 실행해 두고 바로 돌아온다."""
    import subprocess, sys
    if running():
        return False
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # ...\app (mushroom_app 의 상위)
    log = os.path.join(C.CACHE_DIR, "fax", "run_log.txt")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    exe = sys.executable
    if os.name == "nt" and exe.lower().endswith("python.exe") and os.path.exists(exe[:-10] + "pythonw.exe"):
        exe = exe[:-10] + "pythonw.exe"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    flags = 0
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000      # DETACHED_PROCESS | NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== {dt.datetime.now():%Y-%m-%d %H:%M:%S} 시작 (동시 {workers}) ===\n")
    out = open(log, "a", encoding="utf-8")
    subprocess.Popen([exe, "-m", "mushroom_app.etl_fax", "--all", "--workers", str(workers)], cwd=app_dir, env=env,
                     stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    _write_lock({"pid": -1, "start": dt.datetime.now().isoformat(timespec="seconds"), "total": 0, "done": 0, "ok": 0})
    return True


# ───────────────────────── 표 만들기 ─────────────────────────
def _num(v):
    if v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return np.nan


def _shipper(s):
    s = str(s or "")
    for k in ("진성", "연우", "피어리스"):
        if k in s:
            return k
    return s.strip() or "?"


def _crop_of(item, default, force=None):
    """줄의 작물. force 가 있으면 그것(한 장에 두 작물이 섞여 보일 때 파일 이름의 작물 — 동화청과 0909 정산서처럼 시장이 품목을
    '버섯(새송이)'로 잘못 찍는 일이 있고, 경리 확인으로 그 20박스는 전날 출하한 팽이였다, v1.27.2). 아니면 품목 글자, 없으면 파일 이름 작물."""
    if force:
        return force
    t = str(item or "")
    if "팽이" in t:
        return "팽이"
    if "새송이" in t or "송이" in t:
        return "새송이"
    return default or "?"


def _multiple(q, p, a):
    if not (q and p) or q * p <= 0:
        return False
    k = a / (q * p)
    return 2 <= round(k) <= 10 and abs(q * p * round(k) - a) < 1


def _market(slip_market, file_market):
    """문서에 적힌 시장이 파일 이름과 다르면(경리가 이름을 잘못 붙인 경우) 문서 쪽을 쓴다"""
    t = str(slip_market or "").replace(" ", "")
    for name in sorted(set(MARKET_CODE.values()), key=len, reverse=True):
        if name in t or (name == "대전농협" and "공판장" in t):
            return name
    return file_market


def tables(idx=None):
    """index → (docs, lines) 표"""
    idx = load_index() if idx is None else idx
    D, L = [], []
    for k, rec in idx.items():
        res = rec.get("result")
        if not res:
            continue
        fdate = pd.Timestamp(rec["date"])
        fi = file_info(rec.get("file") or "")
        if fi is not None:
            rec = dict(rec, crop=fi[1])              # 파일 이름의 작물은 지금 규칙으로 다시 푼다('새팽대양' → 없음, v1.27.2)
        for si, s in enumerate(res.get("slips") or []):
            try:
                sd = pd.Timestamp(s.get("date")) if s.get("date") else fdate
            except Exception:
                sd = fdate
            if abs((sd - fdate).days) > 3:                    # 모델이 날짜를 잘못 읽은 경우 파일 이름 날짜를 쓴다
                sd = fdate
            lines = s.get("lines") or []
            crops = [_crop_of(l.get("item"), rec.get("crop")) for l in lines] or [rec.get("crop") or "?"]
            # 한 장에 두 작물이 섞여 보이는데 파일 이름이 한 작물이면 이름을 믿는다(시장이 품목을 잘못 찍은 것). '새팽대양'처럼 이름도 두 작물이면 줄대로.
            force = rec.get("crop") if ({"팽이", "새송이"} <= set(crops) and rec.get("crop") in ("팽이", "새송이")) else None
            if force:
                crops = [force] * len(crops)
            crop = max(set(crops), key=crops.count)
            ok_lines = 0
            for li, l in enumerate(lines):
                q, p, a = _num(l.get("qty")), _num(l.get("price")), _num(l.get("amount"))
                # 정산서의 요약 줄(단가 없이 수량·판매대금만)은 수량·금액만 있으면 맞는 것으로 본다
                # 구리청과 콩알·파지처럼 관당(kg당) 단가를 찍는 줄은 금액 = 수량×단가×(2~10) 도 맞는 것으로 본다
                good = bool(pd.notna(q) and pd.notna(a) and (pd.isna(p) or abs(q * p - a) < 1 or _multiple(q, p, a)))
                ok_lines += good
                L.append({"파일": rec["file"], "장": si, "날짜": sd, "시장": _market(s.get("market"), rec.get("market")), "작물": _crop_of(l.get("item"), rec.get("crop"), force),
                          "출하자": _shipper(s.get("shipper")), "문서": s.get("doc_type") or "", "등급": l.get("grade"), "박스당kg": _num(l.get("unit_kg")),
                          "수량": q, "단가": p, "금액": a, "줄검산": good})
            qs = sum(_num(l.get("qty")) for l in lines if pd.notna(_num(l.get("qty"))))
            am = sum(_num(l.get("amount")) for l in lines if pd.notna(_num(l.get("amount"))))
            tq, ta = _num(s.get("total_qty")), _num(s.get("total_amount"))
            check = (ok_lines == len(lines)) and (pd.isna(tq) or abs(tq - qs) < 0.5) and (pd.isna(ta) or abs(ta - am) < 1)
            fee, fr, ot, net = (_num(s.get(c)) for c in ("fee", "freight", "other_deduction", "net_amount"))
            gross = ta if pd.notna(ta) else (am if lines else np.nan)
            net_ok = None
            if pd.notna(net) and pd.notna(gross):
                ded = sum(x for x in (fee, fr, ot) if pd.notna(x))
                net_ok = abs(gross - ded - net) < 1
                check = check and net_ok
            mk = _market(s.get("market"), rec.get("market"))
            sig = json.dumps([s.get("doc_type"), [[_num(l.get(c)) for c in ("qty", "price", "amount")] for l in lines], tq, ta,
                              _num(s.get("fee")), fr, ot, net], sort_keys=True, default=str)
            base = {"파일": rec["file"], "장": si, "날짜": sd, "시장": mk, "출하자": _shipper(s.get("shipper")), "문서": s.get("doc_type") or "",
                    "표기합계수량": tq, "표기합계금액": ta, "검산": "맞음" if check else "확인 필요",
                    "지급검산": "" if net_ok is None else ("맞음" if net_ok else "다름"), "비고": str(s.get("note") or ""), "읽은곳": rec.get("by", "API"), "서명": sig}
            by_crop = {}
            for l in lines:                                   # 한 장에 팽이·새송이가 같이 적힌 정산서는 작물별로 나눈다 (v1.26.5)
                c_ = _crop_of(l.get("item"), rec.get("crop"), force)
                q_, a_ = _num(l.get("qty")), _num(l.get("amount"))
                d_ = by_crop.setdefault(c_, {"n": 0, "q": 0.0, "a": 0.0})
                d_["n"] += 1
                d_["q"] += q_ if pd.notna(q_) else 0
                d_["a"] += a_ if pd.notna(a_) else 0
            if len(by_crop) <= 1:
                D.append({**base, "작물": crop, "줄수": len(lines), "수량": qs if lines else tq, "금액": am if lines else ta,
                          "수수료": _num(s.get("fee")), "운임": _num(s.get("freight")), "기타공제": ot, "지급액": net})
            else:
                tot_a = sum(v["a"] for v in by_crop.values()) or 1
                for c_, v in by_crop.items():
                    w = v["a"] / tot_a
                    share = lambda x: float(round(x * w)) if pd.notna(x) else np.nan        # 원 단위로 (v1.26.9)
                    D.append({**base, "작물": c_, "줄수": v["n"], "수량": v["q"], "금액": v["a"], "표기합계수량": np.nan, "표기합계금액": np.nan,
                              "수수료": share(_num(s.get("fee"))), "운임": share(_num(s.get("freight"))), "기타공제": share(ot), "지급액": share(net),
                              "비고": (base["비고"] + " · " if base["비고"] else "") + f"한 장에 두 작물(금액 비율로 나눔 {w:.0%})",
                              "서명": sig + f"#{c_}"})
    return pd.DataFrame(D), pd.DataFrame(L)


def build(idx=None):
    docs, lines = tables(idx)
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    docs.to_parquet(os.path.join(C.CACHE_DIR, "fax_docs.parquet"), index=False)
    lines.to_parquet(os.path.join(C.CACHE_DIR, "fax_lines.parquet"), index=False)
    return docs, lines


def load():
    p, q = os.path.join(C.CACHE_DIR, "fax_docs.parquet"), os.path.join(C.CACHE_DIR, "fax_lines.parquet")
    ip = _index_path()
    newer = max(os.path.getmtime(ip) if os.path.exists(ip) else 0, os.path.getmtime(__file__))     # 색인이 바뀌었거나 이 코드가 바뀌었으면
    if os.path.exists(ip) and (not os.path.exists(p) or newer > os.path.getmtime(p) + 1):
        try:                                   # index.json 이 밖에서(예약 직접 판독) 바뀌었거나 읽는 규칙이 바뀌면 표를 다시 만든다 (v1.27.0·v1.27.2)
            return build()
        except Exception:
            pass
    docs = pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()
    lines = pd.read_parquet(q) if os.path.exists(q) else pd.DataFrame()
    return docs, lines


def daily(docs=None):
    """(작물, 시장, 날짜) 별 팩스 수량·금액 + 수수료·운임·지급액.
    - 같은 문서를 두 번 받은 것(재전송·다른 파일에 같은 표)은 한 번만 센다(모든 줄·합계가 같으면 같은 문서)
    - 정산서가 있으면 정산서 금액(실제 정산)을 쓰고, 판매원표 합계와 맞는지 '원표대조'에 남긴다
    - 같은 판매분의 가정산·본정산이 둘 다 있으면(수량·금액이 같고 공제만 다름) 공제가 큰 본정산을 쓴다"""
    if docs is None:
        docs, _ = load()
    if docs is None or docs.empty:
        return pd.DataFrame()
    d = docs[docs["문서"].isin(["판매원표", "정산서"])].copy()
    if "서명" not in d:
        d["서명"] = d[["문서", "수량", "금액", "수수료", "지급액"]].astype(str).agg("|".join, axis=1)
    d["_공제"] = d[["수수료", "운임", "기타공제"]].sum(axis=1, min_count=1).fillna(0)
    rows = []
    for (crop, mk, day, sh), g in d.groupby(["작물", "시장", "날짜", "출하자"]):
        u = g.drop_duplicates("서명")
        W = u[u["문서"] == "판매원표"]
        S = u[u["문서"] == "정산서"]
        if len(S):
            S = S.sort_values("_공제", ascending=False).drop_duplicates(["수량", "금액"])      # 가정산·본정산 → 본정산
        base = S if len(S) else W
        wq, wa = W["수량"].sum(min_count=1), W["금액"].sum(min_count=1)
        sq, sa = S["수량"].sum(min_count=1), S["금액"].sum(min_count=1)
        if len(W) and len(S):
            chk = "맞음" if abs(wa - sa) < 1 and abs(wq - sq) < 0.5 else f"다름(원표 {wa:,.0f} / 정산 {sa:,.0f})"
        else:
            chk = "정산서만" if len(S) else "원표만"
        rows.append({"작물": crop, "시장": mk, "날짜": day, "출하자": sh, "팩스박스": base["수량"].sum(), "팩스금액": base["금액"].sum(),
                     "팩스수수료": S["수수료"].sum(min_count=1), "팩스운임": S["운임"].sum(min_count=1),
                     "팩스기타공제": S["기타공제"].sum(min_count=1), "팩스지급액": S["지급액"].sum(min_count=1),
                     "원표대조": chk, "가정산": bool(S["비고"].astype(str).str.contains("가정산").any()) if "비고" in S else False,
                     "팩스검산": "확인 필요" if (g["검산"] != "맞음").any() else "맞음",
                     "중복제외": len(g) - len(u), "팩스파일": ", ".join(sorted(set(g["파일"])))})
    R = pd.DataFrame(rows)
    if R.empty:
        return R
    s1 = lambda x: x.sum(min_count=1)
    j = lambda s: "; ".join(x for x in s if x not in ("맞음", "정산서만", "원표만")) or ("맞음" if (s == "맞음").any() else s.iloc[0])
    return R.groupby(["작물", "시장", "날짜"]).agg(팩스박스=("팩스박스", "sum"), 팩스금액=("팩스금액", "sum"), 팩스수수료=("팩스수수료", s1),
                                                 팩스운임=("팩스운임", s1), 팩스기타공제=("팩스기타공제", s1), 팩스지급액=("팩스지급액", s1),
                                                 원표대조=("원표대조", j), 가정산=("가정산", "any"),
                                                 팩스검산=("팩스검산", lambda s: "확인 필요" if (s != "맞음").any() else "맞음"),
                                                 출하자=("출하자", lambda s: "·".join(sorted(set(s)))), 팩스파일=("팩스파일", lambda s: ", ".join(s))).reset_index()


def status():
    """화면용: (대상 파일 수, 읽은 수, 오류 수, 남은 수, 최근 읽은 시각, 토큰 합)"""
    files = list_files()
    idx = load_index()
    keys = {_key(f) for f, _ in files}
    done = [v for k, v in idx.items() if k in keys and v.get("result")]
    err = [v for k, v in idx.items() if k in keys and v.get("error")]
    last = max((v.get("read_at", "") for v in idx.values()), default="")
    tok = sum((v.get("usage") or {}).get("in") or 0 for v in idx.values()), sum((v.get("usage") or {}).get("out") or 0 for v in idx.values())
    direct = sum(1 for v in done if str(v.get("by", "")).startswith("직접판독"))
    return {"files": len(files), "done": len(done), "error": len(err), "left": len(files) - len(done), "last": last, "tokens": tok, "root": fax_root(),
            "direct": direct, "api": len(done) - direct}


if __name__ == "__main__":
    # 남은 팩스 전부 읽기:  python -m mushroom_app.etl_fax --all --workers 4
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="남은 팩스 전부")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    say = lambda m: print(f"{dt.datetime.now():%H:%M:%S} {m}", flush=True)
    st = status()
    say(f"폴더 {st['root']} · 대상 {st['files']}개 · 읽은 {st['done']}개 · 남은 {st['left']}개")
    n, left = run(progress=say, limit=0 if a.all else a.limit, workers=a.workers, take_over=True)
    say(f"끝 · {n}개 읽음 · 남은 {left}개 · {(time.time() - t0) / 60:.1f}분")
