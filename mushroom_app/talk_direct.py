# -*- coding: utf-8 -*-
"""톡클라우드 사진 직접 판독 도우미 (v1.33.0) — Claude 예약 작업이 크롬으로 받아 둔 사진을 읽어 app_cache\\talk\\index.json 에 넣을 때 쓰는 명령줄 도구.
표준 라이브러리 + Pillow(있으면 축소·회전) 만 쓴다. 메인PC 밖(Claude 작업 환경)에서 돌아감.

  python3 talk_direct.py pending <dir1> [<dir2> ...] -- <index.json> <pending.json>
      폴더들의 YYYYMMDD_HHMMSS[_n].jpg|jpeg|png 중 index 에 없는 것(파일 이름 기준) 목록.
      (이름의 시각은 12시간제 — 오후 1:26 도 012631. 실제 올린 시각은 meta.json 의 uploaded 로 넣는다)
  python3 talk_direct.py render <pending.json> <png_dir> <items.json> [<index.json>] [<meta.json>]
      읽기용 이미지(긴 변 2200px, EXIF 회전 반영)로 복사. 같은 md5 가 index 에 있으면 alias_of. meta.json = {파일이름: {sender, uploaded}}.
  python3 talk_direct.py merge <index.json> <items.json> <results.jsonl> <new_index.json> <report.txt>
      판독 결과를 검산해 index 에 합침.

index 항목: {"file","path","size","sender","uploaded","kind","title","result","note","by","read_at","md5"}
result(출하지시서): {"orders":[{"ship_date":"YYYY-MM-DD","market":"서부청과","shipper":"연우","crop":"새송이",
                     "lines":[{"item":"새송이","unit_kg":4,"grade":"특","qty":80}], "total_qty":99}]}
"""
import os, re, sys, json, glob, hashlib, shutil, datetime as dt

