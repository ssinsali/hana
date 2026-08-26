"""
GitHub / Streamlit Cloud 배포용 진입점 (이 파일 1개만 실행).

실행: streamlit run app.py
Main file path (Streamlit Cloud): app.py

데이터 원본: GitHub Secrets 설정 시 data/event_log.csv (영구 저장)
  → 앱에서 등록한 이벤트도 GitHub CSV에 자동 저장됩니다.

로컬 PC에서는 메인 앱을 직접 실행하세요.
  python "Drill broken.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
_MAIN_SCRIPT = _APP_DIR / "Drill broken.py"

if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))


def _run_main_app() -> None:
    if not _MAIN_SCRIPT.exists():
        st.error(f"메인 스크립트를 찾을 수 없습니다: {_MAIN_SCRIPT.name}")
        st.stop()
    source = _MAIN_SCRIPT.read_text(encoding="utf-8")
    code = compile(source, str(_MAIN_SCRIPT), "exec")
    # Streamlit 컨텍스트로 실행해 로컬 __main__ 자동실행 블록은 건너뜀
    exec(code, {"__name__": "__streamlit__", "__file__": str(_MAIN_SCRIPT)})


_run_main_app()
