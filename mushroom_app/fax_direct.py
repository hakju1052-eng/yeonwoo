# -*- coding: utf-8 -*-
"""팩스 직접 판독 도우미 (v1.27.0) — Claude 예약 작업이 API 없이 팩스를 읽어 app_cache\\fax\\index.json 에 넣을 때 쓰는 명령줄 도구.

표준 라이브러리만 쓴다(메인PC 밖, Claude 작업 환경에서 돌아감). 팩스기 원본 이름(R25C-1i261008110110.pdf 처럼 아직 경리가
이름을 안 붙인 것)도 대상이며, 날짜·시장·작물은 읽은 내용에서 정한다.

  python3 fax_direct.py pending  <listing.json> <index.json> <pending.json>   스캔 폴더 목록(device_list_dir 결과)과 index 를 비교해 안 읽은 PDF 목록
  python3 fax_direct.py render   <pending.json> <pdf_dir> <png_dir> <items.json>  PDF → PNG(150dpi 회색) 변환, 읽을 목록(items.json) 작성
  python3 fax_direct.py merge    <index.json> <items.json> <results.jsonl> <new_index.json> <report.txt>   읽은 결과를 검산해 index 에 합침

index 항목 모양(앱 etl_fax 와 같음): {"file","path","date","crop","market","result":{"slips":[…]},"by","read_at","usage","md5", ("note")}
"""
import os, re, sys, json, glob, hashlib, subprocess, datetime as dt

try:
    from .etl_fax import FAX_RE, MARKET_CODE, CROP_CODE          # 앱 안에서 돌 때
except Exception:                                                   # 단독 실행(판다스 없는 환경)
    FAX_RE = re.compile(r"^(\d{2})(\d{2})\s*(팽|새)?\s*([가-힣A-Za-z ]+?)\s*((?:\(\s*\d*\s*\)?)|(?:-\s*\d+))?\s*\.pdf$", re.I)
    MARKET_CODE = {"서울": "서울청과", "동화": "동화청과", "한국": "한국청과", "대청": "대전청과", "대농": "대전농협", "대공": "대전농협",
                   "대중": "대전중앙청과", "서부": "서부청과", "구리": "구리청과", "경인": "경인농산", "인천": "인천농산물", "광중": "광주중앙청과",
                   "대양": "대양청과", "가락": "가락시장", "인농": "인천농산물", "중앙": "광주중앙청과"}
    CROP_CODE = {"팽": "팽이", "새": "새송이"}

RAW_RE = re.compile(r"^R25C-1i(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})\.pdf$", re.I)      # 팩스기 원본 이름: 수신 시각
SINCE = "2026-08-01"
SKIP_DIRS = ("보관",)       # 보관 폴더는 경리가 이미 정리한 것 — 이름이 규칙에 맞는 것만 대상(원본 이름은 새 폴더에서만)
MARKETS = sorted(set(MARKET_CODE.values()), key=len, reverse=True)


def _name_info(name, mtime_ms=None):
    """파일 이름 → (날짜 'YYYY-MM-DD' 또는 None, 작물 또는 None, 시장 또는 None, 종류 '이름'/'원본'/None)"""
    m = FAX_RE.match(name)
    now = dt.datetime.fromtimestamp((mtime_ms or 0) / 1000) if mtime_ms else dt.datetime.now()
    if m:
        mm, dd, cc, code = int(m.group(1)), int(m.group(2)), m.group(3), m.group(4).replace(" ", "")
        while code[:1] in ("팽", "새") and code[1:] and code not in MARKET_CODE:
            cc, code = cc or code[0], code[1:]
        y = now.year - (1 if mm - now.month > 6 else 0)
        try:
            d = dt.date(y, mm, dd).isoformat()
        except ValueError:
            return None, None, None, None
        return d, CROP_CODE.get(cc or ""), MARKET_CODE.get(code, code), "이름"
    r = RAW_RE.match(name)
    if r:
        yy, mm, dd = int(r.group(1)), int(r.group(2)), int(r.group(3))
        try:
            return dt.date(2000 + yy, mm, dd).isoformat(), None, None, "원본"
        except ValueError:
            return None, None, None, "원본"
    return None, None, None, None


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- pending
def cmd_pending(listing_json, index_json, out_json):
    """listing: device_list_dir(recursive) 의 JSON 그대로(entries[name,type,size,mtimeMs]). 안 읽은 PDF 를 고른다."""
    L = json.load(open(listing_json, encoding="utf-8"))
    idx = json.load(open(index_json, encoding="utf-8")) if os.path.exists(index_json) else {}
    sizes = {}
    for k, v in idx.items():
        try:
            sizes.setdefault(int(k.rsplit("|", 1)[1]), []).append(k)
        except (IndexError, ValueError):
            pass
    out, skipped = [], []
    for e in L.get("entries", []):
        if e.get("type") != "file" or not e["name"].lower().endswith(".pdf"):
            continue
        rel = e["name"].replace("\\", "/")
        base = rel.split("/")[-1]
        if any(p in SKIP_DIRS for p in rel.split("/")[:-1]) and not FAX_RE.match(base):
            continue
        key = f"{base}|{e['size']}"
        if key in idx and not idx[key].get("error"):
            continue
        d, crop, market, kind = _name_info(base, e.get("mtimeMs"))
        if kind is None:
            skipped.append(base)              # '성실근로자 안내문.pdf' 같은 이름 — 팩스 규칙도 원본 이름도 아니면 건드리지 않음
            continue
        if d and d < SINCE:
            continue
        out.append({"id": f"p{len(out):03d}", "key": key, "file": base, "rel": rel, "size": e["size"], "mtimeMs": e.get("mtimeMs"),
                    "date": d, "crop": crop, "market": market, "kind": kind,
                    "same_size_keys": sizes.get(int(e["size"]), [])})     # 크기가 같은 기존 항목 → 이름만 바뀐 같은 파일일 수 있음(md5 로 확인)
    out.sort(key=lambda x: (x["date"] or "", x["file"]))
    json.dump(out, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"읽을 파일 {len(out)}개 (이름 붙은 것 {sum(1 for x in out if x['kind']=='이름')}, 팩스기 원본 이름 {sum(1 for x in out if x['kind']=='원본')})"
          + (f" · 규칙 밖 이름이라 건너뜀 {len(skipped)}개: " + ", ".join(skipped[:5]) if skipped else ""))
    for x in out:
        print(f"  {x['id']} {x['file']}  {x['date'] or '날짜?'} {x['crop'] or ''} {x['market'] or ''}" + ("  (크기 같은 기존 항목 있음)" if x["same_size_keys"] else ""))
    return out


