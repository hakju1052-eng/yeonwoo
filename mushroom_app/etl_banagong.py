# -*- coding: utf-8 -*-
"""반가공센터 현황 ETL — 반가공 업무자료 사본을 읽어 '반가공 현황' 화면용 JSON 문서를 만든다.

원본(사본 폴더, 기본 Downloads\\반가공자료 — copy_banagong.bat 이 Y:/\\jinsung\\반가공\\업무자료 에서 복사):
  생산출고일지및 원재료수불\\*가공일지.xlsx   제품별 가공일지 (2026년 시트)
  거래명세서\\거래명세표.xlsx                 매출(명세서 시트별)
  구매요청서\\결재요청서.xlsx                 구매 결재
  부자재및부재료 수불부\\부재료·부자재수불부.xlsx
  가공 근로자\\*근무일지*.xlsx, 추가수당*.xlsx;  근무조 편성 및 작업지시서\\*.xlsx
  보일러가동일지\\*.xlsx;  일일 업무보고서.hwp (~2026-10-06, olefile 필요 — 없으면 자동 설치 시도)
  반가공일일업무보고서2026.10월~\\반가공 일일업무보고서.xlsx (2026-10-07~, 하루 한 시트)

출력: CACHE_DIR\\banagong\\<collection>__<doc>.json
  meta__info, prod__summary, prod__daily, sales__all, purchase__all, stock__ledgers, labor__all, equip__boiler, reports__all
명령줄:  python -m mushroom_app.etl_banagong            (config 의 BANAGONG_SRC → BANAGONG_CACHE)
        python -m mushroom_app.etl_banagong <원본폴더> <출력폴더> [기준일 YYYY-MM-DD]
원본 파일은 읽기만 하고 고치지 않는다. 한 자료가 실패해도 나머지는 만든다(실패 내용은 meta__info 의 errors).
"""
import os, re, sys, json, glob, struct, zlib, datetime as dt, warnings
import openpyxl
from openpyxl.utils import get_column_letter

warnings.filterwarnings('ignore')

# ───────────────────────── 공통 ─────────────────────────
def norm(s):
    return re.sub(r'\s+', '', str(s)) if s is not None else ''

def num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        t = v.replace(',', '').strip()
        if re.fullmatch(r'-?\d+(\.\d+)?', t):
            return float(t)
    return None

def r2(x):
    if x is None:
        return None
    x = round(float(x), 2)
    return int(x) if x == int(x) else x

def ymd(d):
    return d.strftime('%Y-%m-%d')

def ym(d):
    return d.strftime('%Y-%m')

def mtime(path):
    return dt.datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M')

class Sheet:
    """병합 셀을 펼쳐 읽는 시트 래퍼 (data_only)."""
    def __init__(self, ws):
        self.ws = ws
        self.max_row = ws.max_row
        self.max_col = ws.max_column
        self.merged = {}
        for rng in ws.merged_cells.ranges:
            tl = (rng.min_row, rng.min_col)
            for r in range(rng.min_row, rng.max_row + 1):
                for c in range(rng.min_col, rng.max_col + 1):
                    self.merged[(r, c)] = tl

    def raw(self, r, c):
        return self.ws.cell(r, c).value

    def val(self, r, c):
        tl = self.merged.get((r, c))
        if tl:
            return self.ws.cell(*tl).value
        return self.ws.cell(r, c).value

def load(path):
    return openpyxl.load_workbook(path, data_only=True)

def parse_date_cell(v, year=None, month=None):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and year:
        m = re.match(r'\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', v)
        if m:
            try:
                return dt.date(year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                return None
    if isinstance(v, (int, float)) and 30000 < v < 60000:  # 엑셀 일련번호
        return (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(v))).date()
    return None

# ───────────────────────── 1. 가공일지 ─────────────────────────
PRODUCTS = {
    'crunteen': {'name': '크런틴', 'unit': '봉', 'kind': '감자칩'},
    'homi':     {'name': '호미칩', 'unit': '봉', 'kind': '고구마칩'},
    'dalkom':   {'name': '달콤바삭 고구마칩', 'unit': '봉', 'kind': '고구마칩'},
    'mattang':  {'name': '생고구맛탕칩', 'unit': '봉', 'kind': '고구마칩'},
    'stick':    {'name': '고구마스틱', 'unit': '', 'kind': '고구마 반가공'},
    'cube':     {'name': '허니맛탕큐브', 'unit': '', 'kind': '고구마 반가공'},
    'costino':  {'name': '코스티노 올리브유 감자칩', 'unit': '봉', 'kind': '감자칩'},   # 2026-10 새 제품(올리브유 감자칩 위탁생산)
}

def product_of_title(title):
    t = norm(title)
    if '코스티노' in t or '올리브유감자칩' in t: return 'costino'
    if '크런틴' in t: return 'crunteen'
    if '호미칩' in t: return 'homi'
    if '달콤바삭' in t: return 'dalkom'
    if '생고구맛탕칩' in t: return 'mattang'
    if '큐브' in t: return 'cube'
    if '스틱' in t or '스위틱' in t: return 'stick'
    return None

FLAVOR_ALIAS = {'스모키머스타드': '스모키머스터드'}

def clean_variant(v):
    v = norm(v)
    return FLAVOR_ALIAS.get(v, v)

def is_variant_label(t):
    t = norm(t)
    if not t:
        return False
    return not re.search(r'개입|box|kg|^\(|수량|소비기한|유통기한|작업일자|^\d+$', t, re.I)

def classify(path):
    """헤더 경로(위→아래) → (metric, variant) 또는 None."""
    p = []
    for x in path:
        if isinstance(x, str) and norm(x):
            n = norm(x)
            if not p or p[-1] != n:
                p.append(n)
    if not p:
        return None
    # 크런틴식 그룹(생산/출고/재고 → 맛)
    if p[0] in ('생산(포장)', '생산', '출고', '재고') and len(p) >= 2 and is_variant_label(p[1]):
        if p[0] == '재고' and p[1] in ('연우보유', '한성보유'):
            pass
        else:
            metric = {'생산(포장)': 'pack', '생산': 'pack', '출고': 'ship', '재고': 'stock'}[p[0]]
            sub = p[2] if len(p) > 2 else ''
            if '기한' in sub:
                return None
            return (metric, clean_variant(p[1]))
    if p[0] in ('출고',) and len(p) >= 2 and norm(p[1]) == '작업일자':
        return ('shipdate', None)
    in_gagong = False
    if p[0] == '가공':
        in_gagong = True
        p = p[1:]
        if not p:
            return None
    if in_gagong and p[0] in ('재고', '감자재고') and len(p) == 1:
        return ('rawStockY', None)
    variant = None
    if len(p) >= 2 and ('고구마칩' in p[0] or '달콤바삭' in p[0]):
        variant = '치즈맛' if '치즈' in p[0] else '기본'
        p = p[1:]
    h = p[0]
    s = p[1] if len(p) > 1 else ''
    if '기한' in h:
        return None
    if h in ('입고일자', '작업일자', '날짜', '일자'):
        return ('date', None)
    if '비고' in h:
        return ('note', None)
    if '파렛트' in h:
        return ('pallet', None)
    if '전처리' in h and '반제품' in h:      # 코스티노: '전처리 반제품'(전처리해서 만든 반제품 kg) — 사용량과 따로
        return ('semiMade', None)
    if '반제품' in h and '재고' in h:        # 코스티노: '재고(반제품)'
        return ('semi', None)
    if '전처리' in h:
        return ('preUse', None)
    if '가공횟수' in h:
        return ('batches', None)
    if '가공사용량' in h or h == '가공사용량(kg)':
        return ('procUse', None)
    if '경유' in h:
        return ('fuel', None)
    if ('유' in h and '입고' in h) and any(k in h for k in ('현미유', '팜올레인', '올리브', '식용유', '대두유')):
        return ('oilIn', None)
    if '사용' in h and any(k in h for k in ('현미유', '팜올레인', '올리브', '식용유', '대두유')):
        return ('oilUse', None)
    if '입고' in h and any(k in h for k in ('고구마', '감자', '원물')):
        return ('rawIn', None)
    if '반제품' in h:
        return ('semi', None)
    if h in ('재고', '감자재고') and s in ('연우보유',):
        return ('rawStockY', None)
    if h in ('재고', '감자재고') and s in ('한성보유',):
        return ('rawStockH', None)
    if h == '감자재고':
        return ('rawStockY', None)
    if h == '수율':
        return {'투입량': ('yIn', None), '유탕후': ('yOut', None), 'GOOD': ('yGood', None),
                'BAD': ('yBad', None)}.get(s.upper() if s.upper() in ('GOOD', 'BAD') else s)
    if h == '작업시간':
        return {'시작': ('tStart', None), '종료': ('tEnd', None)}.get(s)
    if '총근무시간' in h:
        return ('workHours', None)
    if '근무인원' in h:
        return ('workers', None)
    if '포장량' in h:
        return ('packBox', variant)
    if '포장개수' in h or re.match(r'^포장\(', h):
        return ('pack', variant)
    if h.startswith('출고'):
        return ('ship', variant)
    if h.startswith('재고'):
        return ('stock', variant)
    return None

