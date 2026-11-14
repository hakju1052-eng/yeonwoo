# -*- coding: utf-8 -*-
"""업무일지 읽기 (Z:\\업무일지 → mirror_nas.bat 5e 단계가 Downloads\\unifarm\\기타일지\\업무일지 로 복사).

폴더 구조: 업무일지\\<담당자>\\... 파일. 형식은 세 가지.
  1) 워드(.docx) 업무보고서 — 표 하나(또는 여러 개)에 '일시 / 업무명 / 담당자 / 업무내용 / 비고' 줄이 날짜마다 반복.
     월별 파일(안석구·이장우·김세영·오동준)과 하루 한 파일(남기천: '일 시: 2026년 10월 31일', '기 타:' 칸) 모두 같은 방식으로 읽는다.
  2) 한글(.hwp) 업무보고서 — 글자만 뽑아 '일 시 :' 마다 끊는다 (etl_banagong.hwp_text 를 그대로 씀, olefile 필요).
  3) 엑셀 주간 업무일지(권석현 업무일지.xlsx) — 'X요일 M/D' 칸 아래 줄이 그날 업무, F열이 처리내용. 연도는 요일로 맞춘다.
  4) 반가공 부장 일일업무보고서(엑셀, 하루 한 파일) — Z:\\반가공\\일일업무보고서(김병준)\\2611\\261113_일일업무보고서.xlsx.
     copy_banagong.bat 이 Downloads\\반가공자료\\일일업무보고서(이름) 으로 복사한 것을 함께 읽는다(extra_dirs). 읽는 법은 etl_banagong._kbj_sheet 와 같다.
  5) 반가공 일일 업무보고서(정미 씨, 한글 ~2026-10-06 · 엑셀 2026-10-07~) — 원본을 다시 읽지 않고 반가공 현황판이 읽어 둔
     app_cache\\banagong\\reports__all.json 을 가져다 쓴다(banagong_recs). etl_cli 가 반가공 → 업무일지 순으로 돌아 같은 갱신에서 바로 반영.
날짜 없는 문서(시험결과 보고서 등)는 '문서'로 따로 모아 제목·앞부분만 둔다.

개인정보: 전화번호·계좌번호처럼 보이는 숫자, 차량번호, 주민번호는 가리고, 출근·퇴근 시각 줄과
급여·계좌·비밀번호 같은 낱말이 든 줄은 버린다. (파일 이름에 계정·비번·급여가 든 것은 복사 단계에서 이미 빠짐)
캐시: app_cache\\worklog.json (화면이 읽는 것), app_cache\\worklog_files.json (파일별 읽은 결과 — 바뀐 파일만 다시 읽음).
"""
import os, re, json, glob, time, zipfile, datetime as dt
import xml.etree.ElementTree as ET
from . import config as C

RULES = 1                     # 읽는 규칙 번호 — 바뀌면 전부 다시 읽는다
OUT = os.path.join(C.CACHE_DIR, "worklog.json")
FILES = os.path.join(C.CACHE_DIR, "worklog_files.json")
EXTS = (".docx", ".hwp", ".xlsx", ".xlsm")
SKIP_NAME = re.compile(r"아이디|비번|비밀번호|계정|시리얼|급여|\._DAV|^~\$")
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# ───────────── 분류 (줄마다 자동으로 붙이는 꼬리표) ─────────────
TAGS = [
    ("설비", r"설비|수리|고장|교체|누수|모타|모터|콘베아|컨베이어|체인|펌프|냉동기|콤푸|보일러|가마|페킹|패킹|센서|PLC|P\.L\.C|"
             r"용접|전자변|밸브|베어링|부품|A/?S|단종|공사|증축|설치|정비|텐션"),
    ("입고", r"입고|미강|비트|콘컵|소맥피|대두피|맥주박|배아박|주유"),
    ("출고·판매", r"출고|출하|납품|배송|경락|경매|판매"),
    ("생육·배양", r"생육|발이|배양|접종|종균|원균|균긁기|균긋기|탈병|입병|살균|배지|재배|일차|주수"),
    ("품질·오염", r"오염|세균|검출|불량|곰팡이|잡균|리스테리아|시료|성적서|품질|이물"),
    ("위생·방역", r"세스코|방역|소독|위생|청소|해충|빈대|바퀴|HACCP|해썹"),
    ("인력", r"근로자|기숙사|면접|채용|입국|출국|귀국|휴가|교육|근태|인원|외국인|작업자"),
    ("구매", r"구매|발주|견적|주문|조달|구입"),
    ("대외", r"방문|미팅|회의|협의|점검위원|농진청|품관원|지원사업|변호사|법률|소송|청구|계약"),
]
_TAGS = [(n, re.compile(p, re.I)) for n, p in TAGS]
ISSUE = re.compile(r"고장|이상(?!\s*유무|\s*없|적|무)|불량|오염|누수|파손|지연|심각|문제|민원|단종|불가|미완|사고|부상|터짐|정지|오작동|부족|막힘|"
                   r"검출|곰팡이|잡균|빈대|클레임|반품|취소|중단|포기|소송|안\s*됨|미작동|작동\s*안|보류")
