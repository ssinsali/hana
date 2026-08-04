"""Gemini(무료)로 현재 필터 파손 결과를 해석합니다."""
from __future__ import annotations

import hashlib
from typing import Any

import pandas as pd
import streamlit as st

from breakage_stats import (
    count_by_column,
    count_drill_lot_grouped,
    monthly_trend,
    summary_metrics,
)

_DEFAULT_MODEL = "gemini-3.5-flash-lite"
_VALID_MODEL_FALLBACKS = (
    "gemini-3.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
)


def _gemini_secret(key: str, default: str | None = None) -> str | None:
    try:
        section = st.secrets.get("gemini", {})
        value = section.get(key, default)
    except Exception:
        return default
    if value is None:
        return default
    text = str(value).strip().strip('"').strip("'")
    # 사용자가 AIza_AQ... 처럼 잘못 붙인 경우 보정
    if text.startswith("AIza_AQ."):
        text = text[len("AIza_") :]
    return text or default


def gemini_configured() -> bool:
    key = _gemini_secret("api_key") or ""
    if not key:
        return False
    if "여기에" in key or "your" in key.lower() or key.endswith("..."):
        return False
    # AI Studio 키: 기존 AIzaSy... 또는 최신 AQ....
    return key.startswith("AIza") or key.startswith("AQ.")


def build_analysis_context(detail_df: pd.DataFrame, map_metrics: dict[str, Any] | None = None) -> str:
    """LLM에 보낼 집계 요약(원본 전체 로그는 보내지 않음)."""
    if detail_df is None or detail_df.empty:
        return "현재 필터 조건에 해당하는 파손 이벤트가 없습니다."

    sm = summary_metrics(detail_df)
    lines: list[str] = ["# 드릴 파손 현황 요약 (필터 적용)"]

    if sm.get("date_min") is not None and sm.get("date_max") is not None:
        d0 = pd.Timestamp(sm["date_min"]).strftime("%Y-%m-%d")
        d1 = pd.Timestamp(sm["date_max"]).strftime("%Y-%m-%d")
        lines.append(f"- 분석 기간: {d0} ~ {d1}")

    lines.append(f"- 총 파손 건수: {sm['total_events']}건")
    lines.append(f"- 관련 설비: {sm['equipment_count']}대")
    lines.append(f"- 제품 종류: {sm['product_count']}종")
    lines.append(f"- 등록자 수: {sm['registrar_count']}명")
    avg = sm.get("avg_drill_usage")
    if avg is not None:
        lines.append(f"- 평균 드릴사용량: {avg:,.0f}")

    if map_metrics:
        lines.append(
            f"- 설비 맵 지표: 총 발생 {map_metrics.get('total_events', 0)}회, "
            f"파손 설비 {map_metrics.get('broken_equipment', 0)}대, "
            f"미파손 {map_metrics.get('intact_equipment', 0)}대, "
            f"파손 설비율 {map_metrics.get('breakage_rate_pct', 0):.1f}%"
        )

    def _append_top(title: str, df: pd.DataFrame, label_col: str, n: int = 8) -> None:
        if df is None or df.empty:
            return
        count_col = "파손 건수" if "파손 건수" in df.columns else df.columns[1]
        lines.append(f"\n## {title} (상위 {min(n, len(df))}개)")
        for _, row in df.head(n).iterrows():
            label = row.get(label_col, "")
            count = row.get(count_col, "")
            lines.append(f"- {label}: {count}")

    _append_top("설비별 파손", count_by_column(detail_df, "설비", 8), "설비")
    _append_top("제품코드별 파손", count_by_column(detail_df, "제품코드", 8), "제품코드")
    _append_top("파손 형태별", count_by_column(detail_df, "파손 형태", 8), "파손 형태")
    _append_top("드릴 랏(통합)", count_drill_lot_grouped(detail_df, 8), "드릴 랏(통합)")
    _append_top("툴설명별", count_by_column(detail_df, "툴설명", 8), "툴설명")

    trend = monthly_trend(detail_df)
    if not trend.empty:
        period_col = "기간" if "기간" in trend.columns else trend.columns[0]
        count_col = "파손 건수" if "파손 건수" in trend.columns else trend.columns[-1]
        lines.append("\n## 월별 파손 추이 (최근 12개월)")
        for _, row in trend.tail(12).iterrows():
            lines.append(f"- {row[period_col]}: {row[count_col]}건")

    if "특이사항" in detail_df.columns:
        notes = detail_df["특이사항"].dropna().astype(str).str.strip()
        notes = notes[notes != ""].drop_duplicates().head(12)
        if not notes.empty:
            lines.append("\n## 특이사항 샘플")
            for note in notes:
                lines.append(f"- {note[:160]}")

    return "\n".join(lines)


def _context_fingerprint(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]


