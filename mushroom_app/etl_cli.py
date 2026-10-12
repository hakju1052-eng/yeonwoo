# -*- coding: utf-8 -*-
"""명령줄에서 캐시 갱신: python -m mushroom_app.etl_cli  (환경 CSV 증분 + 바뀐 일지 + 반가공 현황)"""
import sys, time
from . import etl_env, etl_logs, etl_retort, weather, config as C


def main():
    t0 = time.time()
    print(f"데이터 폴더: {C.DATA_DIR}")
    n = etl_env.run(progress=lambda m: print("  " + m, flush=True))
    print(f"환경 데이터: {n}개 파일 처리 ({time.time()-t0:.0f}초)")
    t1 = time.time()
    m = etl_logs.run(progress=lambda s: print("  " + s, flush=True), force="--force-logs" in sys.argv)
    print(f"작업일지: {m}개 파일 읽음 ({time.time()-t1:.0f}초)")
    t2 = time.time()
    r = etl_retort.run(progress=lambda s: print("  " + s, flush=True))
    print(f"살균솥 기록: {r}개 파일 읽음 ({time.time()-t2:.0f}초)")
    try:
        n_w, last_w = weather.update()
        print(f"날씨({C.FARM_PLACE}): {n_w}일치, 마지막 {last_w}")
    except Exception as e:
        print(f"날씨: 건너뜀 ({e})")
    t3 = time.time()
    try:   # 반가공센터 현황 (copy_banagong.bat 로 복사해 둔 업무자료 사본을 읽음)
        from . import etl_banagong
        n_b = etl_banagong.run(progress=lambda s: print("  " + s, flush=True))
        print(f"반가공 현황: {n_b}개 문서 ({time.time()-t3:.0f}초)")
    except Exception as e:
        print(f"반가공 현황: 건너뜀 ({e})")
    t4 = time.time()
    try:   # 팩스 판매원표(경리 SynologyDrive\스캔) — 새 파일만 AI 로 읽음 (v1.26.0)
        from . import etl_fax, ai_chat, config as C
        if getattr(C, "FAX_AUTO_API", False) and ai_chat.api_key():
            n_f, left = etl_fax.run(progress=lambda s: print("  " + s, flush=True))
            print(f"팩스 판매원표: {n_f}개 읽음, 남은 {left}개 ({time.time()-t4:.0f}초)")
        else:
            etl_fax.load()                      # 예약 직접 판독이 index.json 을 바꿨으면 표만 다시 만든다 (v1.27.0)
            st_ = etl_fax.status()
            print(f"팩스 판매원표: API 자동 읽기 꺼짐 - 읽은 {st_['done']}개, 남은 {st_['left']}개 (Claude 예약 작업이 읽음)")
    except Exception as e:
        print(f"팩스 판매원표: 건너뜀 ({e})")


if __name__ == "__main__":
    main()
