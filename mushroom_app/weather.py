# -*- coding: utf-8 -*-
"""외부 날씨(기상) 자료. Open-Meteo 공개 API에서 농장 좌표의 일별 기록을 받아 캐시한다.
- 키·가입 불필요, 무료. 인터넷이 안 되면 기존 캐시를 그대로 쓴다.
- 캐시: DATA_DIR\\app_cache\\weather_daily.parquet
- 좌표·시작일은 config.FARM_LATLON / WEATHER_START 에서 바꾼다.
"""
import os, json, time, datetime as dt
from urllib.request import urlopen
from urllib.parse import urlencode
import pandas as pd
from . import config as C

CACHE = os.path.join(C.CACHE_DIR, "weather_daily.parquet")
ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
FIELDS = ["temperature_2m_mean", "temperature_2m_min", "temperature_2m_max",
          "relative_humidity_2m_mean", "precipitation_sum", "wind_speed_10m_mean"]
KO = {"temperature_2m_mean": "외기온", "temperature_2m_min": "외기최저", "temperature_2m_max": "외기최고",
      "relative_humidity_2m_mean": "외기습도", "precipitation_sum": "강수mm", "wind_speed_10m_mean": "풍속"}


def _get(url, params, timeout=40, tries=3):
    """requests 가 있으면 그걸 쓴다(회사 프록시·인증서 설정을 그대로 따라감). 없으면 표준 라이브러리.
    일시적인 연결 실패가 잦아 몇 번 다시 시도한다."""
    last = None
    for i in range(tries):
        try:
            try:
                import requests
                r = requests.get(url, params=params, timeout=timeout)
                r.raise_for_status()
                return r.json()
            except ImportError:
                with urlopen(url + "?" + urlencode(params), timeout=timeout) as r:
                    return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            last = e
            if i + 1 < tries:
                time.sleep(2 * (i + 1))
    raise last


def _frame(js):
    d = js.get("daily") or {}
    if not d.get("time"):
        return pd.DataFrame()
    f = pd.DataFrame(d).rename(columns=KO)
    f["date"] = pd.to_datetime(f.pop("time"))
    for c in KO.values():
        if c not in f:
            f[c] = pd.NA
    return f[["date"] + list(KO.values())]


def fetch(d1, d2):
    """d1~d2 (YYYY-MM-DD) 일별 기록. 최근 며칠은 archive 에 아직 없어서 forecast(past_days)로 메운다."""
    lat, lon = C.FARM_LATLON
    base = {"latitude": lat, "longitude": lon, "daily": ",".join(FIELDS), "timezone": "Asia/Seoul"}
    parts = []
    # archive 는 지난 날짜만 받는다 (미래 날짜를 주면 거절당함) → 어제까지만 요청
    a2 = min(pd.Timestamp(d2), pd.Timestamp(dt.date.today()) - pd.Timedelta(days=1))
    if pd.Timestamp(d1) <= a2:
        try:
            parts.append(_frame(_get(ARCHIVE, dict(base, start_date=d1, end_date=a2.strftime("%Y-%m-%d")))))
        except Exception:
            pass
    try:                                    # 최근 3개월 + 예보 7일 (archive 공백 메우기·앞으로의 날씨)
        parts.append(_frame(_get(FORECAST, dict(base, past_days=92, forecast_days=7))))
    except Exception:
        pass
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts).sort_values("date").drop_duplicates("date", keep="last")
    return out.reset_index(drop=True)


def update(force=False):
    """캐시를 갱신하고 (행 수, 마지막 날짜) 를 돌려준다. 인터넷이 안 되면 캐시를 유지한다."""
    C.ensure_dirs()
    old = load()
    d1 = C.WEATHER_START
    d2 = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    if not force and not old.empty:
        last = pd.Timestamp(old.date.max()).date()
        if last >= dt.date.today() + dt.timedelta(days=2):      # 예보까지 이미 있으면 건너뜀
            return len(old), last
    new = fetch(d1, d2)
    if new.empty:
        return (len(old), pd.Timestamp(old.date.max()).date()) if not old.empty else (0, None)
    if not old.empty:
        new = pd.concat([old, new]).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    new["비"] = new["강수mm"].fillna(0) >= 1
    new.to_parquet(CACHE + ".tmp", index=False); os.replace(CACHE + ".tmp", CACHE)
    return len(new), pd.Timestamp(new.date.max()).date()


def load():
    if not os.path.exists(CACHE):
        return pd.DataFrame()
    f = pd.read_parquet(CACHE)
    f["date"] = pd.to_datetime(f["date"])
    if "비" not in f:
        f["비"] = f["강수mm"].fillna(0) >= 1
    return f


def status():
    f = load()
    if f.empty:
        return {"행": 0, "기간": "없음", "위치": C.FARM_PLACE}
    return {"행": len(f), "기간": f"{f.date.min():%Y-%m-%d} ~ {f.date.max():%Y-%m-%d}", "위치": C.FARM_PLACE}


if __name__ == "__main__":
    print(update(force=True))