SUM_METRICS = ('rawIn', 'preUse', 'semiMade', 'batches', 'procUse', 'oilIn', 'oilUse', 'fuel',
               'packBox', 'pack', 'ship', 'workHours', 'yIn', 'yOut', 'yGood', 'yBad')

def parse_log_sheet(sh, yymm):
    """한 달치 가공일지 시트 → dict(rows, carry)."""
    # 헤더 행 찾기: A열에 입고일자/작업일자
    hr = None
    for r in range(1, 8):
        a = norm(sh.val(r, 1))
        if a in ('입고일자', '작업일자', '가공'):
            hr = r
            break
    if hr is None:
        return None
    # 그룹 헤더(가공/생산(포장)...)가 있으면 3단, 아니면 최대 3단
    hdr_rows = [hr, hr + 1, hr + 2]
    # 데이터 시작 행
    start = None
    for r in range(hr + 1, min(hr + 6, sh.max_row) + 1):
        a = sh.raw(r, 1)
        if isinstance(a, (dt.datetime, dt.date)) or norm(a) in ('이월', '전월재고'):
            start = r
            break
    if start is None:
        return None
    hdr_rows = [x for x in hdr_rows if x < start]
    cols = {}
    for c in range(1, sh.max_col + 1):
        path = [sh.val(r, c) for r in hdr_rows]
        if c == 1:
            cols[c] = ('date', None)
            continue
        k = classify(path)
        if k:
            cols[c] = k
    year = 2000 + int(yymm[:2])
    month = int(yymm[2:])
    rows, carry = [], {}
    prev_d = None
    for r in range(start, sh.max_row + 1):
        a = sh.raw(r, 1)
        an = norm(a)
        if an in ('총계', '합계', '계'):
            break
        if an in ('이월', '전월재고'):
            for c, (m, v) in cols.items():
                x = num(sh.raw(r, c))
                if x is not None and m in ('stock', 'rawStockY', 'rawStockH', 'oilIn', 'semi'):
                    key = m if v is None else f'{m}|{v}'
                    carry[key] = x
            continue
        d = parse_date_cell(a, year)
        if d is None and a in (None, '') and prev_d is not None:
            # 날짜 칸이 빈 행: 바로 앞 날짜의 다음 날로 본다 (같은 달 안에서만)
            nd = prev_d + dt.timedelta(days=1)
            has_data = any(num(sh.raw(r, c)) for c in cols if cols[c][0] in SUM_METRICS)
            if nd.month == month and has_data:
                d = nd
        if d is None:
            continue
        prev_d = d
        rec = {'d': d}
        for c, (m, v) in cols.items():
            if m in ('date', 'shipdate'):
                continue
            x = sh.raw(r, c)
            if m in ('note', 'tStart', 'tEnd'):
                if x not in (None, '') and not isinstance(x, (int, float)) or (m == 'note' and isinstance(x, (int, float))):
                    rec[m] = str(x).strip()
                continue
            if m == 'workers':
                if isinstance(x, str):
                    mm = re.search(r'(\d+)', x)
                    x = float(mm.group(1)) if mm else None
                else:
                    x = num(x)
                if x is not None:
                    rec['workers'] = x
                continue
            x = num(x)
            if x is None:
                continue
            key = m if v is None else f'{m}|{v}'
            if m in SUM_METRICS:
                rec[key] = rec.get(key, 0) + x
            else:  # stock 계열은 마지막 값
                rec[key] = x
        rows.append(rec)
    return {'rows': rows, 'carry': carry}

