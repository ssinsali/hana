"""로그인·회원가입 (비밀번호는 PBKDF2 해시로 저장)."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from pathlib import Path
from typing import Any

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
USERS_PATH = _APP_DIR / "data" / "users.json"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
_MIN_PASSWORD_LEN = 8
_PBKDF2_ITERATIONS = 260_000
_HASH_PREFIX = "pbkdf2_sha256"


def _load_users() -> dict[str, Any]:
    if not USERS_PATH.exists():
        return {"users": {}}
    try:
        raw = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"users": {}}
    if not isinstance(raw, dict):
        return {"users": {}}
    users = raw.get("users")
    if not isinstance(users, dict):
        raw["users"] = {}
    return raw


def _save_users(data: dict[str, Any]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
    )
    return (
        f"{_HASH_PREFIX}${_PBKDF2_ITERATIONS}$"
        f"{salt.hex()}${digest.hex()}"
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, iter_s, salt_hex, digest_hex = password_hash.split("$", 3)
        if algo != _HASH_PREFIX:
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _auth_secret(key: str, default: str | None = None) -> str | None:
    try:
        section = st.secrets.get("auth", {})
        value = section.get(key, default)
    except Exception:
        return default
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def ensure_seed_admin() -> None:
    """secrets에 admin이 있고 사용자가 없으면 초기 관리자 생성."""
    data = _load_users()
    if data["users"]:
        return
    username = _auth_secret("admin_username")
    password = _auth_secret("admin_password")
    if not username or not password:
        return
    data["users"][username] = {
        "password_hash": _hash_password(password),
        "display_name": _auth_secret("admin_display_name", username) or username,
        "role": "admin",
    }
    _save_users(data)


def register_user(
    username: str,
    password: str,
    display_name: str = "",
) -> tuple[bool, str]:
    username = username.strip()
    display_name = display_name.strip()
    data = _load_users()
    bootstrap = not data["users"]

    if not display_name:
        return False, "이름을 입력해 주세요."
    if len(display_name) > 40:
        return False, "이름은 40자 이하여야 합니다."
    if not _USERNAME_RE.match(username):
        return False, "아이디는 영문·숫자·._- 3~32자여야 합니다."
    if len(password) < _MIN_PASSWORD_LEN:
        return False, f"비밀번호는 {_MIN_PASSWORD_LEN}자 이상이어야 합니다."
    if password.strip() != password or " " in password:
        return False, "비밀번호에 앞뒤/중간 공백을 넣을 수 없습니다."

    if username in data["users"]:
        return False, "이미 사용 중인 아이디입니다."

    data["users"][username] = {
        "password_hash": _hash_password(password),
        "display_name": display_name,
        "role": "admin" if bootstrap else "user",
    }
    _save_users(data)
    if bootstrap:
        return True, "최초 관리자 계정이 생성되었습니다. 로그인해 주세요."
    return True, "회원가입이 완료되었습니다. 로그인해 주세요."


def authenticate(username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    data = _load_users()
    user = data["users"].get(username)
    if not user or not _verify_password(password, user.get("password_hash", "")):
        return False, "아이디 또는 비밀번호가 올바르지 않습니다."
    return True, user.get("display_name") or username


def is_authenticated() -> bool:
    return bool(st.session_state.get("auth_user"))


def current_user() -> str | None:
    return st.session_state.get("auth_user")


def current_display_name() -> str:
    return st.session_state.get("auth_display_name") or current_user() or ""


def login_session(username: str, display_name: str) -> None:
    st.session_state.auth_user = username
    st.session_state.auth_display_name = display_name


def logout_session() -> None:
    for key in ("auth_user", "auth_display_name", "confirm_shutdown", "tracker"):
        st.session_state.pop(key, None)


def render_auth_gate() -> bool:
    """미로그인 시 로그인/회원가입 UI를 그리고 False 반환."""
    ensure_seed_admin()
    if is_authenticated():
        return True

    st.title("드릴 파손 카운트")
    st.caption("계속하려면 로그인하거나 회원가입하세요.")

    tab_login, tab_signup = st.tabs(["로그인", "회원가입"])

    with tab_login:
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("아이디")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", type="primary", width="stretch")
        if submitted:
            ok, msg = authenticate(username, password)
            if ok:
                login_session(username.strip(), msg)
                st.rerun()
            st.error(msg)

    with tab_signup:
        if not _load_users()["users"]:
            st.info("등록된 계정이 없습니다. **첫 가입자가 관리자**가 됩니다.")
        with st.form("signup_form", clear_on_submit=False):
            new_user = st.text_input("아이디 (영문·숫자·._-)", key="signup_user")
            new_name = st.text_input("이름", key="signup_name")
            new_pw = st.text_input("비밀번호 (8자 이상)", type="password", key="signup_pw")
            new_pw2 = st.text_input("비밀번호 확인", type="password", key="signup_pw2")
            signed = st.form_submit_button("회원가입", type="primary", width="stretch")
        if signed:
            if new_pw != new_pw2:
                st.error("비밀번호 확인이 일치하지 않습니다.")
            else:
                ok, msg = register_user(new_user, new_pw, new_name)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.caption("비밀번호는 해시로만 저장됩니다. 평문 저장하지 않습니다.")
    return False


def render_logout_controls() -> None:
    name = current_display_name()
    st.caption(f"로그인: **{name}**")
    if st.button("로그아웃", width="stretch"):
        logout_session()
        st.rerun()
