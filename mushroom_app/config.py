# -*- coding: utf-8 -*-
"""경로·재배동 매핑·상수. 환경변수 MUSHROOM_DATA_DIR 로 데이터 폴더를 바꿀 수 있다."""
import os

APP_NAME = "통합관리 시스템"          # 2026-10-08 스마트팜 분석 시스템 → 통합관리 시스템 (반가공 현황까지 함께 보므로)
APP_AUTHOR = "phj"
VERSION = "1.27.1"
AI_MODEL = "claude-sonnet-4-5"   # AI 질문창 모델 (Anthropic API)
# AI 질문창 API 키 파일. 정확한 경로를 알면 AI_KEY_FILE 에 적는다(가장 먼저 본다).
# 비워두면 환경변수 ANTHROPIC_API_KEY → DATA_DIR → Downloads 바로 아래 폴더 순으로 AI_KEY_NAMES 이름을 찾는다.
AI_KEY_FILE = r""
# 팩스 판매원표 읽기 (경영 자료 › 출하 대사, v1.26.0): 이 날짜 이후 팩스만, 한 번 갱신에 최대 몇 개, 모델(비우면 AI_MODEL)
FAX_SINCE = "2026-08-01"
FAX_MAX_PER_RUN = 60
FAX_MODEL = ""
FAX_AUTO_API = False      # 갱신(etl_cli) 때 API 로 팩스를 읽을지. False 면 화면 버튼으로만 — 평소엔 Claude 예약 작업이 직접 읽는다 (v1.27.0)
AI_KEY_NAMES = ["anthropic_key.txt", "claude_api_key.txt", "claude_api.txt", "cluade_api.txt"]
SESSION_DAYS = 30                # 로그인 유지 기간(일). 0 으로 두면 유지 기능을 안 씀

HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("MUSHROOM_DATA_DIR") or os.path.join(HOME, "Downloads", "unifarm")
CACHE_DIR = os.path.join(DATA_DIR, "app_cache")