def parse_production(src):
    folder = os.path.join(src, '생산출고일지및 원재료수불')
    found = {}  # (product, yymm) -> (mtime, path, sheet)
    for path in sorted(glob.glob(os.path.join(folder, '*.xlsx'))):
        base = os.path.basename(path)
        if base.startswith('~$') or '원가' in base:
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sn in wb.sheetnames:
            m = re.search(r'\((\d{4})\)', sn)
            if not m or not m.group(1).startswith('26'):
                continue
            ws = wb[sn]
            title = None
            for row in ws.iter_rows(min_row=1, max_row=2, max_col=3):
                for cell in row:
                    v = getattr(cell, 'value', None)
                    if isinstance(v, str) and v.strip():
                        title = v.strip()
                        break
                if title:
                    break
            p = product_of_title(title or '')
            if not p:
                continue
            key = (p, m.group(1))
            mt = os.path.getmtime(path)
            if key not in found or found[key][0] < mt:
                found[key] = (mt, path, sn, title)
        wb.close()
    cache = {}
    products = {}
    files_used = {}
    for (p, yymm), (mt, path, sn, title) in sorted(found.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if path not in cache:
            cache[path] = load(path)
        sh = Sheet(cache[path][sn])
        res = parse_log_sheet(sh, yymm)
        if not res:
            continue
        files_used[os.path.basename(path)] = mtime(path)
        prod = products.setdefault(p, {'months': {}, 'rows': [], 'carry': {}, 'variants': set()})
        prod['months'][yymm] = res
        for rec in res['rows']:
            prod['rows'].append(rec)
            for k in rec:
                if '|' in k:
                    prod['variants'].add(k.split('|', 1)[1])
    return products, files_used

def build_production(products, today):
    """제품별 월 합계·일별 활동·재고(기록값 우선, 없으면 포장-출고 누계)."""
    out = []
    daily = []
    raw_stock = {}
    for key in ['crunteen', 'costino', 'homi', 'dalkom', 'mattang', 'stick', 'cube']:
        if key not in products:
            continue
        pr = products[key]
        meta = PRODUCTS[key]
        months = {}
        last_activity = None
        stock_run, stock_date, stock_basis = {}, {}, {}
        semi = None
        for yymm in sorted(pr['months']):
            res = pr['months'][yymm]
            mkey = f'20{yymm[:2]}-{yymm[2:]}'
            agg = {}
            for k, v in res['carry'].items():
                m, _, var = k.partition('|')
                var = var or None
                if m == 'stock':
                    stock_run[var] = v
                    stock_date[var] = f'{mkey}-01'
                    stock_basis[var] = '이월'
                if m in ('rawStockY', 'rawStockH') and key == 'crunteen':
                    raw_stock[m] = {'v': r2(v), 'd': f'{mkey}-01'}
            for rec in res['rows']:
                d = rec['d']
                if d > today:
                    continue
                active = False
                for k, v in rec.items():
                    if k in ('d', 'note', 'tStart', 'tEnd', 'workers'):
                        continue
                    m, _, var = k.partition('|')
                    if m in SUM_METRICS:
                        agg[k] = agg.get(k, 0) + v
                        if m != 'workHours' and v:
                            active = True
                if rec.get('workers'):
                    agg['workerDays'] = agg.get('workerDays', 0) + rec['workers']
                if 'semi' in rec:
                    semi = {'v': r2(rec['semi']), 'd': ymd(d)}
                # 재고: 같은 행에 기록값이 있으면 그 값(당일 입출고 반영 후), 없으면 포장-출고 누계
                vlist = set()
                for k in rec:
                    m, _, var = k.partition('|')
                    if m in ('pack', 'ship', 'stock'):
                        vlist.add(var or None)
                for var in vlist:
                    sfx = '' if var is None else '|' + var
                    if 'stock' + sfx in rec:
                        stock_run[var] = rec['stock' + sfx]
                        stock_basis[var] = '기록'
                        stock_date[var] = ymd(d)
                    elif rec.get('pack' + sfx) or rec.get('ship' + sfx):
                        stock_run[var] = stock_run.get(var, 0) + rec.get('pack' + sfx, 0) - rec.get('ship' + sfx, 0)
                        stock_basis[var] = '계산' if var not in stock_basis or stock_basis[var] != '기록' else '기록+계산'
                        stock_date[var] = ymd(d)
                if key == 'crunteen':
                    for m in ('rawStockY', 'rawStockH'):
                        if m in rec:
                            raw_stock[m] = {'v': r2(rec[m]), 'd': ymd(d)}
                if active:
                    last_activity = ymd(d)
                    row = {'d': ymd(d), 'p': key}
                    for k, v in rec.items():
                        if k == 'd':
                            continue
                        m, _, var = k.partition('|')
                        if m in ('stock', 'rawStockY', 'rawStockH', 'semi'):
                            continue
                        row[k] = r2(v) if isinstance(v, float) else v
                    daily.append(row)
            # 포장개수 칸이 비고 포장량(box) 칸에 낱개 수를 적은 달 보정
            packs = sum(v for k, v in agg.items() if k.split('|')[0] == 'pack')
            boxes = sum(v for k, v in agg.items() if k.split('|')[0] == 'packBox')
            ships = sum(v for k, v in agg.items() if k.split('|')[0] == 'ship')
            if not packs and boxes and ships and boxes >= 0.5 * ships:
                agg['pack'] = boxes
                agg.pop('packBox', None)
                agg['packFromBox'] = 1
                for row in daily:
                    if row['p'] == key and row['d'][:7] == mkey and 'packBox' in row:
                        row['pack'] = row.pop('packBox')
            mo = {k: r2(v) for k, v in agg.items()}
            if mo:
                months[mkey] = mo
        # 재고는 마지막 활동 달에 갱신된 값만 보고 (오래된 값·종류 제외)
        stock = {}
        last_ym = (last_activity or '')[:7]
        for var, v in stock_run.items():
            sd = stock_date.get(var) or ''
            if last_ym and sd[:7] != last_ym:
                continue
            stock[var or '전체'] = {'v': r2(v), 'd': sd, 'basis': stock_basis.get(var)}
        if len(stock) > 1 and '전체' in stock:
            stock.pop('전체')
        if semi and last_ym and semi['d'][:7] != last_ym:
            semi = None
        out.append({
            'key': key, 'name': meta['name'], 'kind': meta['kind'], 'unit': meta['unit'],
            'variants': sorted(v for v in pr['variants'] if is_variant_label(v)),
            'months': months, 'stock': stock, 'semi': semi, 'lastActivity': last_activity,
        })
    return out, daily, raw_stock


def crunteen_oil_stock(src, today):
    """크런틴 가공일지의 현미유 입고/사용 누계로 현미유 재고 추정 (하단 '재고' 행 우선)."""
    path = os.path.join(src, '생산출고일지및 원재료수불', '크런틴 가공일지.xlsx')
    if not os.path.exists(path):
        return None
    wb = load(path)
    best = None
    for sn in wb.sheetnames:
        m = re.search(r'\((\d{4})\)', sn)
        if not m:
            continue
        ws = wb[sn]
        for r in range(1, ws.max_row + 1):
            for c in range(1, 6):
                if norm(ws.cell(r, c).value) == '재고':
                    x = num(ws.cell(r, c + 1).value)
                    if x is not None and r > 30:
                        cand = (m.group(1), x)
                        if best is None or cand[0] > best[0]:
                            best = cand
    if best:
        return {'v': r2(best[1]), 'ym': f'20{best[0][:2]}-{best[0][2:]}'}
    return None

# ───────────────────────── 2. 거래명세표(매출) ─────────────────────────
def product_of_item(name):
    t = norm(name)
    # '크런틴 허니맛탕큐브'처럼 브랜드명이 붙은 품목이 있어 구체적인 제품명부터 본다
    if '큐브' in t: return 'cube'
    if '스틱' in t or '스위틱' in t: return 'stick'
    if '호미칩' in t: return 'homi'
    if '달콤바삭' in t: return 'dalkom'
    if '맛탕칩' in t or '생고구맛탕' in t: return 'mattang'
    if '크런틴' in t: return 'crunteen'
    if '스윗' in t or '순고칩' in t: return 'sweet'
    if any(k in t for k in ('운임', '운송', '배송', '택배', '파레트', '파렛트', '가구', '박스', '팔레트', '콘티')): return 'etc'
    return 'other'

FLAVORS = ['오리지널', '청양마요', '고추장허니', '스모키머스터드', '핫치폴레']
CUSTOMER_ALIAS = {'공덕': '공덕농협', '주식회사 지구대': '지구대'}

def flavor_of_item(name):
    t = norm(name).replace('스모키머스타드', '스모키머스터드')
    if '치즈' in t:
        return '치즈맛'
    for f in FLAVORS:
        if f in t:
            return f
    return None

def item_display(name):
    s = re.sub(r'\(\s*\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}[^)]*\)', '', str(name))
    return re.sub(r'\s+', ' ', s).strip()

def parse_sales(src):
    path = os.path.join(src, '거래명세서', '거래명세표.xlsx')
    if not os.path.exists(path):
        return None
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    statements = []
    for sn in wb.sheetnames:
        ws = wb[sn]
        grid = []
        for row in ws.iter_rows(min_row=1, max_row=24, max_col=32, values_only=True):
            grid.append(list(row))
        # 날짜·거래처
        date = cust = None
        for r in range(min(12, len(grid))):
            for c, v in enumerate(grid[r]):
                if isinstance(v, (dt.datetime, dt.date)):
                    date = v.date() if isinstance(v, dt.datetime) else v
                    for c2 in range(c + 1, len(grid[r])):
                        if isinstance(grid[r][c2], str) and grid[r][c2].strip():
                            cust = grid[r][c2].strip()
                            break
                    break
            if date:
                break
        if not date:
            continue
        # 품목 헤더
        hdr = None
        for r in range(len(grid)):
            texts = [norm(v) for v in grid[r]]
            if '품목' in texts and any('수량' in t for t in texts):
                hdr = r
                break
        if hdr is None:
            continue
        cmap = {}
        for c, v in enumerate(grid[hdr]):
            t = norm(v)
            if t == '품목': cmap['item'] = c
            elif t == '규격': cmap['spec'] = c
            elif '수량' in t: cmap['qty'] = c
            elif '단가' in t: cmap['price'] = c
            elif '공급가액' in t or t == '금액': cmap['amt'] = c
            elif '세액' in t: cmap['tax'] = c
            elif '비고' in t: cmap['note'] = c
        lines = []
        total = supply = tax = None
        for r in range(hdr + 1, len(grid)):
            row = grid[r]
            first = norm(row[0]) + norm(row[1] if len(row) > 1 else '')
            if first.startswith('합') or '합계' in first:
                # 합계 행: 첫 숫자=합계, '공급가액' 다음 숫자, '세액' 다음 숫자
                nums = []
                label = None
                for c, v in enumerate(row):
                    t = norm(v)
                    if t in ('공급가액', '세액'):
                        label = t
                        continue
                    x = num(v)
                    if x is not None:
                        nums.append((label, x))
                for lab, x in nums:
                    if lab is None and total is None: total = x
                    elif lab == '공급가액' and supply is None: supply = x
                    elif lab == '세액' and tax is None: tax = x
                break
            name = row[cmap['item']] if 'item' in cmap else None
            if (not isinstance(name, str) or not name.strip()) and 'item' in cmap:
                for c2 in range(cmap['item'] + 1, cmap.get('spec', cmap['item'] + 4)):
                    if c2 < len(row) and isinstance(row[c2], str) and row[c2].strip():
                        name = row[c2]
                        break
            if not isinstance(name, str) or not name.strip() or norm(name) in ('0',):
                continue
            qty = num(row[cmap['qty']]) if 'qty' in cmap else None
            price = num(row[cmap['price']]) if 'price' in cmap else None
            amt = num(row[cmap['amt']]) if 'amt' in cmap else None
            if amt is None and qty is not None and price is not None:
                amt = qty * price
            p = product_of_item(name)
            lines.append({'item': item_display(name), 'p': p, 'v': flavor_of_item(name),
                          'spec': r2(num(row[cmap['spec']])) if 'spec' in cmap and num(row[cmap['spec']]) is not None else (str(row[cmap['spec']]).strip() if 'spec' in cmap and row[cmap['spec']] not in (None, 0) else None),
                          'qty': r2(qty), 'price': r2(price), 'amt': r2(amt)})
        if not lines and not total:
            continue
        if supply is None:
            supply = sum(l['amt'] or 0 for l in lines)
        if total is None:
            total = supply + (tax or 0)
        site = None
        if cust:
            base = cust
            m = re.match(r'^(.*?)\s*\((.*)\)\s*$', base)
            if m:
                base, site = m.group(1).strip(), m.group(2).strip()
        else:
            base = sn.strip()
            for _ in range(4):
                nb = re.sub(r'\s*\([^()]*\)?\s*\d*\s*$', '', base).strip()
                if nb == base or not nb:
                    break
                base = nb
        base = CUSTOMER_ALIAS.get(base, base)
        statements.append({'d': ymd(date), 'cust': base, 'site': site, 'total': r2(total),
                           'supply': r2(supply), 'tax': r2(tax or 0), 'lines': lines, 'sheet': sn.strip()})
    wb.close()
    # 같은 날짜·거래처·금액·품목이 겹치는 시트는 하나만 (사본 시트 방지)
    seen = set()
    uniq = []
    for s in sorted(statements, key=lambda s: (s['d'], s['cust'], s['sheet'])):
        sig = (s['d'], s['cust'], s['site'], s['total'], tuple((l['item'], l['qty']) for l in s['lines']))
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(s)
    return {'statements': uniq, 'file': mtime(path)}

# ───────────────────────── 3. 결재요청서(구매) ─────────────────────────
VENDOR_ALIAS = [
    (r'(강원|감원)감자', '강원감자농협'),
    (r'대정포장', '유라케미칼'),
]

def norm_vendor(v):
    if not v:
        return None
    t = re.sub(r'\s+', ' ', str(v)).strip()
    for pat, rep_ in VENDOR_ALIAS:
        if re.search(pat, t):
            return rep_
    t = re.sub(r'^(㈜|\(주\)|주식회사)\s*', '', t)
    t = re.sub(r'\s*(㈜|\(주\)|주식회사)$', '', t)
    return t.strip() or None

def parse_purchases(src):
    path = os.path.join(src, '구매요청서', '결재요청서.xlsx')
    if not os.path.exists(path):
        return None
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    reqs = []
    for sn in wb.sheetnames:
        if sn.strip() in ('양식',):
            continue
        ws = wb[sn]
        grid = [list(r) for r in ws.iter_rows(min_row=1, max_row=30, max_col=24, values_only=True)]
        date = None
        for r in range(min(7, len(grid))):
            for v in grid[r]:
                if isinstance(v, (dt.datetime, dt.date)):
                    date = v.date() if isinstance(v, dt.datetime) else v
                    break
            if date:
                break
        if not date:
            continue
        dept = None
        for r in range(min(8, len(grid))):
            for c, v in enumerate(grid[r]):
                if norm(v) == '의뢰부서':
                    for c2 in range(c + 1, len(grid[r])):
                        if isinstance(grid[r][c2], str) and grid[r][c2].strip():
                            dept = grid[r][c2].strip()
                            break
        hdr = None
        for r in range(len(grid)):
            texts = [norm(v) for v in grid[r]]
            if '품목' in texts:
                hdr = r
                break
        if hdr is None:
            continue
        cmap = {}
        for c, v in enumerate(grid[hdr]):
            t = norm(v)
            if t == '품목': cmap['item'] = c
            elif t.startswith('규격'): cmap['spec'] = c
            elif t == '수량': cmap['qty'] = c
            elif t == '단위': cmap['unit'] = c
            elif t.startswith('단가'): cmap['price'] = c
            elif t.startswith('금액'): cmap['amt'] = c
            elif t == '세액': cmap['tax'] = c
            elif t == '계': cmap['sum'] = c
            elif t == '업체명': cmap['vendor'] = c
            elif t.startswith('비고'): cmap['note'] = c
        items = []
        total = None
        purpose = None
        for r in range(hdr + 1, len(grid)):
            row = grid[r]
            first = norm(row[cmap.get('item', 0)]) if row else ''
            rowtxt = ''.join(norm(v) for v in row[:3])
            if rowtxt.startswith('합계') or first.startswith('합계') or rowtxt in ('계',) or first == '계':
                for key in ('sum', 'amt'):
                    if key in cmap and num(row[cmap[key]]) is not None:
                        total = num(row[cmap[key]])
                        break
                if total is None:
                    xs = [num(v) for v in row if num(v) is not None]
                    total = xs[-1] if xs else None
                continue
            if '구매목적' in rowtxt:
                for r2_ in range(r, min(r + 3, len(grid))):
                    for v in grid[r2_]:
                        if isinstance(v, str) and len(v.strip()) > 4 and '구매 목적' not in v and '구매목적' not in norm(v):
                            purpose = v.strip().strip('"').strip()
                            break
                    if purpose:
                        break
                break
            name = row[cmap['item']] if 'item' in cmap else None
            if not isinstance(name, str) or not name.strip():
                continue
            if norm(name) in ('NEGO', '세액', '총배송비'):
                continue
            g = lambda k: row[cmap[k]] if k in cmap else None
            amt = num(g('amt'))
            sm = num(g('sum'))
            vendor = g('vendor')
            note = g('note')
            items.append({
                'name': re.sub(r'\s+', ' ', name).strip(),
                'spec': str(g('spec')).strip() if g('spec') not in (None, '') else None,
                'qty': r2(num(g('qty'))), 'unit': str(g('unit')).strip() if g('unit') not in (None, '') else None,
                'price': r2(num(g('price'))), 'amt': r2(amt), 'sum': r2(sm if sm is not None else amt),
                'vendor': norm_vendor(vendor) if isinstance(vendor, str) and vendor.strip() else None,
                'note': str(note).strip() if isinstance(note, str) and note.strip() else None,
            })
        if not items:
            continue
        if total is None:
            total = sum((i['sum'] or 0) for i in items)
        # 업체명이 비어 있으면 위 품목의 업체명을 이어 받지 않음 (명시된 것만)
        reqs.append({'d': ymd(date), 'dept': dept, 'total': r2(total), 'items': items,
                     'purpose': (purpose or '')[:400] or None, 'sheet': sn.strip()})
    wb.close()
    seen = set()
    uniq = []
    for q in sorted(reqs, key=lambda q: (q['d'], q['sheet'])):
        sig = (q['d'], q['total'], tuple(i['name'] for i in q['items']))
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(q)
    return {'requests': uniq, 'file': mtime(path)}

# ───────────────────────── 4. 수불부(부재료·부자재) ─────────────────────────
def parse_ledger(path, kind):
    wb = load(path)
    # 가장 최근 시트 (이름의 yymm 기준)
    best = None
    for sn in wb.sheetnames:
        m = re.search(r'\((\d{4})\)', sn)
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), sn)
    if not best:
        return None
    yymm, sn = best
    sh = Sheet(wb[sn])
    # '입고' 이 두 번 이상 나오는 행 = 세부 헤더
    sub_r = None
    for r in range(1, 8):
        vals = [norm(sh.raw(r, c)) for c in range(1, sh.max_col + 1)]
        if vals.count('입고') >= 2:
            sub_r = r
            break
    if sub_r is None:
        return None
    year = 2000 + int(yymm[:2])
    cols = {}
    for c in range(2, sh.max_col + 1):
        sub = norm(sh.raw(sub_r, c))
        if sub not in ('입고', '출고', '재고', '불량'):
            continue
        path_ = [norm(sh.val(r, c)) for r in range(1, sub_r)]
        path_ = [p for p in path_ if p and p not in ('날짜',)]
        cols[c] = (tuple(path_), sub)
    items = {}
    order = []
    last_date = None
    date_months = {}
    for c, (pth, sub) in cols.items():
        if pth not in items:
            items[pth] = {'in': 0, 'out': 0, 'bad': 0, 'carry': None, 'lastStock': None, 'lastStockDate': None}
            order.append(pth)
    for r in range(sub_r + 1, sh.max_row + 1):
        a = sh.raw(r, 1)
        an = norm(a)
        if an in ('계', '합계', '총계'):
            break
        is_carry = an in ('이월', '전월재고')
        d = None if is_carry else parse_date_cell(a, year)
        if not is_carry and d is None:
            continue
        if d is not None:
            k_ = f'{d.year}-{d.month:02d}'
            date_months[k_] = date_months.get(k_, 0) + 1
        any_entry = False
        for c, (pth, sub) in cols.items():
            x = num(sh.raw(r, c))
            if x is None:
                continue
            it = items[pth]
            if is_carry:
                if sub == '재고':
                    it['carry'] = x
                continue
            if sub == '입고': it['in'] += x; any_entry = any_entry or x != 0
            elif sub == '출고': it['out'] += x; any_entry = any_entry or x != 0
            elif sub == '불량': it['bad'] += x; any_entry = any_entry or x != 0
            elif sub == '재고':
                it['lastStock'] = x
                it['lastStockDate'] = ymd(d)
        if any_entry:
            last_date = ymd(d)
    out = []
    for pth in order:
        it = items[pth]
        name = pth[-1] if pth else '?'
        group = pth[0] if len(pth) > 1 else None
        product = pth[1] if len(pth) > 2 else None
        unit = None
        m = re.match(r'^(.*?)\((.*?)\)$', name)
        if m and kind == 'sub':
            name, unit = m.group(1), m.group(2)
        calc = (it['carry'] or 0) + it['in'] - it['out'] - it['bad']
        if it['carry'] is None and it['in'] == 0 and it['out'] == 0 and it['lastStock'] is None:
            continue
        if kind == 'pack' and group == '외포장지':
            if re.fullmatch(r'\d+', name):
                name = f'{name}개입 박스'
            elif product and name == product:
                name = '박스'
        if kind == 'pack' and group == '내포장지' and product == name:
            name = '내포장지'
        out.append({'group': group, 'product': product, 'name': name, 'unit': unit, 'carry': r2(it['carry']),
                    'in': r2(it['in']), 'out': r2(it['out']), 'bad': r2(it['bad']), 'stock': r2(calc)})
    date_ym = max(date_months, key=date_months.get) if date_months else None
    return {'sheet': sn, 'ym': f'20{yymm[:2]}-{yymm[2:]}', 'dateYm': date_ym, 'lastEntry': last_date, 'items': out, 'file': mtime(path)}

