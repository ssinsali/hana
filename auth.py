"""로그인·회원가입 (비밀번호는 PBKDF2 해시로 저장).

Streamlit Cloud에서는 [github] Secrets로 data/users.json 을
GitHub에 읽고 써서, 재시작 후에도 가입 계정이 유지됩니다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
USERS_PATH = _APP_DIR / "data" / "users.json"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
_MIN_PASSWORD_LEN = 8
_PBKDF2_ITERATIONS = 260_000
_HASH_PREFIX = "pbkdf2_sha256"
_GH_API = "https://api.github.com"


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


def _github_secret(key: str, default: str | None = None) -> str | None:
    try:
        section = st.secrets.get("github", {})
        value = section.get(key, default)
    except Exception:
        return default
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def github_store_enabled() -> bool:
    return bool(_github_secret("token") and _github_secret("repo"))


def _github_cfg() -> tuple[str, str, str, str]:
    token = _github_secret("token") or ""
    repo = (_github_secret("repo") or "").strip().strip("/")
    path = (_github_secret("path") or "data/users.json").lstrip("/")
    branch = _github_secret("branch") or "main"
    return token, repo, path, branch


def _normalize_users(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"users": {}}
    users = raw.get("users")
    if not isinstance(users, dict):
        return {"users": {}}
    return {"users": users}


def _github_request(
    method: str,
    url: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "drill-broken-auth")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {e.code}: {detail}") from e


def _load_users_local() -> dict[str, Any]:
    if not USERS_PATH.exists():
        return {"users": {}}
    try:
        return _normalize_users(json.loads(USERS_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return {"users": {}}


def _save_users_local(data: dict[str, Any]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _fetch_github_users() -> tuple[dict[str, Any], str | None]:
    """(users_data, sha). 파일이 없으면 빈 DB와 sha=None."""
    token, repo, path, branch = _github_cfg()
    url = f"{_GH_API}/repos/{repo}/contents/{path}?ref={branch}"
    try:
        info = _github_request("GET", url, token)
    except RuntimeError as e:
        if "404" in str(e):
            return {"users": {}}, None
        raise
    content_b64 = str(info.get("content") or "").replace("\n", "")
    sha = str(info.get("sha") or "") or None
    if not content_b64:
        return {"users": {}}, sha
    text = base64.b64decode(content_b64).decode("utf-8")
    return _normalize_users(json.loads(text)), sha


def _push_github_users(data: dict[str, Any], sha: str | None) -> None:
    token, repo, path, branch = _github_cfg()
    url = f"{_GH_API}/repos/{repo}/contents/{path}"
    content = base64.b64encode(
        (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    ).decode("ascii")
    body: dict[str, Any] = {
        "message": "chore: update users.json (signup)",
        "content": content,
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    _github_request("PUT", url, token, body)


def _load_users(*, force_remote: bool = False) -> dict[str, Any]:
    if github_store_enabled():
        cache_key = "users_db_cache"
        if not force_remote and cache_key in st.session_state:
            return _normalize_users(st.session_state[cache_key])
        data, sha = _fetch_github_users()
        st.session_state[cache_key] = data
        st.session_state["users_db_sha"] = sha
        try:
            _save_users_local(data)
        except OSError:
            pass
        return data
    return _load_users_local()


def _save_users(data: dict[str, Any]) -> None:
    data = _normalize_users(data)
    try:
        _save_users_local(data)
    except OSError:
        pass

    if not github_store_enabled():
        return

    # 충돌 시 최신 sha로 1회 재시도
    sha = st.session_state.get("users_db_sha")
    try:
        _push_github_users(data, sha)
    except RuntimeError as e:
        if "409" not in str(e) and "422" not in str(e):
            raise
        latest, sha = _fetch_github_users()
        # 원격에 이미 같은 아이디가 있으면 원격 우선 유지 후 병합
        merged = {"users": {**latest["users"], **data["users"]}}
        _push_github_users(merged, sha)
        data = merged

    st.session_state["users_db_cache"] = data
    _, sha = _fetch_github_users()
    st.session_state["users_db_sha"] = sha


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


def ensure_seed_admin() -> None:
    """secrets에 admin이 있고 사용자가 없으면 초기 관리자 생성."""
    try:
        data = _load_users(force_remote=True)
    except RuntimeError:
        data = _load_users_local()
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
    try:
        _save_users(data)
    except RuntimeError:
        # GitHub 미설정·오류 시 로컬만이라도 유지
        _save_users_local(data)


def register_user(
    username: str,
    password: str,
    display_name: str = "",
) -> tuple[bool, str]:
    username = username.strip()
    display_name = display_name.strip()
    try:
        data = _load_users(force_remote=True)
    except RuntimeError as e:
        return False, f"계정 저장소 연결 실패: {e}"

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
    try:
        _save_users(data)
    except RuntimeError as e:
        return False, f"GitHub 저장 실패: {e}"

    if bootstrap:
        return True, "최초 관리자 계정이 생성되었습니다. 로그인해 주세요."
    return True, "회원가입이 완료되었습니다. 로그인해 주세요."


def authenticate(username: str, password: str) -> tuple[bool, str]:
    username = username.strip()
    try:
        data = _load_users(force_remote=True)
    except RuntimeError:
        data = _load_users_local()
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


# 초기화 등 위험 작업을 허용할 관리자 아이디
ADMIN_USERNAMES = frozenset({"2120401"})


def is_admin() -> bool:
    return (current_user() or "") in ADMIN_USERNAMES


def login_session(username: str, display_name: str) -> None:
    st.session_state.auth_user = username
    st.session_state.auth_display_name = display_name


def logout_session() -> None:
    for key in (
        "auth_user",
        "auth_display_name",
        "confirm_shutdown",
        "tracker",
        "users_db_cache",
        "users_db_sha",
    ):
        st.session_state.pop(key, None)


def render_auth_gate() -> bool:
    """미로그인 시 로그인/회원가입 UI를 그리고 False 반환."""
    ensure_seed_admin()
    if is_authenticated():
        return True

    st.title("드릴 파손 카운트")
    st.caption("계속하려면 로그인하거나 회원가입하세요.")
    if github_store_enabled():
        st.caption("계정은 GitHub에 저장되어 재시작 후에도 유지됩니다.")
    else:
        st.warning(
            "GitHub 계정 연동 Secrets가 없습니다. "
            "Streamlit Cloud에서는 앱 재시작 시 가입 정보가 초기화될 수 있습니다."
        )

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
