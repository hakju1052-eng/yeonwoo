# -*- coding: utf-8 -*-
"""아침 알림 메일. 갱신(refresh_all.bat)이 끝난 뒤 `python -m mushroom_app.notify` 로 보낸다.
설정 파일: DATA_DIR\\mail_config.json  (예시는 mail_config.example.json)
  {"smtp_host": "smtp.naver.com", "smtp_port": 465, "ssl": true,
   "user": "아이디@naver.com", "password": "앱 비밀번호", "from": "아이디@naver.com",
   "to": ["받는사람1@naver.com", "받는사람2@gmail.com"], "min_level": "주의"}
- 비밀번호는 이 파일에 본인이 직접 적는다(앱·세션은 파일을 만들지 않고 읽기만 한다).
- 보낼 내용이 하나도 없으면(모두 정상) "이상 없음" 한 줄만 보낸다. --dry-run 이면 메일 대신 화면에 출력.
"""
import os, sys, json, smtplib, ssl, datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import numpy as np
import pandas as pd
from . import config as C, etl_logs, etl_retort, analysis as A

CONFIG = os.path.join(C.DATA_DIR, "mail_config.json")
LEVEL = {"높음": 3, "주의": 2, "참고": 1}
SEV_LEVEL = {"높음": "높음", "중간": "주의", "낮음": "참고"}     # 이상 감시 심각도 → 알림 수준 (예전엔 '중간'이 '참고'로 떨어졌음)


def load_config():
    if not os.path.exists(CONFIG):
        return None
    return json.load(open(CONFIG, encoding="utf-8"))


def collect(ref_date=None):
    """알림 항목 모으기 → [{level, 분류, 내용}] 와 요약 수치."""
    items, kpi = [], {}
    today = pd.Timestamp(ref_date) if ref_date else pd.Timestamp.today().normalize()

    # 1) 환경·센서 이상 (최근 2일)
    try:
        env = A.env_daily()
        an = A.anomalies(env, days=2)
        if not an.empty:
            for sev, g in an.groupby("심각도"):
                cats = g.groupby("분류").size().sort_values(ascending=False)
                rooms = g.groupby("분류")["방"].apply(lambda s: ", ".join(sorted(set(s))[:8]) + (" 외" if s.nunique() > 8 else ""))
                for cat, n in cats.items():
                    items.append({"level": SEV_LEVEL.get(sev, "참고"), "분류": f"환경 · {cat}", "내용": f"{n}건 — {rooms[cat]}"})
        kpi["이상 항목"] = int(len(an))
        if not env.empty:
            last = env.date.max()
            if (today - last).days >= 2:
                items.append({"level": "높음", "분류": "환경 데이터", "내용": f"마지막 수집이 {last:%m/%d} — 유니팜 수집이 {(today-last).days}일째 멈춤"})
    except Exception as e:
        items.append({"level": "참고", "분류": "환경 데이터", "내용": f"확인 실패: {e}"})

    # 2) 살균 (어제·오늘 사이클)
    try:
        CY = etl_retort.load_cycles()
        if not CY.empty:
            recent = CY[CY.start >= today - pd.Timedelta(days=1, hours=12)]
            kpi["살균 사이클"] = int(len(recent))
            bad = []
            for _, r in recent.iterrows():
                f_ = []
                if r.get("구분") == "이상" or not r.완료: f_.append("미완료")
                if r.get("구분") == "배지" and r["2차유지분"] < 55: f_.append(f"2차유지 {r['2차유지분']:.0f}분")
                if r.최고온도 < 116: f_.append(f"최고 {r.최고온도:.1f}℃")
                if r.get("중단횟수", 0) > 0: f_.append(f"중단 {int(r.중단횟수)}회")
                if f_:
                    bad.append(f"{r.unit} {r.start:%m/%d %H:%M} {r.get('구분','')}: " + ", ".join(f_))
            for b in bad:
                items.append({"level": "주의", "분류": "살균", "내용": b})
            try:                                     # 2차 유지 설정을 기록 없이 낮춘 것으로 보이는 사이클 (v1.24.7)
                HA = etl_retort.hold2_all()
                HA = HA[(HA["설정의심"] == True) & (HA.start >= today - pd.Timedelta(days=1, hours=12))] if len(HA) else HA
                for _, r in HA.iterrows():
                    items.append({"level": "높음", "분류": "살균", "내용": f"⚠ 임의 설정 변경 의심 — {r.unit} {r.start:%m/%d %H:%M}: 2차 유지 {r['2차유지온도']:.1f}℃·"
                                  f"{r['2차압력']:.0f}kPa (평소 {r['평소2차온도']:.1f}℃·{r['평소2차압력']:.0f}kPa), 스팀은 평소대로 → 조작판 2차 설정 확인"})
            except Exception:
                pass
            last = CY.start.max()
            if (today - last.normalize()).days >= 2:
                items.append({"level": "주의", "분류": "살균", "내용": f"마지막 살균 기록 {last:%m/%d} — 살균솥 PC 공유 폴더 복사가 멈췄을 수 있음"})
    except Exception as e:
        items.append({"level": "참고", "분류": "살균", "내용": f"확인 실패: {e}"})

    # 3) 배양실 균긁기 불량률 (최근 7일 vs 이전 4주)
    try:
        for name, crop, thr in (("cult_scrape_paengi", "팽이", C.RULES["scrape_bad_paengi"]), ("cult_scrape_ss", "새송이", C.RULES["scrape_bad_ss"])):
            P = etl_logs.load(name)
            if P.empty:
                continue
            P = P.dropna(subset=["긁기일", "배양출고"])
            w = P[P["긁기일"] > today - pd.Timedelta(days=7)]
            prev = P[(P["긁기일"] <= today - pd.Timedelta(days=7)) & (P["긁기일"] > today - pd.Timedelta(days=35))]
            if w["배양출고"].sum() > 0:
                rate = 100 * w["불량"].sum() / w["배양출고"].sum()
                base = 100 * prev["불량"].sum() / prev["배양출고"].sum() if prev["배양출고"].sum() > 0 else np.nan
                kpi[f"{crop} 긁기불량률(7일)"] = round(rate, 2)
                if rate >= thr:
                    items.append({"level": "높음" if rate >= 2 * thr else "주의", "분류": f"배양 · {crop} 균긁기",
                                  "내용": f"최근 7일 불량률 {rate:.2f}% (기준 {thr}%, 이전 4주 {base:.2f}%) — 폐기 {int(w['불량'].sum()):,}병"})
    except Exception as e:
        items.append({"level": "참고", "분류": "배양", "내용": f"확인 실패: {e}"})

    # 4) 일지 갱신 지연
    try:
        for name, label in (("paengi_daily", "팽이 생산일지"), ("ss_daily", "새송이 일지"), ("cult_kwonji", "배양소관리(권지)")):
            D = etl_logs.load(name)
            if D.empty:
                continue
            col = "권지일" if "권지일" in D else "date"
            last = pd.to_datetime(D[col]).max()
            if (today - last).days >= 3:
                items.append({"level": "참고", "분류": "일지", "내용": f"{label} 마지막 기록 {last:%m/%d} ({(today-last).days}일 전)"})
    except Exception:
        pass

    # 5) 재고 대사 (지난달 월초 조정)
    try:
        for crop in ("팽이", "새송이"):
            R, *_ = A.stock_recon(crop)
            if R.empty:
                continue
            R = R.dropna(subset=["월초조정"])
            if R.empty:
                continue
            r = R.iloc[-1]
            if abs(r.get("조정률%", 0)) >= 1:
                items.append({"level": "참고", "분류": f"재고 · {crop}", "내용": f"{r.월} 말 장부와 실재고 차이 {r.월초조정:+,.0f}kg ({r['조정률%']:+.1f}%)"})
    except Exception:
        pass

    items.sort(key=lambda x: -LEVEL.get(x["level"], 0))
    return items, kpi


