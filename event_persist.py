"""이벤트 로그 영구 저장 (GitHub data/event_log.csv).

Streamlit Cloud 절전/재시작 후에도 유지되도록 Secrets [github] 로
저장소 CSV를 읽고 씁니다. 로컬에서 토큰이 없으면 JSON만 사용합니다.
"""
from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path

import streamlit as st

from auth import github_file_get, github_file_put, github_store_enabled, _github_secret
from breakage_tracker import BreakageTracker
from data_import import (
    EVENT_LOG_CSV_FILENAME,
    load_event_log_from_csv,
    parse_event_log_table,
    records_to_event_csv_dataframe,
    write_event_log_csv,
)

_APP_DIR = Path(__file__).resolve().parent
LOCAL_EVENT_CSV = _APP_DIR / "data" / EVENT_LOG_CSV_FILENAME
_SHA_KEY = "event_log_csv_sha"


def event_log_github_path() -> str:
    return (_github_secret("event_log_path") or f"data/{EVENT_LOG_CSV_FILENAME}").strip().lstrip("/")


def cloud_persist_enabled() -> bool:
    return github_store_enabled()


def records_to_csv_bytes(records: list[dict]) -> bytes:
    return records_to_event_csv_dataframe(records).to_csv(index=False).encode("utf-8-sig")


def sync_tracker_from_github(tracker: BreakageTracker) -> tuple[int, str | None]:
    """
    GitHub CSV → tracker.
    반환: (건수, 오류메시지 or None)
    """
    if not cloud_persist_enabled():
        return 0, None
    try:
        raw, sha = github_file_get(event_log_github_path())
    except Exception as e:
        return 0, str(e)

    st.session_state[_SHA_KEY] = sha
    if raw is None:
        # 원격에 없으면 로컬 CSV/현재 tracker 유지
        if LOCAL_EVENT_CSV.exists():
            count, errors = tracker.replace_from_event_csv(LOCAL_EVENT_CSV)
            return count, ("; ".join(errors[:3]) if errors else None)
        return len(tracker.state.detail_records), None

    text = raw.decode("utf-8-sig") if raw else ""
    if not text.strip():
        tracker.state.detail_records = []
        tracker.state.events = []
        tracker._sync_counts_and_status(tracker.state)
        return 0, None

    import pandas as pd

    df = pd.read_csv(StringIO(text))
    records, errors = parse_event_log_table(df)
    tracker.state.detail_records = records
    tracker.state.events = []
    tracker._sync_counts_and_status(tracker.state)
    try:
        write_event_log_csv(records, LOCAL_EVENT_CSV)
    except OSError:
        pass
    return len(records), ("; ".join(errors[:3]) if errors else None)


def sync_tracker_from_local_csv(tracker: BreakageTracker) -> int:
    if not LOCAL_EVENT_CSV.exists():
        return 0
    count, _ = tracker.replace_from_event_csv(LOCAL_EVENT_CSV)
    return count


def persist_tracker_to_github(tracker: BreakageTracker) -> str | None:
    """
    tracker → 로컬 CSV + GitHub CSV.
    성공 시 None, 실패 시 오류 메시지.
    """
    records = tracker.state.detail_records
    csv_bytes = records_to_csv_bytes(records)
    try:
        write_event_log_csv(records, LOCAL_EVENT_CSV)
    except OSError:
        pass

    if not cloud_persist_enabled():
        return None

    path = event_log_github_path()
    message = f"chore: update event_log.csv ({len(records)} rows) {datetime.now():%Y-%m-%d %H:%M}"
    sha = st.session_state.get(_SHA_KEY)
    try:
        new_sha = github_file_put(path, csv_bytes, message, sha)
        st.session_state[_SHA_KEY] = new_sha
        return None
    except Exception as e:
        # sha 충돌 시 최신 조회 후 1회 재시도
        if "409" in str(e) or "sha" in str(e).lower():
            try:
                _, latest_sha = github_file_get(path)
                new_sha = github_file_put(path, csv_bytes, message, latest_sha)
                st.session_state[_SHA_KEY] = new_sha
                return None
            except Exception as e2:
                return str(e2)
        return str(e)


def ensure_tracker_loaded(tracker: BreakageTracker) -> None:
    """세션당 1회 원격/로컬 CSV에서 로드."""
    if st.session_state.get("event_log_loaded"):
        return
    if cloud_persist_enabled():
        count, err = sync_tracker_from_github(tracker)
        st.session_state.event_log_loaded = True
        st.session_state.event_log_source = "github"
        st.session_state.event_log_count = count
        if err:
            st.session_state.event_log_load_error = err
        return
    if LOCAL_EVENT_CSV.exists() and not tracker.state.detail_records:
        count = sync_tracker_from_local_csv(tracker)
        st.session_state.event_log_count = count
        st.session_state.event_log_source = "local_csv"
    else:
        st.session_state.event_log_source = "json"
        st.session_state.event_log_count = len(tracker.state.detail_records)
    st.session_state.event_log_loaded = True


def save_tracker(tracker: BreakageTracker) -> str | None:
    """JSON 저장 + (가능하면) GitHub CSV 영구 저장."""
    tracker.save()
    err = persist_tracker_to_github(tracker)
    if err is None:
        st.session_state.event_log_count = len(tracker.state.detail_records)
        st.session_state.pop("event_log_save_error", None)
    else:
        st.session_state.event_log_save_error = err
    return err