# 작업일지 원본 위치 (회사 공유드라이브). 경로가 없으면 DATA_DIR 아래 사본 폴더로 대체한다.
LOG_SOURCES = {
    "paengi": [r"Z:\안석구\송곡리 팽이 포장실\팽이일지", os.path.join(DATA_DIR, "팽이일지")],
    "ss":     [r"Z:\안석구\연우농원 새송이양송이\작업일지", os.path.join(DATA_DIR, "새송이일지")],
    "cult":   [r"Z:\배양센터\배양소관리(작업량)", os.path.join(DATA_DIR, "배양소일지")],
    # 연우농원 폴더 최상위의 요약 파일(새송이 방별 포장량.xlsx 등)
    "ss_extra": [r"Z:\안석구\연우농원 새송이양송이", os.path.join(DATA_DIR, "기타일지", "연우농원")],
    # 재배 관리자 폴더 (재배일보·원재료입고·전기세·가스)
    "kwon_paengi": [r"Z:\권석현\팽이", os.path.join(DATA_DIR, "기타일지", "권석현_팽이")],
    "kwon_ss": [r"Z:\권석현\새송이", os.path.join(DATA_DIR, "기타일지", "권석현_새송이")],
    # 재배 관리자(정규철) 작업 일지: 입병(솥 단위) 종합관리·매출분석
    "jung_paengi": [r"Z:\정규철\작업 일지\팽이버섯", os.path.join(DATA_DIR, "기타일지", "정규철_팽이")],
    "jung_ss": [r"Z:\정규철\작업 일지\새송이 작업 일지", os.path.join(DATA_DIR, "기타일지", "정규철_새송이")],
    # 배양센터 최상위 파일 (종균 관리 대장·배양실 작업 일지)
    "cult_extra": [r"Z:\배양센터", os.path.join(DATA_DIR, "기타일지", "배양센터")],
    # 살균솥 PC의 기록 폴더(데이터수집, *.cld). mirror_nas.bat 이 공유폴더를 Downloads\unifarm\살균솥 으로 복사한다
    # (네트워크 경로를 직접 넣으면 PC가 꺼져 있을 때 앱이 느려지므로 사본 폴더만 읽는다)
    "retort": [os.path.join(DATA_DIR, "살균솥")],
    # 경리(사무실) 자료 — '경영 자료' 페이지(관리자 전용). SynologyDrive_문서_복사.bat 이 만든 사본으로 대체
    "office": [r"Z:\사무실\SynologyDrive", os.path.join(HOME, "Downloads", "SynologyDrive_사본")],
}
# 자료실 페이지에 보여줄 폴더 (이름: 경로 목록 — 앞에서부터 있는 것을 씀; 앱은 로그온 세션에서 돌아 Z:를 직접 읽을 수 있다)
#   하위 폴더(예: 26년 월 마감보고\8월\)까지 2단계 내려가며 찾는다. 앱에서 올린 파일은 DATA_DIR\자료실 에 저장
DOC_SOURCES = {
    "팽이 월간 종합보고 (포장실)": [r"Z:\안석구\송곡리 팽이 포장실\팽이일지\2026년\결산자료", os.path.join(DATA_DIR, "기타일지", "월간보고_팽이")],
    "새송이 월간 보고": [r"Z:\안석구\연우농원 새송이양송이\작업일지\2026년\2026년 결산", os.path.join(DATA_DIR, "기타일지", "월간보고_새송이")],
    "월말 보고 (재배)": [r"Z:\권석현\월말보고", os.path.join(DATA_DIR, "기타일지", "권석현_월말보고")],
    "월 마감보고 (배양센터 26년)": [r"Z:\배양센터\26년 월 마감보고", os.path.join(DATA_DIR, "기타일지", "배양센터_마감보고", "26년")],
    "월 마감보고 (배양센터 25년)": [r"Z:\배양센터\25년 월 마감보고", os.path.join(DATA_DIR, "기타일지", "배양센터_마감보고", "25년")],
    "자동 보고서 (주간·월간)": [os.path.join(DATA_DIR, "분석결과", "01_리포트")],
    "앱에서 올린 자료": [os.path.join(DATA_DIR, "자료실")],
}
# 팽이·새송이 월간 보고 폴더는 해마다 바뀌므로(…\2026년\결산자료) 연도가 바뀌면 위 경로의 연도만 고친다
DOC_EXT = (".pptx", ".pptm", ".ppt", ".pdf", ".xlsx", ".xls", ".docx", ".hwp", ".mp4", ".png", ".jpg")
DOC_DEPTH = 2            # 하위 폴더 몇 단계까지 볼지
DOC_MAX_MB = 300         # 이보다 큰 파일은 목록에만 표시(내려받기 버튼 없음)

# 사건 기록 — 관리 방식·설비가 바뀐 날. 추이 그래프에 점선으로 표시되고 병목 찾기의 기간 나누기에 쓰인다. (날짜, 짧은 이름, 설명)
EVENTS = [
    ("2026-04-09", "새송이 관리자 교체", "새송이 재배 설정값 운영 방식 변경(설정값 제어 본격 사용)"),
    ("2026-07-01", "팽이 억제 스케줄 변경", "발이실 억제 하강 일차가 11→12일차 시작으로 늦춰짐(추정)"),
    ("2026-08-15", "솥단위 중간소독 시작", "접종 시 솥 단위 중간 소독 시작 (배양센터 8월 마감보고)"),
    ("2026-08-25", "지하수 사용 중단", "접종기 세척수를 지하수→상수도로 전환. 지하수(저수탱크) 세균이 계절 따라 늘어 노즐 주위 오염 유발 (배양센터 8월 마감보고 결론)"),
    ("2026-08-26", "접종기 마개기 분해 청소", "접종기 부품 사이 오염물 제거"),
    ("2026-09-03", "접종기 마개기 멸균 사용", "접종 마개기·병 고정 클램프 100℃ 60분 살균 소독 후 사용"),
]