def parse_ledgers(src):
    folder = os.path.join(src, '부자재및부재료 수불부')
    res = {}
    p1 = os.path.join(folder, '부재료수불부.xlsx')
    p2 = os.path.join(folder, '부자재수불부.xlsx')
    if os.path.exists(p1):
        res['sub'] = parse_ledger(p1, 'sub')
    if os.path.exists(p2):
        res['pack'] = parse_ledger(p2, 'pack')
        # 부자재 그룹명 보정: (내포장지|외포장지, 제품, 품목)
        if res['pack']:
            for it in res['pack']['items']:
                pass
    return res

# ───────────────────────── 5. 근무일지·추가수당·업무분담 ─────────────────────────
def parse_attendance_file(path):
    wb = load(path)
    ws = wb.worksheets[0]
    sh = Sheet(ws)
    base = os.path.basename(path)
    m = re.search(r'(\d{4})', base)
    if not m:
        return None
    yymm = m.group(1)
    if not yymm.startswith('2'):
        return None
    year, month = 2000 + int(yymm[:2]), int(yymm[2:])
    # 이름 행
    name_r = None
    for r in range(1, 6):
        for c in range(1, 4):
            if norm(sh.raw(r, c)) == '이름':
                name_r = r
                break
        if name_r:
            break
    if not name_r:
        return None
    sub_r = None
    for r in range(name_r + 1, name_r + 4):
        vals = [norm(sh.raw(r, c)) for c in range(1, sh.max_col + 1)]
        if any(v in ('주간', '야간', '가공') for v in vals):
            sub_r = r
            break
    cols = {}
    for c in range(3, sh.max_col + 1):
        nm = sh.val(name_r, c)
        if not isinstance(nm, str) or not nm.strip():
            continue
        nm = nm.strip()
        if norm(nm) in ('일계', '계', '합계'):
            continue
        sub = norm(sh.raw(sub_r, c)) if sub_r else '주간'
        kind = {'주간': 'day', '가공': 'day', '야간': 'night', '팽이': 'other', '새송이': 'other'}.get(sub)
        if kind:
            cols[c] = (nm, kind)
    start = (sub_r or name_r) + 1
    days = []
    workers = {}
    for r in range(start, sh.max_row + 1):
        a = sh.raw(r, 2)
        d = parse_date_cell(a, year)
        if norm(a) in ('계', '합계'):
            break
        if d is None and a in (None, '') and days:
            # 날짜 없이 시간만 적힌 행(계 행 바로 위 보정 등): 사람별 합계에만 더한다
            for c, (nm, kind) in cols.items():
                x = num(sh.raw(r, c))
                if x:
                    w = workers.setdefault(nm, {'day': 0, 'night': 0, 'other': 0, 'days': set()})
                    w[kind] += x
            continue
        if d is None or d.month != month:
            continue
        tot = 0
        heads = set()
        for c, (nm, kind) in cols.items():
            x = num(sh.raw(r, c))
            if not x:
                continue
            w = workers.setdefault(nm, {'day': 0, 'night': 0, 'other': 0, 'days': set()})
            w[kind] += x
            if kind in ('day', 'night'):
                tot += x
                heads.add(nm)
                w['days'].add(d)
        days.append([ymd(d), r2(tot), len(heads)])
    wlist = []
    for nm, w in workers.items():
        if w['day'] + w['night'] + w['other'] == 0:
            continue
        wlist.append({'name': nm, 'day': r2(w['day']), 'night': r2(w['night']), 'other': r2(w['other']),
                      'days': len(w['days'])})
    return {'ym': f'{year}-{month:02d}', 'days': days, 'workers': wlist, 'file': base}