def interpret_breakage(context: str, user_question: str = "") -> str:
    """Gemini로 해석 텍스트 생성. 실패 시 예외."""
    api_key = _gemini_secret("api_key")
    if not api_key:
        raise RuntimeError(
            "Secrets에 [gemini] api_key 가 없습니다. "
            "https://aistudio.google.com/apikey 에서 무료 키를 발급하세요."
        )
    if not (api_key.startswith("AIza") or api_key.startswith("AQ.")):
        raise RuntimeError(
            "API 키 형식이 올바르지 않습니다. "
            "AI Studio의 **키 복사**로 받은 값 그대로 넣으세요. "
            "(AIzaSy... 또는 AQ.... — 앞에 AIza_ 를 붙이지 마세요.)"
        )

    model_name = _gemini_secret("model", _DEFAULT_MODEL) or _DEFAULT_MODEL

    focus = user_question.strip() or "전체 현황을 균형 있게 해석해 주세요."
    prompt = f"""당신은 PCB/드릴 공정 품질 엔지니어입니다.
아래는 드릴 파손 이벤트 로그를 집계한 결과입니다. 숫자 근거에 맞춰 한국어로 해석하세요.

작성 형식:
1) 한줄 요약
2) 주요 발견 (불릿 3~6개)
3) 우선 점검 설비/제품/파손형태
4) 현장 조치 제안 (구체적, 실현 가능)
5) 데이터 한계·주의점

규칙:
- 과장·추측은 최소화하고, 데이터에 없는 원인은 '추정'으로 표시
- 전문 용어는 현장 작업자가 이해하도록 짧게
- 과한 서론 없이 바로 본문

사용자 관심사: {focus}

=== 집계 데이터 ===
{context}
"""

    models_to_try = [model_name]
    for m in _VALID_MODEL_FALLBACKS:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error: Exception | None = None

    # 1) 신규 SDK (AQ. 키에 더 잘 맞을 수 있음)
    try:
        from google import genai as google_genai

        client = google_genai.Client(api_key=api_key)
        for m in models_to_try:
            try:
                response = client.models.generate_content(model=m, contents=prompt)
                text = getattr(response, "text", None)
                if text:
                    return text.strip()
            except Exception as e:
                last_error = e
                continue
    except ImportError:
        pass

    # 2) 기존 SDK
    try:
        import google.generativeai as genai
    except ImportError as e:
        raise RuntimeError(
            "google-generativeai 또는 google-genai 패키지가 필요합니다. "
            "pip install google-generativeai google-genai"
        ) from e

    genai.configure(api_key=api_key)
    for m in models_to_try:
        try:
            model = genai.GenerativeModel(m)
            response = model.generate_content(prompt)
            text = getattr(response, "text", None)
            if text:
                return text.strip()
        except Exception as e:
            last_error = e
            continue

    if last_error is not None:
        msg = str(last_error)
        if "API_KEY_INVALID" in msg or "API key not valid" in msg:
            raise RuntimeError(
                "API 키가 유효하지 않습니다. AI Studio에서 **키 복사**한 값을 "
                "그대로 secrets.toml 의 api_key 에 넣고, 앱을 재시작하세요. "
                "(AIza_ 접두어를 붙이지 마세요. 모델은 gemini-2.0-flash 권장.)"
            ) from last_error
        raise RuntimeError(f"Gemini 호출 실패: {last_error}") from last_error
    raise RuntimeError("Gemini가 빈 응답을 반환했습니다. 모델명·할당량을 확인하세요.")


def render_gemini_insight(
    detail_df: pd.DataFrame,
    *,
    map_metrics: dict[str, Any] | None = None,
    key_prefix: str = "gemini",
) -> None:
    """맵/통계/로그 탭 공용 Gemini 해석 패널."""
    st.subheader("Gemini 결과 해석")
    st.caption("현재 조회 기간·파손 형태 필터가 적용된 집계를 바탕으로 해석합니다. (Gemini 무료 API)")

    if not gemini_configured():
        st.info(
            "Gemini API 키가 없습니다. Streamlit Secrets에 아래를 추가하세요.\n\n"
            "```toml\n"
            "[gemini]\n"
            'api_key = "AIza..."\n'
            'model = "gemini-2.0-flash"\n'
            "```\n\n"
            "키 발급: https://aistudio.google.com/apikey"
        )
        return

    if detail_df is None or detail_df.empty:
        st.caption("해석할 파손 데이터가 없습니다.")
        return

    q_key = f"{key_prefix}_question"
    btn_key = f"{key_prefix}_run"
    clear_key = f"{key_prefix}_clear"

    user_q = st.text_input(
        "추가 질문 (선택)",
        placeholder="예: 최근 파손이 많은 설비의 공통점은?",
        key=q_key,
    )
    c1, c2 = st.columns([1, 1])
    with c1:
        run = st.button("해석 생성", type="primary", width="stretch", key=btn_key)
    with c2:
        clear = st.button("결과 지우기", width="stretch", key=clear_key)

    if clear:
        st.session_state.pop("gemini_last_text", None)
        st.session_state.pop("gemini_last_fp", None)
        st.rerun()

    context = build_analysis_context(detail_df, map_metrics)
    fp = _context_fingerprint(context + "\n" + (user_q or ""))

    if run:
        with st.spinner("Gemini가 결과를 해석하는 중..."):
            try:
                text = interpret_breakage(context, user_q)
                st.session_state["gemini_last_text"] = text
                st.session_state["gemini_last_fp"] = fp
            except Exception as e:
                st.error(str(e))
                return

    text = st.session_state.get("gemini_last_text")
    if text:
        if st.session_state.get("gemini_last_fp") != fp:
            st.caption("필터 또는 질문이 바뀌었습니다. 다시 **해석 생성**을 눌러 주세요.")
        st.markdown(text)
        with st.expander("Gemini에 전달된 집계 요약 보기"):
            st.code(context, language="markdown")
