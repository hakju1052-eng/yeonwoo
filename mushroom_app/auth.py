# -*- coding: utf-8 -*-
"""로그인. 사용자 목록은 DATA_DIR\\users.json 에 두고, 비밀번호는 원문이 아니라
해시(PBKDF2-HMAC-SHA256, 20만 회)만 저장한다. 계정 만들기·비밀번호 바꾸기는 메인PC에서
  python -m mushroom_app.auth add 홍길동
처럼 직접 실행한다(입력한 비밀번호는 화면에 찍히지 않고 파일에도 원문으로 남지 않는다).

- 아이디 = 한글 이름 그대로 (예: 정규철). 이름이 겹치면 뒤에 숫자를 붙인다 (김철수2).
- 비밀번호 = 숫자만, PIN_MIN 자리 이상. 너무 뻔한 숫자(1111·123456 등)는 막는다.

등급(role)
  관리자 : 모든 페이지
  직원   : 원가·재고 대사·데이터 관리·계정 관리만 빼고 전부 (STAFF_HIDE 에서 조정)
"""
import os, re, sys, json, hmac, base64, getpass, secrets, hashlib, time, unicodedata
from . import config as C

USERS = os.path.join(C.DATA_DIR, "users.json")
ITER = 200_000
PIN_MIN = 8          # 숫자 비밀번호 최소 자릿수. 8자리면 잠금 규칙과 합쳐 사실상 못 뚫는다
ROLES = ("관리자", "직원")
# 등급별로 볼 수 있는 페이지
PAGES_ALL = ["개요", "일일 확인", "배치 탐색기", "배치 이력", "계절·월별 비교", "배양실", "종균·액탱크",
             "살균", "원가", "재고 대사", "이상 감시", "수리 이력", "보고서", "자료실", "AI 질문", "데이터 관리", "계정 관리",
             "반가공 현황", "경영 자료"]   # 반가공 현황·경영 자료는 맨 끝 — 메뉴 구분선 아래에 따로 보인다(app._nav_css). 경영 자료는 반가공 현황 아래(v1.26.7)
STAFF_HIDE = ("원가", "경영 자료", "재고 대사", "데이터 관리", "계정 관리")   # 직원에게 감출 페이지 — 여기만 고치면 된다 (반가공 현황은 v1.21.3부터 직원도 봄, 경영 자료는 v1.25.0 관리자 전용)
PAGES_STAFF = [p for p in PAGES_ALL if p not in STAFF_HIDE]

_FAILS = {}          # 아이디별 최근 실패 (메모리)
MAX_FAIL, LOCK_SEC = 5, 300          # 5회 틀리면 5분, 그 뒤로는 틀릴 때마다 점점 길게


def norm(s):
    """아이디 정리 — 한글 자모 조합 방식(NFC/NFD)이 달라도 같은 이름으로 보게 한다."""
    return unicodedata.normalize("NFC", str(s or "")).strip()