def parse_overtime(path):
    wb = load(path)
    ws = wb.worksheets[0]
    title = None
    for r in range(1, 5):
        for c in range(1, 7):
            v = ws.cell(r, c).value
            if isinstance(v, str) and '추가수당' in v:
                title = v
    m = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월', title or '')
    if not m:
        return None
    ymk = f'{m.group(1)}-{int(m.group(2)):02d}'
    items, total = [], None
    for r in range(1, ws.max_row + 1):
        b = norm(ws.cell(r, 2).value)
        if b.startswith('합'):
            total = num(ws.cell(r, 5).value)
            break
        nm = ws.cell(r, 3).value
        amt = num(ws.cell(r, 5).value)
        if isinstance(nm, str) and nm.strip() and nm.strip() != '이름' and amt:
            items.append({'name': nm.strip(), 'amt': r2(amt), 'reason': (ws.cell(r, 6).value or '').strip() if isinstance(ws.cell(r, 6).value, str) else None})
    return {'ym': ymk, 'items': items, 'total': r2(total if total is not None else sum(i['amt'] for i in items))}

def parse_roles(src):
    folder = os.path.join(src, '근무조 편성 및 작업지시서')
    out = {}
    # 업무 분담: 가장 최근 yymm 파일
    files = sorted(glob.glob(os.path.join(folder, '[0-9][0-9][0-9][0-9].xlsx')))
    if files:
        path = files[-1]
        yymm = os.path.basename(path)[:4]
        wb = load(path)
        sn = next((s for s in wb.sheetnames if '분담' in s), None)
        if sn:
            ws = wb[sn]
            roles, teams = [], {}
            team = None
            for r in range(1, ws.max_row + 1):
                c_, d_ = ws.cell(r, 3).value, ws.cell(r, 4).value
                if isinstance(c_, str) and isinstance(d_, str) and c_.strip() != '이름':
                    roles.append({'name': c_.strip(), 'role': re.sub(r'\s+', ' ', d_).strip()})
                f_ = ws.cell(r, 6).value
                if isinstance(f_, str) and re.fullmatch(r'\s*\d\s*조\s*', f_):
                    team = f_.strip()
                elif isinstance(f_, str) and f_.strip() and team and f_.strip() != '이름':
                    teams.setdefault(team, []).append(f_.strip())
            out['roles'] = {'ym': f'20{yymm[:2]}-{yymm[2:]}', 'list': roles, 'teams': teams, 'sheet': sn}
    p = os.path.join(folder, '근무조.xlsx')
    if os.path.exists(p):
        wb = load(p)
        ws = wb.worksheets[0]
        title, shifts, notes = None, [], []
        for r in range(1, ws.max_row + 1):
            b, c_, d_ = ws.cell(r, 2).value, ws.cell(r, 3).value, ws.cell(r, 4).value
            if isinstance(b, str) and '근무조' in b:
                title = b.strip()
            elif isinstance(b, str) and re.match(r'^[A-Z]조$', b.strip()):
                shifts.append({'team': b.strip(), 'time': str(c_ or '').strip(), 'members': re.sub(r'\s+', ' ', str(d_ or '')).strip()})
            elif isinstance(b, str) and b.strip():
                notes.append(b.strip())
        out['shifts'] = {'title': title, 'list': shifts, 'notes': notes, 'file': mtime(p)}
    return out

