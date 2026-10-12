# -*- coding: utf-8 -*-
"""유니팜 원본 CSV(1분 간격) → 방·날짜별 집계 캐시.
파일마다 마지막으로 읽은 바이트 위치를 기억해 새로 추가된 행만 읽는다(주간 갱신 후 몇 초).
집계는 합계·개수로 저장해 두어 부분 날짜도 정확히 합쳐진다."""
import io, os, json, glob, re
import numpy as np
import pandas as pd
from . import config as C

STATE = os.path.join(C.CACHE_DIR, "etl_state.json")
CACHE = os.path.join(C.CACHE_DIR, "env_daily.parquet")
SCHEMA = 2      # 집계 열 구성이 바뀌면 올린다 → 캐시를 처음부터 다시 만든다
SENSOR_FIELDS = [f"s{i}_{k}" for i in range(1, 5) for k in ("temp", "humidity", "co2")]
USE = ["reg_date", "run_status", "plant", "run_mode", "growth_progress", "co2", "temp", "humidity",
       "co2_val", "temp_val", "humi_val", "heater_relay", "freezer_relay", "humidity_relay",
       "dehumidity_relay", "co2_relay"] + C.ERR_FIELDS + SENSOR_FIELDS
RANGE = {"temp": (-5, 45), "humidity": (0, 101), "co2": (0, 20000)}
SUM_COLS = ["rows", "run_n", "pause_n", "reset_n", "temp_sum", "temp_sq", "temp_n", "hum_sum", "hum_n",
            "co2_sum", "co2_n", "sp_temp_sum", "sp_hum_sum", "sp_co2_sum", "sp_n",
            "heat_n", "cool_n", "humid_n", "dehum_n", "co2r_n", "err_n", "sensor_bad_n",
            "temp_bad_n", "hum_bad_n", "co2_bad_n"] + [e + "_n" for e in C.ERR_FIELDS] + [f + "_bad_n" for f in SENSOR_FIELDS]
MIN_COLS = ["temp_min", "hum_min", "co2_min", "temp_raw_min", "hum_raw_min", "co2_raw_min"]
MAX_COLS = ["temp_max", "hum_max", "co2_max", "temp_raw_max", "hum_raw_max", "co2_raw_max"]
LAST_COLS = ["day_last", "sp_temp_last", "sp_hum_last", "sp_co2_last", "mode_last", "plant"]
FIRST_COLS = ["day_first"]


def _load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {}


def _save_state(st):
    C.ensure_dirs()
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)


def raw_files():
    out = []
    for p in sorted(glob.glob(os.path.join(C.DATA_DIR, "*_*_*.csv"))):
        m = re.search(r"_(\d{4})_(\d{2})\.csv$", p)
        if m and m.group(1) in C.FARMS:
            out.append((p, m.group(1), m.group(2)))
    return out


