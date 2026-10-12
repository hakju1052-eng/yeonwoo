# -*- coding: utf-8 -*-
"""카톡 아침 알림 — 메인PC에 로그인된 PC카카오톡의 단톡방 창에 글을 써 넣는다.
갱신(refresh_all.bat)이 끝난 뒤 `python -m mushroom_app.kakao` 로 보낸다. --dry-run 이면 화면에만 출력.

설정 파일: DATA_DIR\\kakao_config.json  (앱 데이터 관리 → 카톡 알림 에서 만들 수 있다. 비밀번호·키는 없다)
  {"room": "단톡방 이름(카톡에 보이는 그대로)", "min_level": "참고", "max_items": 8, "site": "https://farm.cod-elnath.ts.net"}

방식: 카카오는 개인 단톡방에 쓰는 API가 없어서, 열려 있는 채팅방 창의 입력칸에 글을 넣고 Enter 를 보낸다(pywin32).
  - 방 창이 안 열려 있으면 카톡 메인 창의 검색칸에 방 이름을 넣어 연다.
  - 창 제목이 방 이름과 같아야 한다. 같은 이름의 방이 둘이면 먼저 찾히는 쪽으로 간다.
  - PC카톡이 잠금(자리 비움 잠금)이면 못 쓴다. 설정 → 잠금 모드를 끄는 게 좋다.
  - 카카오가 권장하는 방식은 아니다. 하루 한두 번 짧은 글 정도로만 쓴다.
"""
import os, sys, json, time
import pandas as pd
from . import config as C, analysis as A, notify

CONFIG = os.path.join(C.DATA_DIR, "kakao_config.json")
LEVEL = notify.LEVEL


def load_config():
    if not os.path.exists(CONFIG):
        return None
    return json.load(open(CONFIG, encoding="utf-8"))


def save_config(cfg):
    C.ensure_dirs()
    json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def cooler_surge(ref_date, env=None, min_up=None, ratio=None, spike=None, tol_day=2, lookback=150, min_ref=4):
    """냉각기 가동률이 '그 방의 같은 사이클 일차 평소'보다 유독 높은 방 (어제 기준).
    팽이 발이실·생육실, 새송이 A~D: 같은 방·같은 일차(±tol_day)의 이전 배치들(최근 lookback일) 중앙값과 견준다 → 억제 단계처럼
    원래 많이 도는 시기는 평소도 같이 높아서 안 걸린다. 냉각실·배양실처럼 사이클이 없는 방은 최근 14일 중앙값과 견준다.
    v1.22.0: 하루만 살짝 높은 날은 빼고, 평소보다 min_up(20%p)·ratio(1.5배) 넘게 높은 날이 이틀 연속이거나
             하루라도 spike(35%p) 넘게 높은 방만 알린다 (8~10월 기준 알림 뜨는 아침 47% → 23%). 기본값은 config.RULES.
    반환 [(방, 어제 가동률, 평소 가동률, 설명)]"""
    R = C.RULES
    min_up = R.get("surge_up", 0.15) if min_up is None else min_up
    ratio = R.get("surge_ratio", 1.5) if ratio is None else ratio
    spike = R.get("surge_spike", 0.35) if spike is None else spike
    env = A.env_daily() if env is None else env
    if env is None or env.empty or "cool" not in env:
        return []
    y = pd.Timestamp(ref_date).normalize()
    y0 = y - pd.Timedelta(days=1)
    out, covered = [], set()
    frames = []
    try:
        K = A.paengi_chain(); frames.append(A.paengi_batch_env(K, env))
    except Exception:
        pass
    try:
        B = A.ss_batches(); frames.append(A.ss_batch_env(B, env))
    except Exception:
        pass
    frames = [f for f in frames if f is not None and len(f) and "cool" in f]
    if frames:
        X = pd.concat(frames, ignore_index=True)
        X = X[~X.room.str.startswith("배양실") & X.cool.notna()]

        def day_hits(d):
            """그날 '평소보다 높은' 방 {방: (가동률, 평소, 일차, 초과분)} 과 그날 비교한 방 목록"""
            today_rows = X[X.date == d].drop_duplicates("room")
            ref = X[(X.date < d - pd.Timedelta(days=3)) & (X.date >= d - pd.Timedelta(days=lookback))]
            rows = []
            for _, r in today_rows.iterrows():
                g = ref[(ref.room == r.room) & ((ref.cday - r.cday).abs() <= tol_day)]
                g = g[g.bid != r.bid] if "bid" in g else g
                if len(g) >= min_ref:
                    m = float(g.cool.median())
                    rows.append({"room": r.room, "farm": ("1433" if str(r.room).startswith(("발이실", "생육실")) else str(r.room)[0]),
                                 "cool": float(r.cool), "med": m, "cday": int(r.cday), "d": float(r.cool) - m})
            if not rows:
                return {}, set(today_rows.room)
            Rr = pd.DataFrame(rows)
            # 더운 날은 재배사(동) 전체가 같이 오른다 → 같은 동의 중앙값 상승분을 뺀 '그 방만의 초과분'으로 본다
            Rr["shift"] = Rr.groupby("farm")["d"].transform("median").clip(lower=0)
            Rr["excess"] = Rr["d"] - Rr["shift"]
            hit = Rr[(Rr["excess"] >= min_up) & (Rr["cool"] >= ratio * Rr["med"].clip(lower=0.02))]
            return {r.room: (r.cool, r.med, r.cday, r.excess) for r in hit.itertuples()}, set(today_rows.room)

        now, covered = day_hits(y)
        prev, _ = day_hits(y0)
        for room, (c, m, cday, ex) in sorted(now.items(), key=lambda kv: -kv[1][3]):
            if room in prev or ex >= spike:
                out.append((room, c, m, f"{cday}일차 평소" + (" · 이틀째" if room in prev else "")))
    # 사이클이 없는 방(냉각실 등): 최근 14일 중앙값
    e = env[~env.room.isin(C.MONITOR_EXCLUDE | C.EXCLUDE_ROOMS) & ~env.room.isin(covered) & env.room.str.startswith("냉각")]

    def flat_hits(d):
        t = e[(e.date == d) & (e.run > 0.5)]
        base = e[(e.date < d) & (e.date >= d - pd.Timedelta(days=14)) & (e.run > 0.5)]
        med = base.groupby("room")["cool"].median(); nb = base.groupby("room").size()
        hits = {}
        for _, r in t.iterrows():
            m = med.get(r.room)
            if pd.notna(r.cool) and pd.notna(m) and nb.get(r.room, 0) >= 7 and m >= 0.03 and r.cool - m >= min_up and r.cool >= ratio * m:
                hits[r.room] = (float(r.cool), float(m))
        return hits

    now_f, prev_f = flat_hits(y), flat_hits(y0)
    for room, (c, m) in now_f.items():
        if room in prev_f or c - m >= spike:
            out.append((room, c, m, "최근 14일 평소" + (" · 이틀째" if room in prev_f else "")))
    out.sort(key=lambda t_: -(t_[1] - t_[2]))
    return out


