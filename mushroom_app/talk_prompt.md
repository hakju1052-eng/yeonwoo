You are reading photos posted in a Korean mushroom farm's KakaoTalk team chat (연우/진성 관리자 업무방). Each item in your batch file (JSON list) has: id, file (YYYYMMDD_HHMMSS.jpg|png = upload time), sender (may be null), uploaded, pngs (one image path). Read EVERY image with the Read tool, one item at a time, then append exactly ONE JSON line per item to the output file given in your task, with Bash and a quoted heredoc:

cat >> /tmp/claude-0/tkN/out/sX.jsonl <<'EOT'
{"file": "20261109_093512.jpg", "kind": "출하지시서", "title": "서부/경인/인농 출하지시서 11/8", "sender": "안석구 팀장", "note": "", "result": {...}}
EOT

Write each line right after reading that item. Use the item's "file" exactly as given.

Decide "kind":
- "출하지시서" — a handwritten/printed 출하지시서 form (header 출 하 지 시 서, 수신 = a wholesale market, 출하일자, a table of 생산자/출하자 × 품목 × 규격 × 등급 × 수량). One photo may cover several markets (e.g. "서부/경인/인농": blocks for 서부청과, 경인농산, 인천농산물; "가락": blocks for 서울청과·구리청과; "광주": 광주중앙청과; "대전": 대전청과·대전농협(대전공판장)·대전중앙청과).
- "캡처" — a screenshot of an Excel sheet or document (작업일지, 균긁기 표, 반가공 작업지시서/작업일지, 생산일보 …). Do NOT transcribe numbers; give a short title (sheet name + date/month seen) and result {}.
- "기타" — anything else (chat screenshots, ads, misc photos). result {}.

result for 출하지시서:
{"orders":[{"ship_date":"YYYY-MM-DD","market":"서부청과","shipper":"연우","crop":"새송이",
  "lines":[{"item":"새송이","unit_kg":4,"grade":"특","qty":80},{"item":"미니새송이","unit_kg":8,"grade":"특","qty":16}],
  "total_qty":99}]}
Rules
- One order object per market block (수신/생산자 block). market: one of 서울청과·동화청과·한국청과·대전청과·대전농협·대전중앙청과·서부청과·구리청과·경인농산·인천농산물·광주중앙청과·대양청과. On a 가락 sheet the 생산자/출하자 column names the 법인 (서울청과, 구리청과 …) — each is its own order.
- ship_date: 출하일자 printed/handwritten on the form (YYYY-MM-DD). The 도착일자 is the next day — do not use it. The year is in the form; if missing use the file's year.
- shipper: 연우, 진성 or 피어리스 when the 출하자/상호 shows it (농업회사법인 주식회사 연우 → 연우; 진성 → 진성; 피어리스 → 피어리스). Otherwise the printed name.
- crop: 팽이 if the items are 팽이/팽이버섯/통팽이; 새송이 if 새송이/미니새송이/새송이(미니). Infer from the header label (e.g. the orange "새송이" or "팽이" tag) and item names.
- lines: one per table row with a quantity. item = 품목 text as written; unit_kg = 규격 (kg number, e.g. 4kg → 4, 8kg → 8, 14kg → 14, 5kg → 5); grade = the 등급 column whose count is filled (특/상/중/하) or null; qty = number of boxes (the 개 column, use the row's total 계 if the per-grade cells and 계 both exist and agree).
- total_qty = the 합계 row for that market block (sum of boxes), else null. Self-check: sum of line qty = total_qty; if not, re-read the digits.
- Numbers as plain integers. Unreadable → null, never guess. Add a short Korean "note" when something is unusual (crossed-out rows, 'KPP 파레트 이동 10 EA', re-sent photo, two forms in one photo).
- PRIVACY: never write phone numbers, 사업자번호, 계좌/예금주, addresses, 차량번호, or personal names other than shipper. Only the fields above.
- Helper files only in /tmp/claude-0/tkN/work/<batch>/. Other agents run at the same time; never write to shared paths.

When done, run wc -l on your output file and reply with ONE short line: "<batch>: <n> items written, <k> 출하지시서, <m> with totals that don't add up (file names)". Do not reply with the data.
