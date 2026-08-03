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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
USERS_PATH = _APP_DIR / "data" / "users.json"

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_MIN_PASSWORD_LEN = 8
_PBKDF2_ITERATIONS = 260_000
_HASH_PREFIX = "pbkdf2_sha256"
_GH_API = "https://api.github.com"
_PLACEHOLDER_TOKENS = (
    "github_pat_xxxxxxxx",
    "ghp_xxxxxxxx",
    "발급한_토큰",
    "YOUR_TOKEN",
    "github_pat_여기에실제토큰",
    "github_pat_실제발급토큰",
)
_PLACEHOLDER_REPOS = (
    "계정명/저장소명",
    "YOUR_GITHUB_ID/REPO",
    "YOUR_GITHUB_ID/hana",
    "owner/repo",
)
_TOKEN_RE = re.compile(r"^(ghp_|github_pat_)[A-Za-z0-9_]+$")


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
    token = _github_secret("token") or ""
    repo = (_github_secret("repo") or "").strip().strip("/")
    if not token or not repo:
        return False
    if token in _PLACEHOLDER_TOKENS or "실제" in token or "xxxxxxxx" in token:
        return False
    if not _TOKEN_RE.match(token):
        return False
    if repo in _PLACEHOLDER_REPOS:
        return False
    if not _REPO_RE.match(repo):
        return False
    return True


def _github_cfg() -> tuple[str, str, str, str]:
    token = (_github_secret("token") or "").strip().strip('"').strip("'")
    repo = (_github_secret("repo") or "").strip().strip("/").strip('"').strip("'")
    path = (_github_secret("path") or "data/users.json").strip().lstrip("/")
    branch = (_github_secret("branch") or "main").strip() or "main"

    if not token or token in _PLACEHOLDER_TOKENS or "실제" in token:
        raise RuntimeError(
            "Secrets [github].token 에 예시 문구가 들어 있습니다. "
            "GitHub에서 Generate 한 뒤 복사한 실제 토큰(영문·숫자, github_pat_... 또는 ghp_...)을 넣으세요."
        )
    try:
        token.encode("ascii")
    except UnicodeEncodeError as e:
        raise RuntimeError(
            "Secrets [github].token 에 한글이 들어가 있습니다. "
            "예시 문구가 아니라 GitHub에서 발급한 실제 토큰만 넣으세요."
        ) from e
    if not _TOKEN_RE.match(token):
        raise RuntimeError(
            "Secrets [github].token 형식이 올바르지 않습니다. "
            "github_pat_... 또는 ghp_... 로 시작하는 실제 토큰이어야 합니다."
        )
    if not repo or repo in _PLACEHOLDER_REPOS:
        raise RuntimeError(
            "Secrets [github].repo 에는 실제 저장소를 영문으로 넣으세요. "
            "예: ssinsali/hana"
        )
    if not _REPO_RE.match(repo):
        raise RuntimeError(
            f"Secrets [github].repo 형식이 올바르지 않습니다: {repo!r}. "
            "영문 owner/repo 형식이어야 합니다. 예: ssinsali/hana"
        )
    try:
        path.encode("ascii")
        branch.encode("ascii")
    except UnicodeEncodeError as e:
        raise RuntimeError(
            "Secrets [github].path / branch 에는 영문·숫자만 사용하세요."
        ) from e
    return token, repo, path, branch


def _contents_url(repo: str, path: str, branch: str | None = None) -> str:
    # path 세그먼트만 인코딩 (슬래시는 경로 구분자로 유지)
    encoded_path = "/".join(
        urllib.parse.quote(part, safe="") for part in path.split("/") if part
    )
    url = f"{_GH_API}/repos/{repo}/contents/{encoded_path}"
    if branch:
        url += f"?ref={urllib.parse.quote(branch, safe='')}"
    return url


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
    except UnicodeEncodeError as e:
        raise RuntimeError(
            "GitHub 요청에 한글 등 비ASCII 문자가 있습니다. "
            "Secrets의 token/repo 를 다시 확인해 주세요. "
            "token은 GitHub에서 복사한 영문 실제 값이어야 합니다."
        ) from e
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
    url = _contents_url(repo, path, branch)
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
    url = _contents_url(repo, path)
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