def forecast_today(ref_date=None):
    """오늘 날씨 예보 한 줄 (Open-Meteo). 실패하면 None."""
    try:
        from . import weather
        lat, lon = C.FARM_LATLON
        js = weather._get(weather.FORECAST, {"latitude": lat, "longitude": lon, "timezone": "Asia/Seoul", "forecast_days": 2,
                                            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode,wind_speed_10m_max"}, timeout=15)
        d = js["daily"]; i = 0
        if ref_date is not None:
            ds = pd.Timestamp(ref_date).strftime("%Y-%m-%d")
            i = d["time"].index(ds) if ds in d["time"] else 0
        code = int(d["weathercode"][i] or 0)
        sky = "맑음" if code == 0 else "구름 조금" if code in (1, 2) else "흐림" if code == 3 else "안개" if code in (45, 48) else \
              "이슬비" if code in (51, 53, 55) else "비" if code in (61, 63, 65, 80, 81, 82) else "눈" if code in (71, 73, 75, 77, 85, 86) else "뇌우" if code >= 95 else "흐림"
        pr = d["precipitation_sum"][i] or 0; pp = d["precipitation_probability_max"][i]
        return (f"{sky}, {d['temperature_2m_min'][i]:.0f}~{d['temperature_2m_max'][i]:.0f}℃" +
                (f", 비 {pr:.0f}mm(확률 {pp:.0f}%)" if pr >= 1 or (pp is not None and pp >= 50) else "") +
                (f", 바람 {d['wind_speed_10m_max'][i]:.0f}km/h" if (d["wind_speed_10m_max"][i] or 0) >= 30 else ""))
    except Exception:
        return None


def compose(ref_date=None, cfg=None):
    """카톡 글. 관리자가 눈으로 놓치기 쉬운 것만: 0 살균솥 → 1 냉각실 → 2 냉각기 가동률 상승 → 3 센서 이상 → 4 오늘 날씨 → 주소."""
    cfg = cfg or load_config() or {}
    today = pd.Timestamp(ref_date) if ref_date else pd.Timestamp.today().normalize()
    y = today - pd.Timedelta(days=1)
    max_items = int(cfg.get("max_items", 8))
    lines = [f"[스마트팜 {today:%m/%d(%a)} 아침] 어제({y:%m/%d}) 기준"]
    # 0·1) 살균솥 · 냉각실 (공정 경고)
    try:
        PA = A.process_alerts(y, y)
    except Exception:
        PA = pd.DataFrame()
    ster = PA[PA["분류"] == "살균 지연"] if len(PA) else pd.DataFrame()
    cool = PA[PA["분류"].isin(["냉각 느림", "냉각실 과냉"])] if len(PA) else pd.DataFrame()
    # 살균솥: 어제 사이클 중 중단·미완료·2차 유지 부족
    try:
        from . import etl_retort
        CY = etl_retort.load_cycles()
        cy = CY[(CY.date == y) & (CY["구분"] != "액탱크")] if not CY.empty else pd.DataFrame()
        bad = []
        for _, r in cy.iterrows():
            f_ = []
            if not r["완료"]: f_.append("미완료")
            if r.get("중단횟수", 0) > 0: f_.append(f"중단 {int(r['중단횟수'])}회")
            if r["완료"] and r["2차유지분"] < 55: f_.append(f"2차 유지 {r['2차유지분']:.0f}분")
            if r["완료"] and r["2차유지온도"] < 116.5: f_.append(f"2차 {r['2차유지온도']:.1f}℃")
            if f_: bad.append(f"{r.unit} {r.start:%H:%M}: " + " · ".join(f_))
    except Exception:
        bad = []
    if len(ster) or bad:
        lines.append(f"■ 살균솥 {len(ster) + len(bad)}건")
        for b in bad[:4]:
            lines.append(f"- {b}")
        for _, r in ster.head(4).iterrows():
            lines.append(f"- {r['방']}: {r['내용']}")
    else:
        lines.append("■ 살균솥: 이상 없음")
    if len(cool):
        lines.append(f"■ 냉각실 {len(cool)}건")
        for _, r in cool.head(4).iterrows():
            lines.append(f"- {r['방']}: {r['내용']}")
    else:
        lines.append("■ 냉각실: 이상 없음")
    # 2) 냉각기 가동률 상승 (모든 재배사)
    try:
        surge = cooler_surge(y)
    except Exception:
        surge = []
    if surge:
        lines.append(f"■ 냉각기 가동률 급등 {len(surge)}개 방 (평소 대비)")
        lines.append("- " + ", ".join(f"{room} {c * 100:.0f}%({why} {m * 100:.0f}%)" for room, c, m, why in surge[:8]) + (" 외" if len(surge) > 8 else ""))
        lines.append("  (같은 방·같은 사이클 일차의 이전 배치들과 비교, 이틀 연속이거나 크게 높은 방만 — 냉동기 가스 부족·고장 의심)")
    else:
        lines.append("■ 냉각기 가동률: 평소 수준")
    # 3) 센서 이상 (어제)
    try:
        env = A.env_daily()
        e = env[~env.room.isin(C.MONITOR_EXCLUDE)]
        # 습도 포화(101~115%)·냉각실 뜨거운 솥은 센서 이상이 아님 (이상 감시와 같은 기준)
        sb, _ = A.sensor_fault_minutes(e)
        is_bad = sb > C.RULES.get("sensor_bad_min", 30)
        bad_y = set(e[(e.date == y) & is_bad].room)
        bad_prev = set(e[(e.date < y) & (e.date >= y - pd.Timedelta(days=7)) & is_bad].room)
        new_bad = sorted(bad_y - bad_prev); cont = sorted(bad_y & bad_prev)
        last = e.groupby("room").date.max(); active = e[e.date > y - pd.Timedelta(days=30)].groupby("room").run.mean()
        stale = sorted(r for r, d in last.items() if (y - d).days >= 2 and active.get(r, 0) > 0.2)
    except Exception:
        new_bad, cont, stale = [], [], []
    if new_bad or stale:
        parts = []
        if new_bad: parts.append("새로 생김: " + ", ".join(new_bad[:8]) + (" 외" if len(new_bad) > 8 else ""))
        if stale: parts.append("기록 끊김: " + ", ".join(stale[:6]) + (" 외" if len(stale) > 6 else ""))
        if cont: parts.append(f"계속 {len(cont)}개 방")
        lines.append("■ 센서 이상 — " + " / ".join(parts))
    elif cont:
        lines.append(f"■ 센서 이상: 새로 생긴 건 없음 (계속 {len(cont)}개 방: " + ", ".join(cont[:5]) + (" 외)" if len(cont) > 5 else ")"))
    else:
        lines.append("■ 센서: 이상 없음")
    # 4) 오늘 날씨
    fc = forecast_today(today)
    if fc:
        lines.append(f"■ 오늘 날씨: {fc}")
    site = cfg.get("site") or ""
    if site:
        lines.append(site)
    return "\n".join(lines)


# ───────────── PC카톡 창에 쓰기 (Windows 전용) ─────────────
def _win():
    try:
        import win32gui, win32con, win32api  # noqa
        return win32gui, win32con
    except ImportError:
        raise RuntimeError("pywin32 가 없습니다. 메인PC에서 `pip install pywin32` 후 다시 실행하세요.")


def _find_room(win32gui, room):
    hwnd = win32gui.FindWindow(None, room)
    return hwnd or 0


def _open_room(win32gui, win32con, room, wait=6.0):
    """카톡 메인 창의 검색칸에 방 이름을 넣고 Enter → 채팅방 창이 열린다."""
    main = win32gui.FindWindow("EVA_Window_Dblclk", None) or win32gui.FindWindow(None, "카카오톡")
    if not main:
        raise RuntimeError("PC카카오톡 창을 찾지 못했습니다. 카카오톡이 로그인된 채 켜져 있어야 합니다.")
    # 메인 창의 검색 Edit
    edit = win32gui.FindWindowEx(main, None, "Edit", None)
    if not edit:
        # 하위 창 안에 있는 경우
        found = []
        def cb(h, _):
            if win32gui.GetClassName(h) == "Edit":
                found.append(h)
        win32gui.EnumChildWindows(main, cb, None)
        edit = found[0] if found else 0
    if not edit:
        raise RuntimeError("카카오톡 메인 창의 검색칸을 찾지 못했습니다. 채팅방을 한 번 더블클릭해 따로 열어 두세요.")
    win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, room)
    time.sleep(0.5)
    win32gui.PostMessage(edit, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
    win32gui.PostMessage(edit, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
    t0 = time.time()
    while time.time() - t0 < wait:
        h = _find_room(win32gui, room)
        if h:
            time.sleep(0.8)
            return h
        time.sleep(0.4)
    return 0


def send_pc(room, text, retry=2):
    """채팅방 창(제목 = room)의 입력칸에 text 를 넣고 Enter."""
    win32gui, win32con = _win()
    hwnd = _find_room(win32gui, room) or _open_room(win32gui, win32con, room)
    if not hwnd:
        raise RuntimeError(f"채팅방 창 '{room}' 을 찾지 못했습니다. 카톡에서 그 방을 더블클릭해 창으로 열어 두고 다시 해 보세요.")
    edit = win32gui.FindWindowEx(hwnd, None, "RICHEDIT50W", None)
    if not edit:
        raise RuntimeError("채팅방 입력칸을 찾지 못했습니다 (카톡 버전이 바뀌었을 수 있음).")
    for k in range(retry + 1):
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, text.replace("\n", "\r\n"))
        time.sleep(0.4)
        win32gui.PostMessage(edit, win32con.WM_KEYDOWN, win32con.VK_RETURN, 0)
        win32gui.PostMessage(edit, win32con.WM_KEYUP, win32con.VK_RETURN, 0)
        time.sleep(0.8)
        # 입력칸이 비었으면 보내진 것
        n = win32gui.SendMessage(edit, win32con.WM_GETTEXTLENGTH, 0, 0)
        if n == 0:
            return True
    raise RuntimeError("Enter 를 보냈는데 입력칸이 비지 않았습니다. 창이 잠겨 있거나(잠금 모드) 방이 아닌 창일 수 있습니다.")


def log(msg):
    try:
        with open(os.path.join(C.DATA_DIR, "_kakao_log.txt"), "a", encoding="utf-8") as f:
            f.write(f"{pd.Timestamp.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except Exception:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    cfg = load_config()
    if "--test" in argv:
        text = argv[argv.index("--test") + 1] if len(argv) > argv.index("--test") + 1 else "[스마트팜] 카톡 알림 테스트"
    else:
        text = compose(cfg=cfg)
    if dry or not cfg or not cfg.get("room"):
        print(text)
        if not cfg or not cfg.get("room"):
            print(f"\n(카톡 설정이 없어 보내지 않았습니다: {CONFIG} — 앱 데이터 관리 → 카톡 알림에서 방 이름을 저장하세요)")
        return 0
    try:
        send_pc(cfg["room"], text)
        print(f"보냄 → '{cfg['room']}' ({len(text.splitlines())}줄)"); log(f"보냄 → {cfg['room']}: {text.splitlines()[0]}")
        return 0
    except Exception as e:
        print(f"실패: {e}"); log(f"실패: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