# 농장 위치 (외부 날씨 자료용). 충북 음성군 감곡면 일대 — 이천 장호원과 인접
FARM_LATLON = (37.08, 127.63)
FARM_PLACE = "충북 음성(감곡) 부근"
WEATHER_START = "2025-01-01"        # 날씨를 이 날짜부터 받아 둔다 (일지가 2025년부터 있음)

# 살균 기록 요약에서 '이 값 이상 유지한 시간(분)'을 계산할 기준값 (온도 ℃ 기준)
RETORT_LEVELS = [100, 115, 118, 121]

# 전기 계량기 → 원가 배분 역할. 월말보고 산식과 맞춤 (전기세.xlsx 의 계량기 명칭과 실제 용도가 다를 수 있음)
#   팽이생육: 팽이 입병수로 나눔 / 새송이생육: 새송이 입병수 / 배양공통: 두 작물 입병수 합으로 나눔 / 기타: 원가 제외
METER_ROLE = {"팽이배양동": "팽이생육", "팽이발이동": "배양공통", "팽이생육동": "배양공통",
              "새송이1": "새송이생육", "새송이2": "새송이생육", "새송이3": "새송이생육", "기숙사": "기타", "사무실": "기타"}
LOG_MIN_YEAR = 2025            # 이 연도 폴더('YYYY년')부터 읽음
LOG_SKIP_DIRS = ("백업", "결산", "#recycle", "청과자료", "월간", "기타", "일지양식")   # 경로에 이 말이 있으면 건너뜀


def log_dir(kind):
    for d in LOG_SOURCES[kind]:
        if os.path.isdir(d):
            return d
    return LOG_SOURCES[kind][-1]

FARMS = {"1377": "연우1번재배사", "1378": "연우2번재배사", "1433": "진성팽이재배사"}
CROP = {"1377": "새송이", "1378": "새송이", "1433": "팽이"}

# 재배동 코드 → 방 이름
ROOM = {
    "1377": {**{f"{i:02d}": f"A{i:02d}" for i in range(1, 11)}, **{f"{i+10:02d}": f"B{i:02d}" for i in range(1, 11)}},
    "1378": {**{f"{i:02d}": f"C{i:02d}" for i in range(1, 11)}, **{f"{i+10:02d}": f"D{i:02d}" for i in range(1, 15)}},
    "1433": {**{f"{i:02d}": f"발이실{i:02d}" for i in range(1, 19)}, **{f"{i+18:02d}": f"생육실{i:02d}" for i in range(1, 13)},
             "31": "배양실1", "32": "배양실2", "33": "냉각1실", "34": "냉각실2"},
}
# 새송이 일지 표기 "2-3" → B03
SS_BLOCK = {"1": "A", "2": "B", "3": "C", "4": "D"}
# 재배 분석에서 빼는 방 (저온 보관실·미가동). 데이터 상태·이상 감시에는 포함
EXCLUDE_ROOMS = {"B10", "C02", "D09", "D10", "D11", "D12", "D13", "D14"}
# 이상 감시에서도 빼는 방 (재배와 무관한 용도)
MONITOR_EXCLUDE = {"C02", "D09"}      # C02 = 창고, D09 = 관리목록에서 빠진 방(유니팜 API에만 남아 있음)
PAENGI_BALSIL_ROTATION = [f"발이실{i:02d}" for i in range(3, 19)]   # 1·2번은 다른 용도