def check_pin(pw):
    """숫자 비밀번호 검사. 문제가 없으면 None, 있으면 사유 문자열."""
    if not re.fullmatch(r"\d+", pw or ""):
        return "숫자만 쓸 수 있습니다."
    if len(pw) < PIN_MIN:
        return f"{PIN_MIN}자리 이상이어야 합니다."
    if len(set(pw)) == 1:
        return "같은 숫자만 반복할 수 없습니다."
    d = [int(c) for c in pw]
    if all(b - a == 1 for a, b in zip(d, d[1:])) or all(a - b == 1 for a, b in zip(d, d[1:])):
        return "123456 처럼 이어지는 숫자는 쓸 수 없습니다."
    if len(pw) % 2 == 0 and pw[: len(pw) // 2] == pw[len(pw) // 2:]:
        return "앞뒤가 같은 숫자의 반복은 쓸 수 없습니다."
    if len(set(pw)) < 3:
        return "서로 다른 숫자를 3가지 이상 쓰세요 (11112222·12121212 같은 건 안 됩니다)."
    return None


def _lock_left(uid):
    """잠겨 있으면 남은 초, 아니면 0. 실패가 쌓일수록 잠금이 길어진다."""
    f = _FAILS.get(uid)
    if not f or f[0] < MAX_FAIL:
        return 0
    wait = LOCK_SEC * (2 ** min((f[0] - MAX_FAIL) // MAX_FAIL, 4))      # 5분 → 10 → 20 → 40 → 80분
    left = wait - (time.time() - f[1])
    return int(left) if left > 0 else 0


def _hash(pw, salt):
    return base64.b64encode(hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, ITER)).decode()


def load():
    if not os.path.exists(USERS):
        return {}
    try:
        return json.load(open(USERS, encoding="utf-8"))
    except Exception:
        return {}


def save(users):
    C.ensure_dirs()
    json.dump(users, open(USERS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    try:                                  # 이 파일은 본인만 읽게 (윈도우에서는 효과가 제한적)
        os.chmod(USERS, 0o600)
    except OSError:
        pass


def verify(uid, pw):
    """맞으면 사용자 dict, 틀리면 None, 잠겨 있으면 ('locked', 남은 초)."""
    uid = norm(uid)
    left = _lock_left(uid)
    if left:
        return ("locked", left)
    u = load().get(uid)
    ok = False
    if u:
        salt = base64.b64decode(u["salt"])
        ok = hmac.compare_digest(_hash(pw, salt), u["hash"])
    if ok:
        _FAILS.pop(uid, None)
        return {"id": uid, "name": u.get("name", uid), "role": u.get("role", "직원")}
    f = _FAILS.get(uid)
    n = (f[0] + 1) if f else 1
    _FAILS[uid] = (n, time.time())
    return None


def set_password(uid, pw, name=None, role=None):
    uid = norm(uid)
    users = load()
    u = users.get(uid, {})
    salt = secrets.token_bytes(16)
    u["salt"] = base64.b64encode(salt).decode()
    u["hash"] = _hash(pw, salt)
    if name:
        u["name"] = name
    if role:
        u["role"] = role
    u["name"] = uid          # 아이디가 곧 이름
    u.setdefault("role", "직원")
    users[uid] = u
    save(users)
    return u


LOG = os.path.join(C.DATA_DIR, "users_log.txt")
ACCESS = os.path.join(C.DATA_DIR, "access_log.tsv")     # 누가 언제 무엇을 봤는지
ACCESS_MAX_MB = 5
SESSIONS = os.path.join(C.DATA_DIR, "sessions.json")    # 로그인 유지용 (브라우저 쿠키 <-> 사람)
COOKIE = "smartfarm_sid"
SESSION_DAYS = getattr(C, "SESSION_DAYS", 30)           # 이 기간이 지나면 다시 로그인


def _sess_load():
    try:
        return json.load(open(SESSIONS, encoding="utf-8"))
    except Exception:
        return {}


def _sess_save(d):
    try:
        C.ensure_dirs()
        json.dump(d, open(SESSIONS, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        os.chmod(SESSIONS, 0o600)
    except OSError:
        pass


def _tok_key(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()      # 원본 토큰은 저장하지 않는다


def session_new(uid, days=None):
    """로그인 유지 표식을 만들고 브라우저에 줄 토큰을 돌려준다."""
    days = SESSION_DAYS if days is None else days
    token = secrets.token_urlsafe(32)
    d = {k: v for k, v in _sess_load().items() if v.get("exp", 0) > time.time()}
    d[_tok_key(token)] = {"id": uid, "exp": time.time() + days * 86400,
                          "from": time.strftime("%Y-%m-%d %H:%M")}
    _sess_save(d)
    return token


def session_user(token):
    """토큰이 살아 있고 그 계정이 아직 있으면 사용자 dict, 아니면 None."""
    if not token:
        return None
    rec = _sess_load().get(_tok_key(token))
    if not rec or rec.get("exp", 0) <= time.time():
        return None
    u = load().get(rec["id"])
    if not u:
        return None
    return {"id": rec["id"], "name": u.get("name", rec["id"]), "role": u.get("role", "직원")}


def session_drop(token):
    if not token:
        return
    d = _sess_load()
    if d.pop(_tok_key(token), None) is not None:
        _sess_save(d)


def sessions_drop_user(uid):
    """그 사람의 로그인 유지를 전부 끊는다 (비밀번호 변경·계정 삭제 때)."""
    uid = norm(uid)
    d = _sess_load()
    n = {k: v for k, v in d.items() if v.get("id") != uid}
    if len(n) != len(d):
        _sess_save(n)


def log_access(user, event, detail=""):
    """접속 기록 한 줄. 실패해도 앱은 그냥 돌아간다."""
    try:
        if os.path.exists(ACCESS) and os.path.getsize(ACCESS) > ACCESS_MAX_MB * 1024 * 1024:
            os.replace(ACCESS, ACCESS + ".1")           # 너무 커지면 한 번만 보관하고 새로 시작
        first = not os.path.exists(ACCESS)
        with open(ACCESS, "a", encoding="utf-8") as f:
            if first:
                f.write("시각\t이름\t등급\t구분\t내용\n")
            u = user if isinstance(user, dict) else {"id": str(user)}
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{u.get('id','-')}\t{u.get('role','-')}\t{event}\t{detail}\n")
    except OSError:
        pass


def read_access(days=30):
    """최근 기록을 표로. 없으면 빈 표."""
    import pandas as pd
    if not os.path.exists(ACCESS):
        return pd.DataFrame(columns=["시각", "이름", "등급", "구분", "내용"])
    try:
        d = pd.read_csv(ACCESS, sep="\t", encoding="utf-8", on_bad_lines="skip")
    except Exception:
        return pd.DataFrame(columns=["시각", "이름", "등급", "구분", "내용"])
    d["시각"] = pd.to_datetime(d["시각"], errors="coerce")
    d = d.dropna(subset=["시각"])
    if days:
        d = d[d["시각"] >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    return d.sort_values("시각", ascending=False)


def log_change(who, what):
    """계정 변경 기록. 누가 언제 무엇을 했는지만 남기고 비밀번호는 남기지 않는다."""
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t{who}\t{what}\n")
    except OSError:
        pass


def admins(users=None):
    users = load() if users is None else users
    return [k for k, v in users.items() if v.get("role") == "관리자"]


def set_role(uid, role, by="-"):
    """등급 바꾸기. 마지막 관리자를 직원으로 내리는 것은 막는다."""
    uid = norm(uid); users = load()
    if uid not in users or role not in ROLES:
        return "없는 계정이거나 등급이 올바르지 않습니다."
    if role != "관리자" and users[uid].get("role") == "관리자" and len(admins(users)) <= 1:
        return "마지막 남은 관리자입니다. 다른 사람을 관리자로 올린 뒤에 바꾸세요."
    users[uid]["role"] = role; save(users)
    log_change(by, f"등급 변경 {uid} → {role}")
    return None


def delete_user(uid, by="-"):
    """계정 지우기. 자기 자신과 마지막 관리자는 못 지운다."""
    uid = norm(uid); users = load()
    if uid not in users:
        return "없는 계정입니다."
    if uid == norm(by):
        return "자기 계정은 지울 수 없습니다."
    if users[uid].get("role") == "관리자" and len(admins(users)) <= 1:
        return "마지막 남은 관리자는 지울 수 없습니다."
    users.pop(uid); save(users); _FAILS.pop(uid, None); sessions_drop_user(uid)
    log_change(by, f"계정 삭제 {uid}")
    return None


def unlock(uid):
    """비밀번호를 여러 번 틀려 잠긴 계정을 바로 풀어준다."""
    _FAILS.pop(norm(uid), None)


def is_locked(uid):
    return _lock_left(norm(uid))


def can_see(role):
    return PAGES_ALL if role == "관리자" else PAGES_STAFF


# ───────────────────────── 화면 ─────────────────────────
def _cookie_get(name):
    import streamlit as st
    try:
        return (st.context.cookies or {}).get(name)
    except Exception:
        return None


def _cookie_set(name, value, days):
    """브라우저에 쿠키를 심는다. https 로 들어온 경우에만 Secure 를 붙인다."""
    import streamlit as st
    import streamlit.components.v1 as components
    try:
        proto = (st.context.headers or {}).get("x-forwarded-proto", "")
    except Exception:
        proto = ""
    sec = "; Secure" if proto == "https" else ""
    age = int(days * 86400) if days else 0
    extra = f"; max-age={age}" if age else ""
    components.html(f"""<script>
      window.parent.document.cookie = "{name}={value}{extra}; path=/; SameSite=Lax{sec}";
    </script>""", height=0)


def gate():
    """로그인 화면. 통과하면 사용자 dict, 아니면 화면을 세우고 멈춘다.
    사용자 파일이 아예 없으면 로그인을 걸지 않는다(사내망 전용으로 쓰던 그대로)."""
    import streamlit as st
    users = load()
    if not users:
        return {"id": "-", "name": "", "role": "관리자", "무인증": True}
    if st.session_state.get("_user"):
        return st.session_state["_user"]
    tok = _cookie_get(COOKIE)                     # 이 브라우저에 로그인 표식이 남아 있나
    if tok:
        u = session_user(tok)
        if u:
            st.session_state["_user"] = u
            st.session_state["_sid"] = tok
            return u
    st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        icon = os.path.join(os.path.dirname(__file__), "assets_eco_icon.png")
        if os.path.exists(icon):
            st.image(icon, width=64)
        st.markdown(f"### {C.APP_NAME}")
        with st.form("login"):
            uid = st.text_input("이름", placeholder="예: 홍길동")
            pw = st.text_input("비밀번호", type="password", placeholder="숫자")
            keep = st.checkbox("이 기기에서 로그인 유지", value=True,
                               help=f"{SESSION_DAYS}일 동안 이 브라우저에서는 바로 들어옵니다. 여러 사람이 쓰는 PC라면 끄세요.")
            go = st.form_submit_button("로그인", type="primary", use_container_width=True)
        if go:
            r = verify(uid, pw)
            if isinstance(r, tuple) and r[0] == "locked":
                log_access({"id": norm(uid), "role": "-"}, "잠김")
                m = r[1] // 60 + 1
                st.error(f"비밀번호를 여러 번 틀려 잠겼습니다. {m}분 뒤에 다시 해 주세요.")
            elif r:
                st.session_state["_user"] = r
                log_access(r, "로그인", "유지" if keep else "")
                if keep:
                    t = session_new(r["id"])
                    st.session_state["_sid"] = t
                    _cookie_set(COOKIE, t, SESSION_DAYS)
                    time.sleep(0.4)               # 쿠키가 심어질 틈을 준다
                st.rerun()
            else:
                log_access({"id": norm(uid) or "(빈칸)", "role": "-"}, "로그인 실패")
                st.error("이름이나 비밀번호가 맞지 않습니다.")
        st.caption("이름은 한글 그대로, 비밀번호는 숫자입니다. 계정은 관리자에게 문의하세요.")
    st.stop()


def sidebar_box():
    """사이드바 아래쪽에 누구로 들어왔는지 + 로그아웃."""
    import streamlit as st
    u = st.session_state.get("_user")
    if not u:
        return
    st.caption(f"{u['name']} ({u['role']})")
    if st.button("로그아웃", use_container_width=True):
        log_access(u, "로그아웃")
        session_drop(st.session_state.pop("_sid", None))
        _cookie_set(COOKIE, "", 0)
        st.session_state.pop("_user", None)
        time.sleep(0.4)
        st.rerun()


# ───────────────────────── 명령줄 ─────────────────────────
def _ask_pw():
    a = getpass.getpass(f"새 비밀번호(숫자 {PIN_MIN}자리 이상, 화면에 안 보임): ")
    bad = check_pin(a)
    if bad:
        print("  " + bad); return None
    if a != getpass.getpass("한 번 더: "):
        print("  두 번 입력이 다릅니다."); return None
    return a


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "list"
    users = load()
    if cmd == "list":
        print(f"사용자 파일: {USERS}")
        if not users:
            print("  (없음 — 계정을 하나도 안 만들면 로그인 없이 지금처럼 열립니다)")
        for k, v in users.items():
            print(f"  {k:12s} {v.get('role','직원')}")
        return 0
    if cmd in ("add", "passwd") and len(argv) >= 2:
        uid = norm(argv[1])
        if not uid:
            print("이름을 입력하세요."); return 1
        if cmd == "add" and uid in users:
            print(f"'{uid}' 는 이미 있습니다. 동명이인이면 뒤에 숫자를 붙이세요(예: {uid}2). "
                  "비밀번호만 바꾸려면 3번(비밀번호 바꾸기)을 쓰세요."); return 1
        if cmd == "passwd" and uid not in users:
            print(f"'{uid}' 계정이 없습니다."); return 1
        role = None
        if cmd == "add":
            role = input("등급 [관리자/직원] (그냥 엔터면 직원): ").strip() or "직원"
            if role not in ROLES:
                print(f"등급은 {' 또는 '.join(ROLES)} 중 하나여야 합니다."); return 1
        pw = _ask_pw()
        if not pw:
            return 1
        u = set_password(uid, pw, None, role)
        print(f"{'만들었습니다' if cmd == 'add' else '비밀번호를 바꿨습니다'}: {uid} ({u['role']})")
        return 0
    if cmd == "role" and len(argv) >= 3:
        uid, role = norm(argv[1]), argv[2].strip()
        if uid not in users or role not in ROLES:
            print("아이디를 찾을 수 없거나 등급이 올바르지 않습니다."); return 1
        users[uid]["role"] = role; save(users); print(f"{uid} → {role}"); return 0
    if cmd == "remove" and len(argv) >= 2:
        uid = norm(argv[1])
        if uid not in users:
            print("없는 아이디입니다."); return 1
        if input(f"{uid} 계정을 지울까요? (y) ").strip().lower() != "y":
            return 1
        users.pop(uid); save(users); print("지웠습니다."); return 0
    print("사용법: python -m mushroom_app.auth [list | add 이름 | passwd 이름 | role 이름 등급 | remove 이름]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