NO_ISSUE = re.compile(r"특이\s*사항\s*없음|이상\s*없음|문제\s*없음|불검출|정상")


def tag_line(t):
    tags = [n for n, rx in _TAGS if rx.search(t)]
    if ISSUE.search(t) and not (NO_ISSUE.search(t) and not re.search(r"고장|파손|누수|오염", t)):
        tags.insert(0, "이슈")
    return tags


# ───────────── 개인정보 가리기 ─────────────
_DROP = re.compile(r"^\s*(?:[○●\-·•]\s*)?(?:출근|퇴근)\s*(?:시간|시각)?\s*[:：]?\s*\d|급여|월급|연봉|계좌|통장|비밀번호|비번|아이디|주민\s*번호|주민등록")
_MASKS = [
    (re.compile(r"\b\d{6}\s*-\s*[1-4]\d{6}\b"), "******-*******"),                 # 주민번호
    (re.compile(r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b"), "010-****-****"),     # 휴대전화
    (re.compile(r"\b0\d{1,2}[-.)]\s?\d{3,4}[-.]\d{4}\b"), "0**-***-****"),          # 일반전화
    (re.compile(r"\b\d{2,6}-\d{2,6}-\d{2,8}(?:-\d{1,6})?\b"), "***-***-***"),       # 계좌번호 모양
    (re.compile(r"\b\d{2,3}\s?[가-힣]\s?\d{4}\b"), "**가****"),                       # 차량번호
]


def clean_text(t):
    t = (t or "").encode("utf-8", "replace").decode("utf-8", "replace")
    for rx, rep in _MASKS:
        t = rx.sub(rep, t)
    return t


def to_lines(text):
    """긴 글 → [{t, sub, tags}] (빈 줄·동그라미만 있는 줄·민감한 줄은 버림)."""
    out = []
    for raw in (text or "").replace("\r", "\n").split("\n"):
        s = raw.rstrip()
        t = s.strip()
        t2 = re.sub(r"^[○●◦·•\-–⇒※]+\s*", "", t).strip()
        if not t2 or t2 in ("○", "-", "없음.") or re.fullmatch(r"건|의|사|항|건의|사항|건의\s*사항|비|고|기타", t2):
            continue
        if _DROP.search(t):
            continue
        sub = bool(re.match(r"^\s{2,}", s)) or bool(re.match(r"^(?:[-–·•]|\d+[.\-]\d+|\(\w)", t))
        t = clean_text(t2)
        out.append({"t": t, "sub": sub, "tags": tag_line(t)})
    return out


# ───────────── 날짜 ─────────────
_DATE = re.compile(r"(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?")


def find_date(s):
    m = _DATE.search(s or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _norm(s):
    return re.sub(r"[\s:：]+", "", s or "")


# ───────────── 워드(.docx) ─────────────
def _p_text(p):
    buf = []
    for el in p.iter():
        if el.tag == W + "t":
            buf.append(el.text or "")
        elif el.tag == W + "tab":
            buf.append(" ")
        elif el.tag in (W + "br", W + "cr"):
            buf.append("\n")
    return "".join(buf)


def _tc_text(tc):
    """칸 글자. 칸 안에 표가 또 있으면(오동준 양식의 작업명|원료|수량) 한 줄에 'a | b | c' 로 붙인다."""
    out = []
    for ch in tc:
        if ch.tag == W + "p":
            out.append(_p_text(ch))
        elif ch.tag == W + "tbl":
            for tr in ch.findall(W + "tr"):
                cells = [re.sub(r"\s+", " ", _tc_text(c)).strip() for c in tr.findall(W + "tc")]
                cells = [c for c in cells if c]
                if cells:
                    out.append(" | ".join(cells))
        elif ch.tag == W + "sdt":
            c = ch.find(W + "sdtContent")
            if c is not None:
                out.append(_tc_text(c))
    return "\n".join(out)


def docx_rows(path):
    """본문 순서대로 ('row', [칸 글자...]) / ('p', 글자). 이미지는 건드리지 않는다."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    out = []

    def walk(el):
        for ch in el:
            if ch.tag == W + "tbl":
                for tr in ch.findall(W + "tr"):
                    cells = []
                    for tc in tr.findall(W + "tc"):
                        cells.append(_tc_text(tc).strip())
                    out.append(("row", cells))
            elif ch.tag == W + "p":
                t = _tc_text(ch).strip()
                if t:
                    out.append(("p", t))
            elif ch.tag == W + "sdt":
                c = ch.find(W + "sdtContent")
                if c is not None:
                    walk(c)
    walk(body)
    return out


_APPROVAL = re.compile(r"^(결재|담당자|부서장|이사|대표|팀장)$")


def _row_vals(cells):
    """값 칸 = 둘째 칸 하나. (그 뒤는 결재란: 첫 줄엔 '결재', 아랫줄엔 빈 칸(세로 병합) + 결재자 이름이라 쓰지 않는다)"""
    return [cells[1]] if len(cells) > 1 and cells[1] and _norm(cells[1]) != "결재" else []


def parse_docx(path):
    recs, cur = [], None
    texts = []
    for kind, v in docx_rows(path):
        if kind == "p":
            texts.append(v)
            continue
        cells = v
        if not cells:
            continue
        first = _norm(cells[0])
        allt = "\n".join(cells)
        texts.append(" | ".join(c for c in cells if c))
        if first.startswith("일시"):
            d = find_date(" ".join([cells[0]] + _row_vals(cells)))
            cur = {"d": d.isoformat() if d else None, "title": "", "who": "", "body": "", "note": ""}
            recs.append(cur)
            continue
        if cur is None:
            continue
        if first.startswith("업무명"):
            cur["title"] = re.sub(r"^\s*업\s*무\s*명\s*[:：]?\s*", "", cells[0]).strip() or " ".join(_row_vals(cells))
        elif first.startswith("담당자"):
            cur["who"] = re.sub(r"^\s*담\s*당\s*자\s*[:：]?\s*", "", cells[0]).strip() or " ".join(_row_vals(cells))
        elif first.startswith("업무내용"):
            cur["body"] += ("\n" if cur["body"] else "") + "\n".join(_row_vals(cells))
        elif first.startswith("비고") or first.startswith("특이사항"):
            cur["note"] += ("\n" if cur["note"] else "") + "\n".join(_row_vals(cells))
        else:
            vals = [c for c in cells if c and not _APPROVAL.match(_norm(c))]
            j = "\n".join(vals)
            if re.match(r"^\s*기\s*타\s*[:：]", j):                 # 남기천 양식 '기 타:' 칸
                cur["note"] += ("\n" if cur["note"] else "") + re.sub(r"^\s*기\s*타\s*[:：]\s*", "", j)
    return recs, "\n".join(texts)


# ───────────── 한글(.hwp) ─────────────
def hwp_plain(path):
    from . import etl_banagong as B
    return B.hwp_text(path)


def parse_hwp_text(text):
    text = (text or "").encode("utf-8", "replace").decode("utf-8", "replace")
    parts = re.split(r"\n[ \t]*일\s*시\s*[:：]", "\n" + text)
    recs = []
    for b in parts[1:]:
        d = find_date(b[:80])
        m_t = re.search(r"업\s*무\s*명\s*[:：]\s*(.+)", b)
        m_w = re.search(r"담\s*당\s*자\s*[:：]\s*(.+)", b)
        # 내용: '업\n무\n내\n용' 뒤 ~ '비고' 앞, 비고: 그 뒤
        m_b = re.search(r"\n\s*업\s*\n+\s*무\s*\n+\s*내\s*\n+\s*용\s*\n", b) or re.search(r"업\s*무\s*내\s*용", b)
        body_start = m_b.end() if m_b else (m_w.end() if m_w else 0)
        rest = b[body_start:]
        m_n = re.search(r"\n\s*비\s*\n*\s*고\s*\n", rest)
        body, note = (rest[:m_n.start()], rest[m_n.end():]) if m_n else (rest, "")
        note = re.split(r"\n\s*업\s*무\s*보\s*고\s*서\s*\n", note)[0]
        body = re.split(r"\n\s*업\s*무\s*보\s*고\s*서\s*\n", body)[0]
        recs.append({"d": d.isoformat() if d else None,
                     "title": (m_t.group(1).strip() if m_t else ""),
                     "who": re.sub(r"\s+", "", m_w.group(1)) if m_w else "",
                     "body": body, "note": note})
    return recs


# ───────────── 엑셀 주간 업무일지 ─────────────
_WD = "월화수목금토일"
_DAYCELL = re.compile(r"^\s*([월화수목금토일])요일\s*(\d{1,2})\s*/\s*(\d{1,2})")


def _year_for(wd, m, d, hint):
    """요일이 맞는 연도 (hint 에 가까운 것). 없으면 None."""
    best = None
    for y in range(hint - 3, hint + 2):
        try:
            x = dt.date(y, m, d)
        except ValueError:
            continue
        if _WD[x.weekday()] == wd and (best is None or abs(y - hint) < abs(best - hint)):
            best = y
    return best


def parse_xlsx_daily(path, who):
    """반가공 부장 일일업무보고서(하루 한 파일, '보고일자'·'작성일자' 칸이 있는 양식). 다른 양식이면 []."""
    import openpyxl
    from . import etl_banagong as B
    wb = openpyxl.load_workbook(path, data_only=True)
    if not any("일일업무보고서" in ws.title.replace(" ", "") for ws in wb.worksheets):
        return []
    d = by = None
    role = ""
    body = []
    for ws in wb.worksheets:
        if "대시보드" in ws.title or "누적" in ws.title:
            continue
        d1, by1, lines = B._kbj_sheet(B.Sheet(ws))
        d, by = d or d1, by or by1
        for r in range(1, min(ws.max_row, 8) + 1):               # '부서 / 직급: 반가공 / 부장'
            for c in range(1, min(ws.max_column, 12) + 1):
                m = re.search(r"직급\s*[:：]\s*(.+)$", str(ws.cell(r, c).value or ""))
                if m and not role:
                    role = m.group(1).split("/")[-1].strip()
        body += [("  " if l.get("sub") else "") + l["t"] for l in lines]
    if d is None:
        m = re.match(r"(\d{2})(\d{2})(\d{2})", os.path.basename(path))
        try:
            d = dt.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        except ValueError:
            d = None
    if d is None or not body:
        return []
    name = who or by or ""
    return [{"d": d.isoformat(), "title": "반가공 일일업무보고서", "who": (name + " " + role).strip(), "body": "\n".join(body), "note": ""}]


def parse_xlsx(path, who):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    recs = {}
    this_year = dt.date.today().year
    for ws in wb.worksheets:
        m = re.search(r"(\d{2})\s*년", ws.title)
        hint = 2000 + int(m.group(1)) if m else this_year
        rows = list(ws.iter_rows(values_only=True))
        ncol = max((len(r) for r in rows), default=0)
        for off in range(0, ncol, 8):              # 한 장에 주(週)를 옆으로 여러 개 둔 양식(A·I·Q열)
            cur = None
            for r in rows:
                cell = lambda k: (str(r[off + k]).strip() if off + k < len(r) and r[off + k] not in (None, "") else "")
                a = cell(0)
                if a:
                    mm = _DAYCELL.match(a)
                    if mm:
                        wd, mo, da = mm.group(1), int(mm.group(2)), int(mm.group(3))
                        y = _year_for(wd, mo, da, hint)
                        cur = dt.date(y, mo, da).isoformat() if y else None
                    elif re.match(r"^\s*[월화수목금토일]요일\s*$", a) or "요약" in a or "업무일지" in a or "업무내용" in a:
                        cur = None
                        continue
                if not cur:
                    continue
                b = cell(1)
                f = " ".join(x for x in (cell(k) for k in range(2, 7)) if x)
                if not b and not f:
                    continue
                t = b + (f" → {f}" if f and b else f)
                recs.setdefault(cur, []).append(t)
    out = []
    for d, ls in sorted(recs.items()):
        out.append({"d": d, "title": "주간 업무일지", "who": who, "body": "\n".join(ls), "note": ""})
    return out


# ───────────── 파일 → 기록 ─────────────
def base_dir():
    return os.path.join(C.DATA_DIR, "기타일지", "업무일지")


def extra_dirs():
    """업무일지 폴더 밖에 있는 개인 보고서 폴더 → [(폴더, 담당자)]. 지금은 반가공자료\\일일업무보고서(이름)."""
    out = []
    for src in getattr(C, "BANAGONG_SRC", []):
        for p in sorted(glob.glob(os.path.join(src, "일일업무보고서*"))):
            m = re.search(r"[(（]\s*([^)）]+?)\s*[)）]", os.path.basename(p))
            if os.path.isdir(p) and m:
                out.append((p, m.group(1)))
    return out


BANAGONG_FILE = "반가공/일일 업무보고서 (반가공 현황판이 읽은 것)"


def banagong_recs():
    """반가공 현황판 캐시의 일일 업무보고서 → (기록 목록, 캐시 파일 수정시각). 부장 보고서처럼 폴더에서 따로 읽는 것('file' 있음)은 뺀다."""
    p = os.path.join(getattr(C, "BANAGONG_CACHE", ""), "reports__all.json")
    try:
        with open(p, encoding="utf-8") as f:
            reps = json.load(f).get("reports") or []
        mt = int(os.path.getmtime(p))
    except Exception:
        return [], 0
    out = []
    for r in reps:
        if r.get("file") or not r.get("d"):
            continue
        lines = []
        for l in r.get("lines") or []:
            t = clean_text(str(l.get("t") or "").strip())
            if not t or _DROP.search(t):
                continue
            lines.append({"t": t, "sub": bool(l.get("sub")), "tags": tag_line(t)})
        if sum(len(l["t"]) for l in lines) < 4:
            continue
        out.append({"d": r["d"], "who": re.sub(r"\s+", "", r.get("by") or "") or "반가공", "role": "", "title": "반가공 일일 업무보고서",
                    "lines": lines, "note": [], "file": BANAGONG_FILE})
    return out, mt


def _person(rel):
    parts = rel.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def parse_file(path, rel):
    """→ {'recs': [...], 'doc': {...} 또는 None, 'err': ''}"""
    ext = os.path.splitext(path)[1].lower()
    person = _person(rel)
    recs, text = [], ""
    if ext == ".docx":
        recs, text = parse_docx(path)
    elif ext == ".hwp":
        text = hwp_plain(path)
        recs = parse_hwp_text(text)
    elif ext in (".xlsx", ".xlsm"):
        recs = parse_xlsx_daily(path, person) or parse_xlsx(path, person)
    out = []
    for r in recs:
        if not r.get("d"):
            continue
        who = re.sub(r"\s+", " ", r.get("who") or "").strip()
        compact = who.replace(" ", "")
        if person and person in compact:                 # 폴더 이름(담당자)이 기준. 직급은 앞뒤 나머지
            name, role = person, compact.replace(person, " ").strip()
        else:
            name = re.sub(r"\s*(팀장|대리|사원|과장|부장|이사|주임|실장|차장|수석연구원|연구원)\s*$", "", who).replace(" ", "")
            role = compact[len(name):] if name and compact.startswith(name) else ""
        lines = to_lines(r.get("body"))
        note = to_lines(r.get("note"))
        if sum(len(l["t"]) for l in lines + note) < 4:
            continue
        out.append({"d": r["d"], "who": name or person, "role": role,
                    "title": clean_text(r.get("title") or "")[:40], "lines": lines, "note": note})
    doc = None
    if not out and text.strip():                     # 날짜 없는 문서 (시험결과 보고서 등)
        body = [l for l in to_lines(text) if len(l["t"]) > 1][:60]
        if sum(len(l["t"]) for l in body) >= 40:
            doc = {"who": person, "title": os.path.splitext(os.path.basename(path))[0], "lines": body}
    return {"recs": out, "doc": doc}


def run(progress=print, force=False):
    base = base_dir()
    if not os.path.isdir(base):
        progress(f"업무일지 사본 폴더 없음: {base}")
        return 0
    try:
        with open(FILES, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if cache.get("_rules") != RULES or force:
        cache = {"_rules": RULES}
    seen, n_read, errs = set(), 0, []
    todo = [(p, os.path.relpath(p, base).replace("\\", "/")) for p in sorted(glob.glob(os.path.join(base, "**", "*"), recursive=True))]
    for folder, person in extra_dirs():          # 반가공자료\일일업무보고서(김병준)\2611\… → '김병준/반가공/2611/…'
        todo += [(p, person + "/반가공/" + os.path.relpath(p, folder).replace("\\", "/"))
                 for p in sorted(glob.glob(os.path.join(folder, "**", "*"), recursive=True))]
    for p, rel in todo:
        if not os.path.isfile(p) or os.path.splitext(p)[1].lower() not in EXTS:
            continue
        if "/" not in rel:                       # 맨 위 낱개 파일(빈 양식 '업무보고서.hwp')은 담당자를 몰라 뺀다
            continue
        if "#recycle" in rel or SKIP_NAME.search(os.path.basename(p)) or os.path.getsize(p) == 0:
            continue
        seen.add(rel)
        st_ = os.stat(p)
        key = f"{int(st_.st_mtime)}:{st_.st_size}"
        old = cache.get(rel)
        if old and old.get("key") == key:
            continue
        try:
            res = parse_file(p, rel)
            res["err"] = ""
        except Exception as e:
            res = {"recs": [], "doc": None, "err": f"{type(e).__name__}: {e}"[:200]}
            errs.append(f"{rel}: {res['err']}")
        res["key"], res["mtime"] = key, int(st_.st_mtime)
        cache[rel] = res
        n_read += 1
    for k in [k for k in cache if k != "_rules" and k not in seen]:
        del cache[k]
    os.makedirs(C.CACHE_DIR, exist_ok=True)
    with open(FILES, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    build(cache)
    for e in errs[:5]:
        progress("  못 읽음 " + e)
    return n_read


def build(cache):
    """파일별 결과 → 화면용 묶음. 같은 사람·같은 날짜가 여러 파일에 있으면 가장 최근에 고친 파일 것을 쓴다
    (월 파일에 다음 달 날짜를 이어 쓰거나 '복사본'이 있는 경우). 같은 파일 안 같은 날짜 두 번은 둘 다 둔다."""
    best = {}
    for rel, res in cache.items():
        if rel == "_rules":
            continue
        for r in res.get("recs", []):
            k = (r["who"], r["d"])
            if k not in best or res["mtime"] > best[k][0]:
                best[k] = (res["mtime"], rel)
    today = dt.date.today().isoformat()
    recs, docs, files = [], [], []
    for rel, res in sorted(cache.items()):
        if rel == "_rules":
            continue
        used = 0
        for r in res.get("recs", []):
            if best[(r["who"], r["d"])][1] != rel or r["d"] > today:
                continue
            recs.append({**r, "file": rel})
            used += 1
        if res.get("doc"):
            docs.append({**res["doc"], "file": rel, "mtime": res["mtime"]})
        files.append({"file": rel, "who": _person(rel), "n": len(res.get("recs", [])), "used": used,
                      "mtime": res["mtime"], "err": res.get("err", ""), "doc": bool(res.get("doc"))})
    b_recs, b_mt = banagong_recs()                       # 반가공 일일 업무보고서(현황판 캐시) — 같은 사람·같은 날이 이미 있으면 넣지 않음
    have = {(r["who"], r["d"]) for r in recs}
    b_recs = [r for r in b_recs if r["d"] <= today and (r["who"], r["d"]) not in have]
    if b_recs:
        recs += b_recs
        files.append({"file": BANAGONG_FILE, "who": "·".join(sorted({r["who"] for r in b_recs})), "n": len(b_recs), "used": len(b_recs),
                      "mtime": b_mt, "err": "", "doc": False})
    # 매일 똑같이 적는 줄(양식 문구: '접종 불량 점검 수,토 …')은 rep 표시 → 화면에서 접어 두고 '이슈'로 세지 않는다
    by_who = {}
    for r in recs:
        by_who.setdefault(r["who"], []).append(r)
    for who, rs in by_who.items():
        cnt = {}
        for r in rs:
            for k in {_key(l["t"]) for l in r["lines"] + r["note"]}:
                cnt[k] = cnt.get(k, 0) + 1
        lim = max(5, 0.3 * len(rs))
        for r in rs:
            for l in r["lines"] + r["note"]:
                if cnt.get(_key(l["t"]), 0) >= lim:
                    l["rep"] = True
                    l["tags"] = [t for t in l["tags"] if t != "이슈"]
    recs.sort(key=lambda r: (r["d"], r["who"]), reverse=True)
    data = {"updated": time.strftime("%Y-%m-%d %H:%M"), "base": base_dir(), "recs": recs, "docs": docs, "files": files}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def _key(t):
    return re.sub(r"[\s\d:./~()\-–,]+", "", t)


def load():
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"recs": [], "docs": [], "files": []}