def parse_labor(src):
    folder = os.path.join(src, '가공 근로자')
    months = {}
    for path in sorted(glob.glob(os.path.join(folder, '*근무일지*.xlsx'))):
        if os.path.basename(path).startswith('~$'):
            continue
        try:
            res = parse_attendance_file(path)
        except Exception as e:
            print('근무일지 오류', path, e, file=sys.stderr)
            continue
        if res and res['ym'] >= '2026-01':
            months[res['ym']] = res
    overtime = {}
    for path in sorted(glob.glob(os.path.join(folder, '추가수당*.xlsx'))):
        try:
            res = parse_overtime(path)
        except Exception as e:
            print('추가수당 오류', path, e, file=sys.stderr)
            continue
        if res and res['ym'] >= '2026-01':
            overtime[res['ym']] = res
    return {'months': months, 'overtime': overtime, **parse_roles(src)}

# ───────────────────────── 6. 보일러 ─────────────────────────
def parse_hours_text(t):
    if not isinstance(t, str):
        return None
    m = re.match(r'\s*(am|pm)?\s*(\d{1,2})\s*:\s*(\d{2})', t.strip(), re.I)
    if not m:
        return None
    h = int(m.group(2)) % 12
    if (m.group(1) or '').lower() == 'pm':
        h += 12
    return h + int(m.group(3)) / 60

def parse_boiler(src):
    folder = os.path.join(src, '보일러가동일지')
    months = {}
    for path in sorted(glob.glob(os.path.join(folder, '*.xlsx'))):
        base = os.path.basename(path)
        m = re.match(r'(\d{4})', base)
        if not m:
            continue
        yymm = m.group(1)
        year, month = 2000 + int(yymm[:2]), int(yymm[2:])
        wb = load(path)
        ws = wb.worksheets[0]
        hours = fuel = 0
        days = 0
        last = None
        for r in range(1, ws.max_row + 1):
            d = parse_date_cell(ws.cell(r, 2).value, year)
            if not d or d.month != month:
                continue
            f = num(ws.cell(r, 3).value) or 0
            h = num(ws.cell(r, 6).value)
            if h is None:
                s, e = parse_hours_text(ws.cell(r, 4).value), parse_hours_text(ws.cell(r, 5).value)
                if s is not None and e is not None:
                    h = (e - s) % 24
            if f:
                fuel += f
                last = ymd(d)
            if h:
                hours += h
                days += 1
                last = ymd(d)
        months[f'{year}-{month:02d}'] = {'hours': r2(hours), 'days': days, 'fuel': r2(fuel), 'last': last, 'file': base}
    return months