# 팽이 QC 절대 판정선 — 품질 기준서(Z:\안석구\송곡리 팽이 포장실\QC\260619_QC_재구성.xlsx, 2026-06-19 서식 B)의
# 항목별 점수 앵커로 계산한 값. 가중치: 개체충실도 25 · 갓크기 20 · 갓상태 6 · 갓기형 5 · 갓병내균일 9 · 대길이 15 · 대병내균일 9 · 대병간균일 6 · 병해 5
#   모든 항목 '정상·양호' 앵커(개체 8 / 갓 8.5mm→8.2 / 상태·기형·병해 10 / 균일 9 / 대길이 9.5)  → 88.3
#   균일성 항목이 모두 '다소 편차'(8)                                                     → 85.1
#   '편차 큼'(7) + '키작은개체·개체수 부족'(7)                                             → 78.7
#   '심함' 수준(개체 6·갓 10mm·상태/기형 5·균일 6·대길이 7)                                → 55~60
# 서식 A(2026-06-18 이전, 가중치 20/20/15/15/10/10/10)는 앵커가 없어 절대 판정을 하지 않는다. 서식이 달라 점수도 서로 비교하지 않는다.
RAIN_MARK_MM = 3.0        # 배치 탐색기 습도 그래프에 "비 온 날" 동그라미를 찍는 최소 강수량(mm). 이슬비는 뺀다
QC_BANDS = {"B": [(85, "양호"), (79, "보통"), (0, "미흡")]}

SEASON = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
          6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}

# 팽이 발이실 억제 하강 표준(1~6월 방식): 일차 → 설정온도
PAENGI_SUPPRESS_STD = {11: 14.5, 12: 11.0, 13: 5.5, 14: 4.0}

# 이상 감시 기준 — v1.22.0 에서 2026-01~10 실측 분포로 다시 정함 (주의가 너무 많아 산만하다는 의견 반영)
#   원칙: 정상 운전에서도 늘 생기는 값(습도 포화·냉각실 뜨거운 솥·입실 직후 온도)은 이상으로 보지 않고,
#         같은 방에서 며칠 이어지는 건 하루 한 줄이 아니라 '한 건(N일 연속)'으로 묶는다.
RULES = {
    # ── 환경(유니팜) ──
    "track_err_c": 2.0,        # 온도 추종오차 °C (1.5→2.0). 입실·입상 당일과 다음날, 냉각실·배양실을 뺀 운전일의 99.5% 가 이 안
    "track_skip_days": 1,      # 입실·입상(재배일자 리셋) 뒤 이 일수까지는 추종오차를 보지 않음 (병이 들어와 식는 중)
    "hum_sat_max": 115.0,      # 습도 101~115% 는 센서 포화(물기)로 보고 '센서 이상'에서 뺌 (넘은 날의 99% 가 114% 이하)
    "cool_room_temp_max": 80.0,  # 냉각실은 45℃ 넘는 뜨거운 솥이 매일 들어오므로 80℃까지는 정상 값
    "sensor_bad_min": 30,      # 센서 이상: 불가능한 값이 하루 이 분 이상
    "err_min": 120,            # 장비 에러: 에러 상태가 하루 이 분 이상 (60→120. 대부분 저온실 '안심한계값 초과' 경보)
    "log_stale_days": 2,
    "sensor_temp_max": 45.0,
    "sensor_temp_min": -5.0,
    # ── 배양 ──
    "scrape_bad_paengi": 0.8,  # 균긁기 불량률 % (주간) 0.5→0.8. 여름 아닌 때 주간 99% 가 0.82% 이하
    "scrape_bad_ss": 0.4,      # 0.3→0.4. 여름 아닌 때 최대 0.28%
    "scrape_high_mult": 2.5,   # 기준의 이 배 이상이면 '높음' (팽이 2.0%, 새송이 1.0%)
    "kwonji_bad": 1.0,         # 권지 불량률 % (주간) 0.5→1.0. 여름 아닌 때 주간 95% 가 0.63% 이하
    "inoc_loss": 0.2,
    "cult_temp_c": 2.0,        # 배양실 설정과 실측 차이 °C (1.0→2.0). 배양실2는 늘 1.3℃ 낮게 운전하므로 그보다 크게 벗어날 때만
    "cult_hum": 85,            # 배양실 습도 % (그대로)
    "cult_days": 3,            # 배양실 조건이 최근 며칠 이상일 때
    # ── 새송이 입상 ──
    "ss_bottles_tol": 0.20,    # 방당 병수가 같은 동(A~D) 최근 120일 중앙값에서 ±20% 넘게 벗어날 때 (예전 고정 범위 5,000~7,500병은
                               #   방이 작은 D동이 늘 걸려서 없앰). 비교할 입상이 8번 미만이면 판정 안 함
    "suppress_tol_c": 1.5,
    # ── 냉각실·살균 ──
    "cool_slow_ratio": 1.5,    # 냉각 느림: 평소(같은 방·같은 솥수) 소요시간의 이 배 (1.3→1.5)
    "cool_slow_add_min": 90,   #   이면서 평소보다 이 분 넘게 더 걸릴 때 (60→90). 사건의 약 2% 가 걸림
    "overcool_c": 3.0,         # 냉각실 과냉: 설정보다 이 ℃ 넘게 낮은 시간이
    "overcool_min": 60,        #   하루 이 분 이상 (30→60)
    # ── 카톡 냉각기 가동률 급등 ──
    "surge_up": 0.20,          # 평소보다 가동률이 이만큼(20%p) 높고
    "surge_ratio": 1.5,        #   평소의 1.5배 이상인 날이 이틀 연속이거나
    "surge_spike": 0.35,       #   하루라도 35%p 넘게 높으면 알림 (아침의 47%→23%에만 뜸)
    # ── 배치 이력서 판정 (z = 평균에서 표준편차 몇 배) ──
    "z_warn": 1.5,             # 주의 (1.0→1.5)
    "z_bad": 2.0,              # 나쁨 (1.5→2.0)
    "z_diff": 2.0,             # 방향 없는 항목의 '평소와 다름' (1.5→2.0). '약간 다름'은 없앰
}