NAME_RE = re.compile(r"^(\d{8})_(\d{6})(?:_\d+)?\.(jpe?g|png)$", re.I)      # 같은 초에 여러 장이면 크롬이 ' (1)' 을 붙임 → '_1' 로 바꿔 둔다


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def cmd_pending(dirs, index_json, pending_json):
    idx = json.load(open(index_json, encoding="utf-8")) if os.path.exists(index_json) else {}
    out = []
    for d in dirs:
        for p in sorted(glob.glob(os.path.join(d, "*"))):
            name = os.path.basename(p)
            m = NAME_RE.match(name)
            if not m or name in idx:
                continue
            size = os.path.getsize(p)
            if size == 0:
                print(f"  0바이트 건너뜀: {name}")
                continue
            out.append({"id": f"t{len(out):03d}", "file": name, "local": p, "size": size,
                        "uploaded": f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]} {m.group(2)[:2]}:{m.group(2)[2:4]}:{m.group(2)[4:]}"})
    json.dump(out, open(pending_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"읽을 사진 {len(out)}개")
    for it in out:
        print(f"  {it['id']} {it['file']}  {it['uploaded']}  {it['size']:,}B")
    return out


def _prep_image(src, dst, max_side=2200):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        shutil.copy(src, dst); return dst
    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGB") if im.mode not in ("RGB", "L") else im
    w, h = im.size
    if max(w, h) > max_side:
        r = max_side / max(w, h)
        im = im.resize((int(w * r), int(h * r)))
    im.save(dst, quality=88)
    return dst


def cmd_render(pending_json, png_dir, items_json, index_json=None, meta_json=None):
    P = json.load(open(pending_json, encoding="utf-8"))
    idx = json.load(open(index_json, encoding="utf-8")) if index_json and os.path.exists(index_json) else {}
    meta = json.load(open(meta_json, encoding="utf-8")) if meta_json and os.path.exists(meta_json) else {}
    md5_of = {v.get("md5"): k for k, v in idx.items() if v.get("md5")}
    os.makedirs(png_dir, exist_ok=True)
    items = []
    for it in P:
        it = dict(it, md5=_md5(it["local"]))
        it.update({k: v for k, v in (meta.get(it["file"]) or {}).items() if k in ("sender", "uploaded") and v})
        if it["md5"] in md5_of:
            it["alias_of"] = md5_of[it["md5"]]
            print(f"  {it['id']} {it['file']}: 이미 읽은 사진과 내용 같음 → {it['alias_of']}")
        else:
            out = os.path.join(png_dir, it["id"] + ".jpg")
            if not os.path.exists(out):
                _prep_image(it["local"], out)
            it["pngs"] = [out]
        items.append(it)
    json.dump(items, open(items_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n = sum(1 for i in items if not i.get("alias_of"))
    print(f"읽을 사진 {n}개, 별칭 {len(items) - n}개 → {items_json}")
    return items


def _check(res):
    """출하지시서 검산: 줄 수량 합 = 표기 합계. 돌려줌: (정상 여부, 메모)"""
    bad = []
    for o in (res or {}).get("orders", []):
        q = sum((ln.get("qty") or 0) for ln in o.get("lines", []))
        t = o.get("total_qty")
        if t is not None and abs(q - t) > 0.5:
            bad.append(f"{o.get('market')} 합계 {t} ≠ 줄 합 {q:g}")
    return (not bad), "; ".join(bad)


def cmd_merge(index_json, items_json, results_jsonl, new_index_json, report_txt):
    idx = json.load(open(index_json, encoding="utf-8")) if os.path.exists(index_json) else {}
    items = json.load(open(items_json, encoding="utf-8"))
    R = {}
    for line in open(results_jsonl, encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line); R[r["file"]] = r
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
    lines, n_ok, n_bad, n_alias, missing = [], 0, 0, 0, []
    for it in items:
        key = it["file"]
        base = {"file": key, "path": it.get("local"), "size": it.get("size"), "sender": it.get("sender"), "uploaded": it.get("uploaded"),
                "by": "직접판독(예약)", "read_at": now, "md5": it.get("md5")}
        if it.get("alias_of") and it["alias_of"] in idx:
            src = idx[it["alias_of"]]
            idx[key] = dict(src, **{k: v for k, v in base.items() if v is not None}, note=(src.get("note") or "") + f" (같은 사진: {it['alias_of']})")
            n_alias += 1; lines.append(f"= {key}: {it['alias_of']} 와 같은 사진"); continue
        r = R.get(key)
        if not r:
            missing.append(key); continue
        res = r.get("result") or {}
        kind = r.get("kind") or ("출하지시서" if res.get("orders") else "기타")
        ok, memo = _check(res) if kind == "출하지시서" else (True, "")
        e = dict(base, kind=kind, title=r.get("title") or "", result=res, note=(r.get("note") or "") + ((" · 검산: " + memo) if memo else ""))
        if r.get("sender") and not e.get("sender"):
            e["sender"] = r["sender"]
        idx[key] = e
        n_ok += ok; n_bad += (not ok)
        if kind == "출하지시서":
            s = " / ".join(f"{o.get('market')} {o.get('total_qty') if o.get('total_qty') is not None else sum((l.get('qty') or 0) for l in o.get('lines', [])):g}박스"
                           for o in res.get("orders", []))
            d = (res.get("orders") or [{}])[0].get("ship_date", "?")
            lines.append(f"{'o' if ok else 'x'} {key} → 출하지시서 {d} {s}" + (f"  !! {memo}" if memo else ""))
        else:
            lines.append(f"o {key} → {kind} {e['title']}")
    os.makedirs(os.path.dirname(os.path.abspath(new_index_json)), exist_ok=True)
    json.dump(idx, open(new_index_json, "w", encoding="utf-8"), ensure_ascii=False)
    head = f"{now[:16]} 톡클라우드 사진 판독: 새로 읽음 {n_ok}개 정상, {n_bad}개 검산 불일치, 같은 사진 {n_alias}개" + (f", 결과 없음 {len(missing)}개({', '.join(missing[:5])})" if missing else "")
    open(report_txt, "w", encoding="utf-8").write(head + "\n" + "\n".join(lines) + "\n")
    print(head); print("\n".join(lines))
    return n_ok, n_bad, n_alias, missing


def main(argv=None):
    a = argv or sys.argv[1:]
    if not a:
        print(__doc__); return 2
    c = a[0]
    if c == "pending" and "--" in a:
        i = a.index("--")
        cmd_pending(a[1:i], a[i + 1], a[i + 2])
    elif c == "render" and len(a) in (4, 5, 6):
        cmd_render(a[1], a[2], a[3], a[4] if len(a) > 4 else None, a[5] if len(a) > 5 else None)
    elif c == "merge" and len(a) == 6:
        cmd_merge(a[1], a[2], a[3], a[4], a[5])
    else:
        print(__doc__); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
