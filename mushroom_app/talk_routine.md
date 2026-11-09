# 톡방 사진 자동 수집·판독 절차 (Claude 예약 작업용, v1.33.0, 2026-11-09)

08:20·14:20 KST, 팩스 판독 직후 같은 대화에서 실행. 사용자(학주)가 방식 2(크롬 자동 저장)를 택함 — 사진 다운로드는 이 절차의 일부로 승인됨.

1. 크롬 확장(Claude in Chrome, 메인PC): tabs_context_mcp(createIfEmpty) → navigate
   https://talkcloud.kakao.com/teamchat/ca324bc18828f99401267d1b55c7c5a1/photo → 3초 대기 → 첫 썸네일(약 334,409) 클릭 → 뷰어.
   find "저장하기 link" 로 ref 확보. JS: window.__meta = 본문 텍스트에서 '유형\n' 위치 기준 [type,size,dim,uploaded], '뷰어' 다음 줄 = sender.
2. 반복(최신→과거): __meta() → 이미 index(app_cache\talk\index.json)에 있는 사진(uploaded 분 단위 + size 일치)이면 건너뜀,
   아니면 '저장하기'(ref) 클릭 → 4초 → ArrowRight → 2초. 아는 사진 3장 연속 또는 40장 넘으면 중단, Escape.
   저장한 사진마다 meta.json {파일이름: {sender, uploaded "YYYY-MM-DD HH:MM"}}. 파일 이름 = 올린 시각 12시간제 YYYYMMDD_HHMMSS (오후 1:26 → 012631).
   같은 초에 여러 장이면 크롬이 ' (1)', ' (2)' 를 붙임.
3. device_bash: ~/mnt/Downloads 의 2026*_??????*.(jpg|jpeg|png) → mv -n → ~/mnt/Downloads/unifarm/톡클라우드/ (' (n)' → '_n').
4. device_stage_files 로 새 사진 받기(한 번에 40MB 이하), PC 의 mushroom_app\talk_direct.py·talk_prompt.md stage.
   /tmp/claude-0/tkN: pending <폴더> -- index.json pending.json → render pending.json img items.json index.json meta.json
   → 하위 에이전트(talk_prompt.md 의 tkN 치환, 5~7장 배치, out/sX.jsonl) → merge index.json items.json results.jsonl new_index.json report.txt
   → app_cache\talk\index.json 커밋(expectedMtimeMs) → C:\Users\phj\Downloads\unifarm\app\REFRESH_REQUEST.txt 커밋.
5. 크롬이 응답 없거나 브라우저 선택을 요구하면 B 는 건너뛰고 보고만. 보고: 사진 수, 출하지시서 요약, 지시↔팩스 차이.