RAW_FIELDS = [
    "reg_date", "timestamp", "farm_code", "dong_code", "run_status", "plant", "run_mode",
    "growth_progress", "co2", "temp", "humidity", "illum",
    "s1_co2", "s1_temp", "s1_humidity", "s1_ilum", "s2_co2", "s2_temp", "s2_humidity", "s2_ilum",
    "s3_co2", "s3_temp", "s3_humidity", "s3_ilum", "s4_co2", "s4_temp", "s4_humidity", "s4_ilum",
    "co2_type", "co2_val", "co2_val2", "temp_type", "temp_val", "temp_val2",
    "humi_type", "humi_val", "humi_val2", "ilum_type", "ilum_val", "ilum_val2",
    "co2_relay", "heater_relay", "freezer_relay", "humidity_relay", "dehumidity_relay", "ilum_relay",
    "co2_error", "temp_error", "humi_error", "ilum_error", "vent_error", "heater_error", "freeze_error",
    "humidify_error", "dehumidify_error", "ilum_output_error", "sensor_error", "device_error",
    "sensor1_error", "sensor2_error", "sensor3_error", "sensor4_error",
]
ERR_FIELDS = ["co2_error", "temp_error", "humi_error", "sensor_error", "device_error",
              "sensor1_error", "sensor2_error", "sensor3_error", "sensor4_error"]


def room_name(farm, code):
    return ROOM.get(str(farm), {}).get(str(code).zfill(2), f"{farm}-{code}")


def ensure_dirs():
    os.makedirs(CACHE_DIR, exist_ok=True)


# ───────── 반가공센터 현황 (v1.21.0~) ─────────
# 업무자료 사본 폴더(copy_banagong.bat 이 \\jinsung\반가공\업무자료 → Downloads\반가공자료 로 복사) 후보 — 처음 있는 곳을 쓴다
BANAGONG_SRC = [os.path.join(HOME, "Downloads", "반가공자료")]
BANAGONG_CACHE = os.path.join(CACHE_DIR, "banagong")           # 화면용 JSON
BANAGONG_COPY_BAT = os.path.join(HOME, "Downloads", "copy_banagong.bat")