# ───────────────────────── 7. 일일 업무보고서(HWP) ─────────────────────────
def _hwp_records(data):
    i, n = 0, len(data)
    while i + 4 <= n:
        hdr = struct.unpack_from('<I', data, i)[0]
        tag, level, size = hdr & 0x3FF, (hdr >> 10) & 0x3FF, (hdr >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            size = struct.unpack_from('<I', data, i)[0]
            i += 4
        yield tag, level, data[i:i + size]
        i += size

_EXT = {1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
_INL = {4, 5, 6, 7, 8, 9, 19, 20}

def _para_text(payload):
    out, i = [], 0
    n = len(payload) // 2
    w = struct.unpack_from('<%dH' % n, payload)
    while i < n:
        c = w[i]
        if c < 32:
            if c in _EXT or c in _INL:
                i += 8
                if c == 9:
                    out.append(' ')
                continue
            if c in (10, 13):
                out.append('\n')
            elif c == 24:
                out.append('-')
            elif c in (30, 31):
                out.append(' ')
            i += 1
            continue
        out.append(chr(c))
        i += 1
    return ''.join(out)

def _olefile():
    try:
        import olefile
        return olefile
    except ImportError:
        import subprocess
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', 'olefile'], timeout=180, check=False)
            import olefile
            return olefile
        except Exception:
            return None

def hwp_text(path):
    olefile = _olefile()
    if olefile is None:
        raise RuntimeError('olefile 패키지가 없어 한글(.hwp) 파일을 읽지 못했습니다 (pip install olefile)')
    ole = olefile.OleFileIO(path)
    hdr = ole.openstream('FileHeader').read()
    compressed = bool(struct.unpack_from('<I', hdr, 36)[0] & 1)
    lines = []
    k = 0
    while ole.exists('BodyText/Section%d' % k):
        raw = ole.openstream('BodyText/Section%d' % k).read()
        data = zlib.decompress(raw, -15) if compressed else raw
        for tag, level, payload in _hwp_records(data):
            if tag == 67:
                lines.append(_para_text(payload))
        k += 1
    ole.close()
    return '\n'.join(lines)

TAGS = [
    ('설비', r'설비|수리|고장|교체|누수|모터|체인|이탈|변압기|보일러|유탕기|포장기|컨베이어|탈수기|진공|에어|센서|부품|오일탱크|AS|A/S'),
    ('입고', r'입고|주유'),
    ('출고', r'출고|납품|배송'),
    ('생산', r'생산|포장|유탕|가공|작업'),
    ('인력', r'기숙사|근로자|귀국|입국|출국|휴가|채용|면접|건강검진|교육|외국인|근무'),
    ('위생', r'세스코|소독|위생|HACCP|해썹|성적서|자가품질|검사|이물|청소|AUDIT|오딧|심사'),
    ('개발', r'블랜칭|시험|샘플|시생산|테스트|시제품|개발|레시피|배합'),
    ('구매', r'구매|발주|견적|결재'),
]

def tag_line(t):
    return [name for name, pat in TAGS if re.search(pat, t, re.I)]

def parse_reports(src):
    path = os.path.join(src, '일일 업무보고서.hwp')
    if not os.path.exists(path):
        return None
    text = hwp_text(path)
    blocks = re.split(r'\n\s*업무보고서\s*\n', '\n' + text)
    reports = []
    skip_exact = {'결', '재', '업', '무', '내', '용', '담 당 자', '담당자', '이 사', '이사', '대 표', '대표', '-', '–', '⇒'}
    for b in blocks:
        m = re.search(r'일\s*시\s*:\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', b)
        if not m:
            continue
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        by = None
        mb = re.search(r'담\s*당\s*자\s*:\s*([^\n]+)', b)
        if mb:
            by = re.sub(r'\s+', '', mb.group(1))
        body = b
        # 담당자 줄 이후 ~ 비고 전까지
        if mb:
            body = b[mb.end():]
        body = re.split(r'\n\s*비고\s*\n', body + '\n')[0]
        lines = []
        for raw in body.split('\n'):
            t = raw.strip()
            if not t or t in skip_exact:
                continue
            if re.fullmatch(r'[\-–•·⇒\s]*', t):
                continue
            if re.fullmatch(r'\(?\s*수리\s*(전|후)\s*\)?(\s+\(?\s*수리\s*(전|후)\s*\)?)*', t):
                continue
            indent = len(raw) - len(raw.lstrip(' '))
            bullet = bool(re.match(r'^[\-–•·]', t))
            t = re.sub(r'^[\-–•·]\s*', '', t).strip()
            t = re.sub(r'\s{2,}', ' ', t)
            if not t:
                continue
            if lines and not bullet and indent >= 9 and lines[-1].get('b'):
                # 들여쓴 이어지는 줄은 하위 항목으로
                lines.append({'t': t, 'sub': True})
            else:
                lines.append({'t': t, 'b': True})
        for ln in lines:
            ln['tags'] = tag_line(ln['t'])
        reports.append({'d': ymd(d), 'by': by, 'lines': [
            {k: v for k, v in ln.items() if k in ('t', 'sub', 'tags') and (v or k == 't')} for ln in lines]})
    reports.sort(key=lambda r: r['d'])
    # 같은 날짜가 두 번이면 합침
    merged = []
    for r in reports:
        if merged and merged[-1]['d'] == r['d']:
            merged[-1]['lines'].extend(r['lines'])
        else:
            merged.append(r)
    return {'reports': merged, 'file': mtime(path)}


# ───────────────────────── 7-2. 일일 업무보고서(엑셀, 2026-10-07~) ─────────────────────────
# 업무자료\반가공일일업무보고서2026.10월~\반가공 일일업무보고서.xlsx — 하루 한 시트(시트 이름 YYMMDD).
#  A2 '일 시 : 2026년 10월 7일 수요일', A4 '담 당 자 : 김정미', 표 머리 '업무구분 | 업무내용 | 진행상황', 맨 아래 '비고'(▣ 줄).
def _cell_text(v):
    t = '' if v is None else str(v)
    t = re.sub(r'\s*\n\s*', ' ', t)
    t = re.sub(r'\s+([,.)])', r'\1', t)
    return re.sub(r'\s{2,}', ' ', t).strip()

def _report_sheet(sh, title):
    head = {}
    d = by = None
    for r in range(1, min(sh.max_row, 12) + 1):
        for c in range(1, min(sh.max_col, 12) + 1):
            v = sh.raw(r, c)
            if v is None:
                continue
            t = str(v)
            m = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', t)
            if d is None and m and '일' in norm(t)[:3]:
                d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            mb = re.search(r'담\s*당\s*자\s*:\s*(\S.*)', t)
            if by is None and mb:
                by = re.sub(r'\s+', '', mb.group(1))
            k = norm(t)
            if k in ('업무구분', '업무내용', '진행상황'):
                head.setdefault(k, []).append((r, c))
    if d is None:
        m = re.fullmatch(r'(\d{2})(\d{2})(\d{2})', title.strip())
        if not m:
            return None
        d = dt.date(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3)))
    # 표 머리 줄 = '업무구분'이 있는 줄 (왼쪽 세로 글씨 '업 무 내 용' 칸과 헷갈리지 않게 같은 줄의 머리만 쓴다)
    if '업무구분' not in head:
        return None
    r0, cg = head['업무구분'][0]
    pick = lambda k: min((c for r, c in head.get(k, []) if r == r0 and c > cg), default=None)
    cn, cs = pick('업무내용'), pick('진행상황')
    if cn is None:
        return None
    lines = []
    seen = set()
    for r in range(r0 + 1, sh.max_row + 1):
        first = norm(sh.val(r, 1)) + norm(sh.val(r, 2))
        if first.startswith('비고'):
            note = sh.val(r, 2) if norm(sh.val(r, 2)) and not norm(sh.val(r, 2)).startswith('비고') else sh.val(r, 3)
            for raw in str(note or '').split('\n'):
                t = re.sub(r'^[▣■□◆◇●○•·\-–]\s*', '', raw.strip()).strip()
                if t:
                    lines.append({'t': '비고: ' + _cell_text(t), 'sub': True})
            break
        g = _cell_text(sh.raw(r, cg)) if cg else ''
        n = _cell_text(sh.raw(r, cn))
        st_ = _cell_text(sh.raw(r, cs)) if cs else ''
        if not n and not g:
            continue
        key = (g, n)
        if key in seen:
            continue
        seen.add(key)
        t = (f'{g}: {n}' if g and n else (g or n)) + (f' ({st_})' if st_ else '')
        lines.append({'t': t, 'b': True})
    for ln in lines:
        ln['tags'] = tag_line(ln['t'])
    return {'d': ymd(d), 'by': by, 'lines': [{k: v for k, v in ln.items() if k in ('t', 'sub', 'tags') and (v or k == 't')} for ln in lines]}

def parse_reports_xlsx(src):
    files = []
    for folder in sorted(glob.glob(os.path.join(src, '반가공일일업무보고서*'))):
        if os.path.isdir(folder):
            files += [f for f in sorted(glob.glob(os.path.join(folder, '*.xls*'))) if not os.path.basename(f).startswith('~$')]
    if not files:
        return None
    reports, stamps = [], []
    files.sort(key=lambda f: os.path.getmtime(f))     # 같은 날짜 시트가 두 파일에 있으면 나중에 고친 파일 것이 이기게 (2610 새 파일 > 옛 파일)
    for path in files:
        wb = load(path)
        for ws in wb.worksheets:
            rep = _report_sheet(Sheet(ws), ws.title)
            if rep and rep['lines']:
                reports.append(rep)
        stamps.append(mtime(path))
    return {'reports': reports, 'file': max(stamps)}

# ───────────────────────── 7-3. 일간 작업지시서 (2026-10~) ─────────────────────────
# 근무조 편성 및 작업지시서\*작업지시서*.xlsx — 시트 하나 = 하루. '2026년 10월 06일 (화) | 근무시간 08:00 ~ 18:30',
#  표 머리 '시간 | 구분 | 작업 내용 | 작업자', 아래 '-특이사항 및 전달사항-' 칸(* 로 시작하는 줄, 들여쓴 줄은 앞 줄의 세부).
# 파일을 날마다 덮어써도 지난 날짜가 남도록 이전 결과(reports__all.json 의 orders)와 합친다.
def _order_sheet(sh):
    d = hours = None
    hr = None
    for r in range(1, min(sh.max_row, 10) + 1):
        for c in range(1, min(sh.max_col, 8) + 1):
            t = sh.raw(r, c)
            if t is None:
                continue
            t = str(t)
            m = re.search(r'(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', t)
            if d is None and m:
                try:
                    d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except ValueError:
                    pass
                mh = re.search(r'근무\s*시간\s*:?\s*([0-9:]+\s*~\s*[0-9:]+)', t)
                if mh:
                    hours = re.sub(r'\s+', ' ', mh.group(1))
        if hr is None:
            heads = [norm(sh.raw(r, c)) for c in range(1, min(sh.max_col, 8) + 1)]
            if '시간' in heads and any('작업' in h_ for h_ in heads):
                hr = r
                cols = {}
                for i, h_ in enumerate(heads, start=1):
                    if h_ == '시간': cols['time'] = i
                    elif h_ == '구분': cols['kind'] = i
                    elif '작업내용' in h_ or h_ == '내용': cols['task'] = i
                    elif '작업자' in h_ or '담당' in h_: cols['who'] = i
    if d is None or hr is None:
        return None
    rows, notes = [], []
    for r in range(hr + 1, sh.max_row + 1):
        first = sh.raw(r, 1)
        ft = str(first or '').strip()
        if '특이사항' in ft or '전달사항' in ft:
            for raw in ft.split('\n'):
                t = raw.rstrip()
                if not t.strip() or re.fullmatch(r'\s*-?\s*특이사항.*', t):
                    continue
                sub = not t.lstrip().startswith(('*', '※', '-', '•', '·', '▣')) and bool(notes)
                t = re.sub(r'^\s*[*※•·▣\-]\s*', '', t).strip()
                if t:
                    notes.append({'t': t, 'sub': sub})
            continue
        if ft.startswith('※'):
            continue
        g = lambda k: _cell_text(sh.raw(r, cols[k])) if k in cols else ''
        row = {'time': g('time'), 'kind': g('kind'), 'task': g('task'), 'who': g('who')}
        if row['task'] or row['kind']:
            rows.append(row)
    if not rows and not notes:
        return None
    return {'d': ymd(d), 'hours': hours, 'rows': rows, 'notes': notes}