# ---------------------------------------------------------------- render
def cmd_render(pending_json, pdf_dir, png_dir, items_json, index_json=None):
    """pdf_dir 에 받아 둔 PDF(pending 의 rel 경로)를 PNG 로. 같은 md5 가 index 에 이미 있으면 읽지 않고 별칭으로 처리(items 의 'alias_of')."""
    P = json.load(open(pending_json, encoding="utf-8"))
    idx = json.load(open(index_json, encoding="utf-8")) if index_json and os.path.exists(index_json) else {}
    md5_of = {v.get("md5"): k for k, v in idx.items() if v.get("md5")}
    os.makedirs(png_dir, exist_ok=True)
    items = []
    for it in P:
        src = os.path.join(pdf_dir, it["rel"])
        if not os.path.exists(src):
            src2 = os.path.join(pdf_dir, it["file"])
            if os.path.exists(src2):
                src = src2
            else:
                print(f"  없음: {it['rel']}"); continue
        it = dict(it, local=src, md5=_md5(src))
        if it["md5"] in md5_of:
            it["alias_of"] = md5_of[it["md5"]]
            items.append(it)
            print(f"  {it['id']} {it['file']}: 이미 읽은 파일과 내용 같음 → {it['alias_of']} 결과를 그대로 씀")
            continue
        out = os.path.join(png_dir, it["id"])
        if not glob.glob(out + "-*.png"):
            subprocess.run(["pdftoppm", "-r", "150", "-gray", "-png", src, out], check=True)
        it["pngs"] = sorted(glob.glob(out + "-*.png"))
        items.append(it)
    json.dump(items, open(items_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_read = sum(1 for i in items if not i.get("alias_of"))
    print(f"읽을 문서 {n_read}개, 쪽 {sum(len(i.get('pngs', [])) for i in items)}장, 별칭 {len(items) - n_read}개 → {items_json}")
    return items


# ---------------------------------------------------------------- 검산
def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _line_ok(q, p, a, kg):
    if a is None or q is None or p is None:
        return True                                   # 없는 값은 검산하지 않음(정산서 요약 줄 등)
    if abs(q * p - a) < 1:
        return True
    if kg and abs(q * p * kg - a) < 1:                # kg 단가
        return True
    if q * p > 0:
        k = a / (q * p)
        if 2 <= round(k) <= 10 and abs(q * p * round(k) - a) < 1:   # 관 단가(콩알 ×4, 파지 ×7 등)
            return True
    return False


def check(result):
    """결과 JSON 의 검산. 반환 (문제 설명 문자열 또는 '', 요약 dict)"""
    probs = []
    slips = (result or {}).get("slips") or []
    if not isinstance(slips, list):
        return "slips 가 배열이 아님", {}
    tot_a = 0.0
    for si, s in enumerate(slips, 1):
        lines = s.get("lines") or []
        bad = [l for l in lines if not _line_ok(_num(l.get("qty")), _num(l.get("price")), _num(l.get("amount")), _num(l.get("unit_kg")))]
        if bad:
            probs.append(f"{si}장 수량×단가≠금액 {len(bad)}줄")
        sq = sum(_num(l.get("qty")) or 0 for l in lines)
        sa = sum(_num(l.get("amount")) or 0 for l in lines)
        tq, ta = _num(s.get("total_qty")), _num(s.get("total_amount"))
        if lines and tq is not None and abs(sq - tq) >= 0.5:
            probs.append(f"{si}장 줄 수량 합 {sq:g} ≠ 합계 {tq:g}")
        if lines and ta is not None and abs(sa - ta) >= 1:
            probs.append(f"{si}장 줄 금액 합 {sa:,.0f} ≠ 합계 {ta:,.0f}")
        net = _num(s.get("net_amount"))
        if net is not None and ta is not None:
            ded = (_num(s.get("fee")) or 0) + (_num(s.get("freight")) or 0) + (_num(s.get("other_deduction")) or 0)
            if abs(ta - ded - net) >= 1:
                probs.append(f"{si}장 합계−공제 {ta - ded:,.0f} ≠ 지급액 {net:,.0f}")
        tot_a += ta if ta is not None else sa
    return "; ".join(probs), {"slips": len(slips), "amount": tot_a}


def _crop_from(result):
    names = []
    for s in (result or {}).get("slips") or []:
        for l in s.get("lines") or []:
            t = str(l.get("item") or "")
            names.append("팽이" if "팽이" in t else ("새송이" if "새송이" in t else None))
    names = [n for n in names if n]
    return max(set(names), key=names.count) if names else None


def _market_from(result):
    for s in (result or {}).get("slips") or []:
        t = str(s.get("market") or "").replace(" ", "")
        for name in MARKETS:
            if name in t or (name == "대전농협" and "공판장" in t):
                return name
    return None


def _date_from(result):
    ds = [s.get("date") for s in (result or {}).get("slips") or [] if re.match(r"^\d{4}-\d{2}-\d{2}$", str(s.get("date") or ""))]
    return min(ds) if ds else None


# ---------------------------------------------------------------- merge
def cmd_merge(index_json, items_json, results_jsonl, out_json, report_txt):
    idx = json.load(open(index_json, encoding="utf-8")) if os.path.exists(index_json) else {}
    items = {i["key"]: i for i in json.load(open(items_json, encoding="utf-8"))}
    R = {}
    if os.path.exists(results_jsonl):
        for ln in open(results_jsonl, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
                R[o["key"]] = o["result"]
            except Exception as e:
                print(f"  결과 줄 무시(JSON 오류): {ln[:80]} — {e}")
    now = dt.datetime.now().isoformat(timespec="seconds")
    lines, n_ok, n_bad, n_alias, missing = [], 0, 0, 0, []
    for key, it in items.items():
        if it.get("alias_of") and it["alias_of"] in idx:
            src = idx[it["alias_of"]]
            idx[key] = dict(src, file=it["file"], path=it["rel"], md5=it["md5"], read_at=now, by=src.get("by", "직접판독"),
                            note=((src.get("note") + " · ") if src.get("note") else "") + f"같은 내용: {src.get('file')}")
            n_alias += 1
            lines.append(f"= {it['file']}: {src.get('file')} 와 같은 파일(이름만 다름)")
            continue
        if key not in R:
            missing.append(it["file"]); continue
        res = R[key]
        bad, summ = check(res)
        date = (it.get("date") if it.get("kind") == "이름" else None) or _date_from(res) or it.get("date")
        crop = it.get("crop") or _crop_from(res)
        market = it.get("market") or _market_from(res)
        rec = {"file": it["file"], "path": it["rel"], "date": date, "crop": crop, "market": market, "result": res,
               "by": "직접판독(예약)", "read_at": now, "usage": {"in": 0, "out": 0}, "md5": it.get("md5")}
        if not date:
            bad = (bad + "; " if bad else "") + "날짜를 정하지 못함"
        if bad:
            rec["note"] = "검산 불일치: " + bad
            n_bad += 1
        else:
            n_ok += 1
        idx[key] = rec
        kinds = ",".join(sorted({str(s.get("doc_type") or "?") for s in (res.get("slips") or [])})) or "기타"
        lines.append(f"{'!' if bad else 'o'} {it['file']} → {date or '?'} {crop or '?'} {market or '?'} {kinds} {summ.get('amount', 0):,.0f}원"
                     + (f"  [{bad}]" if bad else ""))
    tmp = out_json + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False)
    os.replace(tmp, out_json)
    head = f"{now[:16]} 팩스 직접 판독: 새로 읽음 {n_ok}개 정상, {n_bad}개 검산 불일치, 같은 파일 {n_alias}개" + (f", 결과 없음 {len(missing)}개({', '.join(missing[:5])})" if missing else "")
    rep = head + "\n" + "\n".join(lines) + "\n"
    with open(report_txt, "a", encoding="utf-8") as fh:
        fh.write(rep + "\n")
    print(rep)
    return n_ok, n_bad, n_alias, missing


def main(argv=None):
    a = argv or sys.argv[1:]
    if not a:
        print(__doc__); return 2
    c = a[0]
    if c == "pending" and len(a) == 4:
        cmd_pending(a[1], a[2], a[3])
    elif c == "render" and len(a) in (5, 6):
        cmd_render(a[1], a[2], a[3], a[4], a[5] if len(a) == 6 else None)
    elif c == "merge" and len(a) == 6:
        cmd_merge(a[1], a[2], a[3], a[4], a[5])
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