def render(items, kpi, today):
    color = {"높음": "#e0312a", "주의": "#e08a00", "참고": "#6b7160"}
    rows = "".join(f"<tr><td style='padding:6px 8px;color:{color.get(i['level'],'#333')};font-weight:700;white-space:nowrap'>{i['level']}</td>"
                   f"<td style='padding:6px 8px;white-space:nowrap'>{i['분류']}</td><td style='padding:6px 8px'>{i['내용']}</td></tr>" for i in items)
    kp = " · ".join(f"{k} {v}" for k, v in kpi.items())
    body = f"""<div style='font-family:Malgun Gothic,Apple SD Gothic Neo,sans-serif;font-size:14px;color:#222'>
<h2 style='margin:0 0 4px 0'>스마트팜 아침 점검 — {today:%Y-%m-%d (%a)}</h2>
<div style='color:#6b7160;margin-bottom:12px'>{kp}</div>
{'<table style="border-collapse:collapse;border-top:2px solid #ddd">' + rows + '</table>' if items else '<p style="color:#1baf7a;font-weight:700">이상 없음 — 어제 환경·살균·배양 기록에서 걸린 항목이 없습니다.</p>'}
<p style='color:#8a8f84;font-size:12px;margin-top:14px'>자세한 내용은 통합관리 시스템 (http://192.168.0.62:8501) 이상 감시·살균·배양실 페이지에서 확인하세요. 이 메일은 자동 발송입니다.</p></div>"""
    text = f"스마트팜 아침 점검 {today:%Y-%m-%d}\n{kp}\n" + ("\n".join(f"[{i['level']}] {i['분류']}: {i['내용']}" for i in items) or "이상 없음")
    return body, text


def send(cfg, subject, html, text):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject; msg["From"] = cfg.get("from") or cfg["user"]; msg["To"] = ", ".join(cfg["to"])
    msg.attach(MIMEText(text, "plain", "utf-8")); msg.attach(MIMEText(html, "html", "utf-8"))
    host, port = cfg["smtp_host"], int(cfg.get("smtp_port", 465))
    if cfg.get("ssl", True):
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
            s.login(cfg["user"], cfg["password"]); s.sendmail(msg["From"], cfg["to"], msg.as_string())
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ssl.create_default_context()); s.login(cfg["user"], cfg["password"]); s.sendmail(msg["From"], cfg["to"], msg.as_string())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    dry = "--dry-run" in argv
    today = pd.Timestamp.today().normalize()
    items, kpi = collect(today)
    cfg = load_config()
    min_level = LEVEL.get((cfg or {}).get("min_level", "참고"), 1)
    shown = [i for i in items if LEVEL.get(i["level"], 1) >= min_level]
    html, text = render(shown, kpi, today)
    n_hi = sum(1 for i in shown if i["level"] == "높음"); n_mid = sum(1 for i in shown if i["level"] == "주의")
    subject = f"[스마트팜] {today:%m/%d} 아침 점검 — " + (f"높음 {n_hi}·주의 {n_mid}" if shown else "이상 없음")
    if dry or cfg is None:
        print(subject); print(text)
        if cfg is None:
            print(f"\n(메일 설정 파일이 없어 보내지 않았습니다: {CONFIG})")
        return 0
    send(cfg, subject, html, text)
    print(f"보냄: {subject} → {', '.join(cfg['to'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