def parse_orders(src, outdir=None):
    folder = os.path.join(src, '근무조 편성 및 작업지시서')
    files = [f for f in glob.glob(os.path.join(folder, '*작업지시서*.xls*')) if not os.path.basename(f).startswith('~$')]
    files.sort(key=lambda f: os.path.getmtime(f))          # 같은 날짜면 나중에 고친 파일 것
    by_d = {}
    if outdir:                                               # 지난 결과 이어받기 (파일을 날마다 덮어쓰는 경우)
        try:
            with open(os.path.join(outdir, 'reports__all.json'), encoding='utf-8') as f:
                for o in (json.load(f).get('orders') or []):
                    by_d[o['d']] = o
        except Exception:
            pass
    stamps = []
    for path in files:
        wb = load(path)
        for ws in wb.worksheets:
            o = _order_sheet(Sheet(ws))
            if o:
                o['file'] = os.path.basename(path)
                by_d[o['d']] = o
        stamps.append(mtime(path))
    if not by_d:
        return None
    return {'orders': [by_d[k] for k in sorted(by_d)], 'file': max(stamps) if stamps else None}

def merge_reports(hwp, xl):
    """한글(~2026-10-06)과 엑셀(2026-10-07~) 업무보고서를 날짜순으로 합친다. 같은 날이 둘 다 있으면 엑셀 것을 쓴다."""
    if not hwp and not xl:
        return None
    by_d = {r['d']: r for r in (hwp or {}).get('reports', [])}
    for r in (xl or {}).get('reports', []):
        by_d[r['d']] = r
    files = [f for f in ((hwp or {}).get('file'), (xl or {}).get('file')) if f]
    return {'reports': [by_d[k] for k in sorted(by_d)], 'file': max(files) if files else None}


# ───────────────────────── 실행 ─────────────────────────
DOC_FILES = ['meta__info', 'prod__summary', 'prod__daily', 'sales__all', 'purchase__all',
             'stock__ledgers', 'labor__all', 'equip__boiler', 'reports__all']


def _write_json(path, body):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(body, f, ensure_ascii=False, separators=(',', ':'), default=str)
    os.replace(tmp, path)


def build(src, outdir, today=None, progress=print):
    """원본 폴더 src 를 읽어 outdir 에 JSON 문서를 쓴다. 쓴 문서 수를 돌려준다."""
    today = today or dt.date.today()
    os.makedirs(outdir, exist_ok=True)
    errors = []

    def safe(label, fn, *a, default=None):
        try:
            return fn(*a)
        except Exception as e:  # 한 자료가 깨져도 나머지는 계속
            errors.append(f'{label}: {e}')
            progress(f'반가공 {label}: 실패 — {e}')
            return default

    prods, prod_files = safe('가공일지', parse_production, src, default=({}, {}))
    products, daily, raw_stock = safe('가공일지 집계', build_production, prods, today, default=([], [], {}))
    oil = safe('현미유 재고', crunteen_oil_stock, src, today)
    sales = safe('거래명세표', parse_sales, src)
    purchases = safe('결재요청서', parse_purchases, src)
    ledgers = safe('수불부', parse_ledgers, src, default={}) or {}
    labor = safe('근무일지', parse_labor, src, default={'months': {}, 'overtime': {}}) or {'months': {}, 'overtime': {}}
    boiler = safe('보일러 가동일지', parse_boiler, src, default={}) or {}
    reports = merge_reports(safe('일일 업무보고서(한글)', parse_reports, src),
                            safe('일일 업무보고서(엑셀)', parse_reports_xlsx, src))
    orders = safe('일간 작업지시서', parse_orders, src, outdir)

    def latest(dates):
        ds = [d for d in dates if d]
        return max(ds) if ds else None

    sources = []
    for p in products:
        sources.append({'key': 'prod_' + p['key'], 'label': f"{p['name']} 가공일지", 'area': '생산', 'latest': p['lastActivity']})
    if sales:
        sources.append({'key': 'sales', 'label': '거래명세표', 'area': '매출', 'latest': latest([s['d'] for s in sales['statements']]), 'file': sales['file']})
    if purchases:
        sources.append({'key': 'purchase', 'label': '결재요청서', 'area': '구매', 'latest': latest([q['d'] for q in purchases['requests']]), 'file': purchases['file']})
    if ledgers.get('sub'):
        sources.append({'key': 'ledger_sub', 'label': '부재료 수불부', 'area': '재고', 'latest': ledgers['sub']['lastEntry'], 'file': ledgers['sub']['file'], 'note': f"{ledgers['sub']['sheet']} 시트"})
    if ledgers.get('pack'):
        sources.append({'key': 'ledger_pack', 'label': '부자재 수불부', 'area': '재고', 'latest': ledgers['pack']['lastEntry'], 'file': ledgers['pack']['file'], 'note': f"{ledgers['pack']['sheet']} 시트"})
    if labor.get('months'):
        lm = max(labor['months'])
        lastday = latest([d[0] for d in labor['months'][lm]['days'] if d[1]])
        sources.append({'key': 'labor', 'label': '근무일지', 'area': '인력', 'latest': lastday, 'note': labor['months'][lm]['file']})
    if labor.get('overtime'):
        sources.append({'key': 'overtime', 'label': '추가수당 지급요청서', 'area': '인력', 'latest': max(labor['overtime']) + '-01', 'note': f"{max(labor['overtime'])} 급여분"})
    if boiler:
        sources.append({'key': 'boiler', 'label': '보일러 가동일지', 'area': '설비', 'latest': latest([v['last'] for v in boiler.values()]),
                        'note': ', '.join(f"{int(k[5:])}월 기록 없음" for k, v in sorted(boiler.items()) if not v['last'])})
    if reports:
        sources.append({'key': 'reports', 'label': '일일 업무보고서', 'area': '업무', 'latest': latest([r['d'] for r in reports['reports']]), 'file': reports['file']})
    if orders:
        sources.append({'key': 'orders', 'label': '일간 작업지시서', 'area': '업무', 'latest': latest([o['d'] for o in orders['orders']]), 'file': orders['file']})

    docs = {
        'prod__summary': {'products': products, 'rawStock': raw_stock, 'oil': oil},
        'prod__daily': {'rows': sorted(daily, key=lambda r: (r['d'], r['p']))},
        'sales__all': sales or {'statements': []},
        'purchase__all': purchases or {'requests': []},
        'stock__ledgers': ledgers,
        'labor__all': labor,
        'equip__boiler': {'months': boiler},
        'reports__all': {**(reports or {'reports': []}), 'orders': (orders or {}).get('orders', []), 'ordersFile': (orders or {}).get('file')},
        # meta 는 마지막에 쓴다 (화면은 meta 의 updatedAt 으로 새 자료를 알아챈다)
        'meta__info': {'updatedAt': dt.datetime.now().strftime('%Y-%m-%d %H:%M'), 'today': ymd(today),
                       'source': src, 'sources': sources, 'productFiles': prod_files, 'errors': errors},
    }
    for name, body in docs.items():
        _write_json(os.path.join(outdir, name + '.json'), body)
    return len(docs)


def source_dir():
    """config.BANAGONG_SRC 중 처음으로 있는 폴더."""
    from . import config as C
    for p in getattr(C, 'BANAGONG_SRC', []):
        if p and os.path.isdir(os.path.join(p, '생산출고일지및 원재료수불')):
            return p
    return None


def run(progress=print, today=None):
    """앱·etl_cli 에서 부르는 진입점. 원본이 없으면 0."""
    from . import config as C
    src = source_dir()
    if not src:
        progress('반가공: 원본 폴더 없음 (copy_banagong.bat 로 Downloads\\반가공자료 를 먼저 만드세요)')
        return 0
    n = build(src, C.BANAGONG_CACHE, today, progress)
    progress(f'반가공: {src} → {C.BANAGONG_CACHE} ({n}개 문서)')
    return n


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) >= 2:
        today = dt.date.fromisoformat(argv[2]) if len(argv) >= 3 else None
        n = build(argv[0], argv[1], today)
        print(f'{n}개 문서 → {argv[1]}')
    else:
        run()


if __name__ == '__main__':
    main()