def _read_new(path, offset):
    """offset 이후의 완결된 행만 읽어 DataFrame과 새 offset을 돌려준다."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if offset == 0:
            head = f.readline()               # 헤더(BOM 포함) 건너뜀
            offset = len(head)
        if size <= offset:
            return None, offset
        f.seek(offset)
        data = f.read()
    nl = data.rfind(b"\n")
    if nl < 0:
        return None, offset
    data = data[:nl + 1]
    df = pd.read_csv(io.BytesIO(data), names=C.RAW_FIELDS, header=None, dtype=str,
                     usecols=USE, encoding="utf-8", on_bad_lines="skip", engine="c")
    return df, offset + len(data)


def _aggregate(df, prev_day_last):
    """새 행 → 날짜별 부분 집계. prev_day_last: 직전 청크의 마지막 재배일자(리셋 감지용)."""
    df["t"] = pd.to_datetime(df["reg_date"], errors="coerce", format="%Y-%m-%dT%H:%M:%S")
    df = df.dropna(subset=["t"]).drop_duplicates("reg_date").sort_values("t")
    if df.empty:
        return None, prev_day_last
    for c in ("co2", "temp", "humidity", "co2_val", "temp_val", "humi_val") + tuple(SENSOR_FIELDS):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["day"] = pd.to_numeric(df["growth_progress"], errors="coerce")
    # 물리적으로 불가능한 값: 항목별·센서별로 따로 센다. 원값의 최대/최소는 raw_* 로 남긴다
    for c, s in (("temp", "temp"), ("humidity", "hum"), ("co2", "co2")):
        lo, hi = RANGE[c]
        df[s + "_raw"] = df[c]
        df[s + "_bad"] = (df[c].notna() & ~df[c].between(lo, hi)).astype(int)
    df["sensor_bad"] = df[["temp_bad", "hum_bad", "co2_bad"]].max(axis=1)
    for f in SENSOR_FIELDS:
        lo, hi = RANGE[f.split("_", 1)[1]]
        df[f + "_bad"] = (df[f].notna() & ~df[f].between(lo, hi)).astype(int)
    df.loc[df.temp_bad.eq(1), "temp"] = np.nan
    df.loc[df.hum_bad.eq(1), "humidity"] = np.nan
    df.loc[df.co2_bad.eq(1), "co2"] = np.nan
    on = df.run_status.eq("운전중")
    df["on"] = on.astype(int)
    df["pause"] = df.run_status.eq("일시정지").astype(int)
    dff = df.day.ffill()
    prev = pd.concat([pd.Series([prev_day_last]), dff.iloc[:-1]], ignore_index=True)
    prev.index = dff.index
    df["reset"] = ((dff - prev) < -0.5).fillna(False).astype(int)
    for src, dst in (("heater_relay", "heat"), ("freezer_relay", "cool"), ("humidity_relay", "humid"),
                     ("dehumidity_relay", "dehum"), ("co2_relay", "co2r")):
        df[dst] = (df[src].eq("작동") & on).astype(int)
    for e in C.ERR_FIELDS:
        df[e + "_n"] = df[e].fillna("정상").ne("정상").astype(int)
    df["err"] = df[[e + "_n" for e in C.ERR_FIELDS]].max(axis=1)
    # 운전중 행만 환경 통계에 사용
    for c, s in (("temp", "temp"), ("humidity", "hum"), ("co2", "co2")):
        v = df[c].where(on)
        df[s + "_v"] = v
        df[s + "_ok"] = v.notna().astype(int)
    df["temp_sq_v"] = df["temp_v"] ** 2
    for c, s in (("temp_val", "sp_temp"), ("humi_val", "sp_hum"), ("co2_val", "sp_co2")):
        df[s + "_v"] = df[c].where(on)
    df["sp_ok"] = df["sp_temp_v"].notna().astype(int)
    df["date"] = df.t.dt.normalize()
    g = df.groupby("date")
    agg = pd.DataFrame({
        "rows": g.size(), "run_n": g.on.sum(), "pause_n": g.pause.sum(), "reset_n": g.reset.sum(),
        "temp_sum": g.temp_v.sum(), "temp_sq": g.temp_sq_v.sum(), "temp_n": g.temp_ok.sum(),
        "temp_min": g.temp_v.min(), "temp_max": g.temp_v.max(),
        "hum_sum": g.hum_v.sum(), "hum_n": g.hum_ok.sum(), "hum_min": g.hum_v.min(), "hum_max": g.hum_v.max(),
        "co2_sum": g.co2_v.sum(), "co2_n": g.co2_ok.sum(), "co2_min": g.co2_v.min(), "co2_max": g.co2_v.max(),
        "sp_temp_sum": g.sp_temp_v.sum(), "sp_hum_sum": g.sp_hum_v.sum(), "sp_co2_sum": g.sp_co2_v.sum(), "sp_n": g.sp_ok.sum(),
        "heat_n": g.heat.sum(), "cool_n": g.cool.sum(), "humid_n": g.humid.sum(), "dehum_n": g.dehum.sum(), "co2r_n": g.co2r.sum(),
        "err_n": g.err.sum(), "sensor_bad_n": g.sensor_bad.sum(),
        "temp_bad_n": g.temp_bad.sum(), "hum_bad_n": g.hum_bad.sum(), "co2_bad_n": g.co2_bad.sum(),
        "temp_raw_min": g.temp_raw.min(), "temp_raw_max": g.temp_raw.max(), "hum_raw_min": g.hum_raw.min(), "hum_raw_max": g.hum_raw.max(),
        "co2_raw_min": g.co2_raw.min(), "co2_raw_max": g.co2_raw.max(),
        "day_first": g.day.first(), "day_last": g.day.last(),
        "sp_temp_last": g.temp_val.last(), "sp_hum_last": g.humi_val.last(), "sp_co2_last": g.co2_val.last(),
        "mode_last": g.run_mode.last(), "plant": g.plant.last(),
    })
    for e in C.ERR_FIELDS:
        agg[e + "_n"] = g[e + "_n"].sum()
    for f in SENSOR_FIELDS:
        agg[f + "_bad_n"] = g[f + "_bad"].sum()
    agg = agg.reset_index()
    return agg, (dff.iloc[-1] if pd.notna(dff.iloc[-1]) else prev_day_last)


def _merge(old, new):
    """같은 (farm, code, date) 행을 합친다."""
    if old is None or old.empty:
        return new
    both = pd.concat([old, new], ignore_index=True)
    key = ["farm", "code", "date"]
    both = both.sort_values(key + ["_seq"])
    g = both.groupby(key, sort=False)
    out = g[SUM_COLS].sum()
    for c in MIN_COLS:
        out[c] = g[c].min()
    for c in MAX_COLS:
        out[c] = g[c].max()
    for c in LAST_COLS:
        out[c] = g[c].last()
    for c in FIRST_COLS:
        out[c] = g[c].first()
    out["room"] = g["room"].first()
    return out.reset_index()


def _save_parquet(df, path):
    """임시 파일에 쓰고 이름을 바꿔 교체 — 앱이 읽는 도중에 반쯤 쓰인 파일을 보지 않도록."""
    tmp = path + ".tmp"
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def load_cache():
    if os.path.exists(CACHE):
        return pd.read_parquet(CACHE)
    return pd.DataFrame()


def run(progress=None):
    """증분 ETL. progress(msg) 콜백으로 진행 상황을 알린다. 처리한 파일 수를 돌려준다."""
    C.ensure_dirs()
    st = _load_state()
    cache = load_cache()
    if st.get("_schema") != SCHEMA or (not cache.empty and "temp_bad_n" not in cache):
        # 집계 방식이 바뀌었으면 처음부터 다시 만든다
        if progress:
            progress("집계 방식이 바뀌어 캐시를 처음부터 다시 만듭니다 (수 분 소요)")
        st, cache = {"_schema": SCHEMA}, pd.DataFrame()
    st["_schema"] = SCHEMA
    if not cache.empty:
        cache["_seq"] = 0
    news, n_done = [], 0
    files = raw_files()
    for i, (p, farm, code) in enumerate(files):
        key = os.path.basename(p)
        s = st.get(key, {"offset": 0, "day_last": None})
        df, new_off = _read_new(p, s["offset"])
        if df is None:
            continue
        agg, day_last = _aggregate(df, s.get("day_last"))
        if agg is not None:
            agg.insert(0, "room", C.room_name(farm, code))
            agg.insert(0, "code", code)
            agg.insert(0, "farm", farm)
            agg["_seq"] = 1
            news.append(agg)
        st[key] = {"offset": new_off, "day_last": None if day_last is None or pd.isna(day_last) else float(day_last),
                   "rows_total": s.get("rows_total", 0) + int(len(df)), "updated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}
        n_done += 1
        if progress:
            progress(f"[{i+1}/{len(files)}] {key}: +{len(df):,}행")
        # 파일이 많을 때 중간 저장
        if n_done % 10 == 0:
            cache = _merge(cache, pd.concat(news, ignore_index=True)); news = []
            _save_parquet(cache.drop(columns=["_seq"], errors="ignore"), CACHE); cache["_seq"] = 0
            _save_state(st)
    if news:
        cache = _merge(cache, pd.concat(news, ignore_index=True))
    if n_done:
        cache = cache.drop(columns=["_seq"], errors="ignore").sort_values(["farm", "code", "date"])
        _save_parquet(cache, CACHE)
        _save_state(st)
    try:
        run_minute(progress)
    except Exception as e:                       # 분 캐시가 실패해도 일집계는 살린다
        if progress:
            progress(f"냉각실 1분 캐시 실패: {e}")
    return n_done


# ───────────────────────── 분 단위 캐시 (냉각실) ─────────────────────────
# 냉각실은 하루 집계로는 '뜨거운 솥이 들어와 몇 시간 만에 식었나'를 볼 수 없어 1분 값을 따로 둔다.
# 방 목록은 config.MINUTE_ROOMS (없으면 진성팽이 냉각1실·냉각실2). 상태 파일의 min_offset 으로 새 행만 읽고,
# 처음 한 번은 파일 전체를 읽어 채운다(파일당 몇 초).
MINUTE = os.path.join(C.CACHE_DIR, "env_minute.parquet")
MIN_USE = ["reg_date", "run_status", "temp", "humidity", "temp_val", "freezer_relay", "heater_relay"]


def minute_rooms():
    return getattr(C, "MINUTE_ROOMS", {"1433": ["33", "34"]})


def _read_minute(path, offset):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if offset == 0:
            offset = len(f.readline())
        if size <= offset:
            return None, offset
        f.seek(offset)
        data = f.read()
    nl = data.rfind(b"\n")
    if nl < 0:
        return None, offset
    data = data[:nl + 1]
    df = pd.read_csv(io.BytesIO(data), names=C.RAW_FIELDS, header=None, dtype=str, usecols=MIN_USE,
                     encoding="utf-8", on_bad_lines="skip", engine="c")
    return df, offset + len(data)


def _minute_rows(df, farm, code):
    df["ts"] = pd.to_datetime(df["reg_date"], errors="coerce", format="%Y-%m-%dT%H:%M:%S").dt.floor("min")
    df = df.dropna(subset=["ts"])
    for c in ("temp", "humidity", "temp_val"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.loc[~df["temp"].between(-5, 80), "temp"] = np.nan          # 냉각실은 뜨거운 솥이 들어오면 50℃를 넘는다
    df.loc[~df["humidity"].between(*RANGE["humidity"]), "humidity"] = np.nan
    out = pd.DataFrame({"farm": farm, "code": code, "room": C.room_name(farm, code), "ts": df["ts"],
                        "temp": df["temp"].astype("float32"), "hum": df["humidity"].astype("float32"), "sp_temp": df["temp_val"].astype("float32"),
                        "run": df["run_status"].eq("운전중").astype("int8"), "cool": df["freezer_relay"].eq("작동").astype("int8"),
                        "heat": df["heater_relay"].eq("작동").astype("int8")})
    return out.drop_duplicates(["room", "ts"], keep="last")


def run_minute(progress=None):
    """냉각실 1분 캐시 증분 갱신. 처리한 파일 수를 돌려준다."""
    rooms = minute_rooms()
    st = _load_state()
    old = pd.read_parquet(MINUTE) if os.path.exists(MINUTE) else pd.DataFrame()
    if not old.empty:
        old["ts"] = pd.to_datetime(old["ts"])
    news, n = [], 0
    for p, farm, code in raw_files():
        if code not in rooms.get(farm, []):
            continue
        key = os.path.basename(p)
        s = st.get(key, {"offset": 0, "day_last": None})
        off = s.get("min_offset", 0) if not old.empty else 0
        df, new_off = _read_minute(p, off)
        if df is None:
            continue
        news.append(_minute_rows(df, farm, code))
        s["min_offset"] = new_off
        st[key] = s; n += 1
        if progress:
            progress(f"냉각실 1분 기록 {key}: +{len(df):,}행")
    if news:
        allr = pd.concat([old] + news, ignore_index=True) if not old.empty else pd.concat(news, ignore_index=True)
        allr = allr.drop_duplicates(["room", "ts"], keep="last").sort_values(["room", "ts"]).reset_index(drop=True)
        _save_parquet(allr, MINUTE)
        _save_state(st)
    return n


def load_minute(rooms=None, d1=None, d2=None):
    """냉각실 1분 표 [room, ts, temp, hum, sp_temp, run, cool, heat]. 인자를 주면 그 범위만."""
    if not os.path.exists(MINUTE):
        return pd.DataFrame()
    filt = []
    if rooms:
        filt.append(("room", "in", list(rooms)))
    if d1 is not None:
        filt.append(("ts", ">=", pd.Timestamp(d1)))
    if d2 is not None:
        filt.append(("ts", "<", pd.Timestamp(d2) + pd.Timedelta(days=1)))
    df = pd.read_parquet(MINUTE, filters=filt or None)
    df["ts"] = pd.to_datetime(df["ts"])
    return df


def _humid_setting(d):
    """가습 설정값 해석.
    제어기의 humi_type 이 '자동'이면 humi_val = 목표 습도(%), '고정'이면 가습기 작동/정지 주기를
    한 숫자로 인코딩한 값이다 (작동초 × 10 + 정지초 ÷ 10; 예 501.5 = 50초 작동 / 15초 정지).
    팽이 발이실·생육실은 대부분 '고정'이라 그대로 두면 설정습도가 500% 같은 값으로 보인다.
    캐시에 humi_type 이 없으므로 그날 마지막 원본값(sp_hum_last)의 범위로 가른다.
    """
    last = pd.to_numeric(d.get("sp_hum_last"), errors="coerce")
    pct = last.between(0, 100)                       # 습도 %로 제어하는 방·날
    # 아니면 설정습도는 없는 값으로. 하루 중에 방식이 바뀌어 평균이 섞인 날도 같이 뺀다.
    d["sp_hum"] = d["sp_hum"].where(pct & d["sp_hum"].between(0, 100))
    on = np.floor(last / 10)
    off = ((last - on * 10) * 10).round()
    ok = (~pct) & last.notna() & on.between(1, 99) & off.between(0, 99)
    d["가습작동초"] = on.where(ok)
    d["가습정지초"] = off.where(ok)
    d["가습주기"] = np.where(ok, on.fillna(0).astype("Int64").astype(str) + "초/" + off.fillna(0).astype("Int64").astype(str) + "초", None)
    return d


def daily_view(cache=None):
    """캐시 → 사람이 읽는 일집계(평균·가동률)."""
    d = load_cache() if cache is None else cache.copy()
    if d.empty:
        return d
    with np.errstate(invalid="ignore", divide="ignore"):
        d["run"] = d.run_n / d.rows
        d["pause"] = d.pause_n / d.rows
        d["temp"] = d.temp_sum / d.temp_n.replace(0, np.nan)
        d["temp_sd"] = np.sqrt(np.clip(d.temp_sq / d.temp_n.replace(0, np.nan) - d.temp ** 2, 0, None))
        d["hum"] = d.hum_sum / d.hum_n.replace(0, np.nan)
        d["co2"] = d.co2_sum / d.co2_n.replace(0, np.nan)
        for s in ("sp_temp", "sp_hum", "sp_co2"):
            d[s] = d[s + "_sum"] / d.sp_n.replace(0, np.nan)
        _humid_setting(d)
        rn = d.run_n.replace(0, np.nan)
        for s in ("heat", "cool", "humid", "dehum", "co2r"):
            d[s] = d[s + "_n"] / rn
        d["err"] = d.err_n / d.rows
        d["track_err"] = (d.temp - d.sp_temp).abs()
    d["crop"] = d.farm.map(C.CROP)
    d["date"] = pd.to_datetime(d["date"])
    return d
