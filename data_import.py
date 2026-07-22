"""CSV·엑셀 등 외부 데이터 파싱."""
from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import pandas as pd

from equipment_layout import ALL_EQUIPMENT_IDS, EquipmentStatus, STATUS_LABELS

_ID_SET = set(ALL_EQUIPMENT_IDS)

_EQUIPMENT_COLS = ("설비", "설비id", "설비_id", "equipment", "equipment_id", "id", "드릴", "장비")
_COUNT_COLS = ("파손횟수", "파손 횟수", "파손", "파손수", "count", "cnt", "수량", "qty", "quantity")
_STATUS_COLS = ("상태", "status", "state")

_PRODUCT_COLS = ("제품코드", "제품 코드", "product", "product_code", "품번")
_WORK_END_COLS = ("작업종료", "작업 종료", "work_end", "종료일시", "작업완료", "완료일시")
_REGISTRAR_COLS = ("등록자", "registrar", "작성자", "입력자")
_TOOL_DESC_COLS = ("툴설명", "툴 설명", "tool_description", "tool", "툴")
_DRILL_LOT_COLS = ("드릴 랏", "드릴랏", "드릴lot", "drill_lot", "드릴 lot", "랏")
_BROKEN_HOLE_COLS = ("브로큰 홀번호", "브로큰홀번호", "broken_hole", "broken_hole_number", "홀번호")
_DRILL_USAGE_COLS = ("드릴사용량", "드릴 사용량", "drill_usage", "사용량")
_BREAKAGE_TYPE_COLS = ("파손 형태", "파손형태", "breakage_type", "breakage_form", "파손유형")
_REMARKS_COLS = ("특이사항", "비고", "remarks", "note", "메모")

EVENT_LOG_CSV_COLUMNS = (
    "제품코드",
    "설비",
    "작업종료",
    "등록자",
    "툴설명",
    "드릴 랏",
    "브로큰 홀번호",
    "드릴사용량",
    "파손 형태",
    "특이사항",
)
EVENT_LOG_CSV_FILENAME = "event_log.csv"


def _cell_str(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    return str(raw).strip()


def _cell_float(raw) -> float | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _cell_datetime(raw) -> str:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ""
    try:
        ts = pd.to_datetime(raw, errors="coerce")
        if pd.isna(ts):
            return _cell_str(raw)
        return ts.isoformat(timespec="seconds")
    except Exception:
        return _cell_str(raw)


def is_event_log_format(df: pd.DataFrame) -> bool:
    """이벤트 로그 형식 여부 (제품코드·작업종료·등록자 등)."""
    has_eq = _pick_column(df, _EQUIPMENT_COLS) is not None
    markers = (
        _PRODUCT_COLS,
        _WORK_END_COLS,
        _REGISTRAR_COLS,
        _TOOL_DESC_COLS,
        _DRILL_LOT_COLS,
        _BROKEN_HOLE_COLS,
        _DRILL_USAGE_COLS,
        _BREAKAGE_TYPE_COLS,
        _REMARKS_COLS,
    )
    extra = sum(1 for cols in markers if _pick_column(df, cols) is not None)
    return has_eq and extra >= 2


def normalize_equipment_id(raw: str) -> str | None:
    """DRA3 → DRA03 등으로 정규화. 알 수 없는 ID는 None."""
    s = str(raw).strip().upper().replace(" ", "")
    if not s:
        return None

    m = re.match(r"^DRA(\d+)$", s)
    if m:
        candidate = f"DRA{int(m.group(1)):02d}"
        return candidate if candidate in _ID_SET else None

    m = re.match(r"^FPAS3-?(\d+)$", s)
    if m:
        candidate = f"FPAS3-{int(m.group(1)):02d}"
        return candidate if candidate in _ID_SET else None

    return s if s in _ID_SET else None


def _pick_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    lower_map = {c.lower().replace(" ", ""): c for c in df.columns}
    for name in candidates:
        key = name.lower().replace(" ", "")
        if key in lower_map:
            return lower_map[key]
    return None


def _pick_breakage_type_column(df: pd.DataFrame) -> str | None:
    col = _pick_column(df, _BREAKAGE_TYPE_COLS)
    if col is not None:
        return col
    if len(df.columns) >= 9:
        return df.columns[8]
    return None


def _parse_status(raw) -> EquipmentStatus | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    s = str(raw).strip().lower()
    if s in ("미파손", "정상", "normal", "green", "g", "ok"):
        return EquipmentStatus.NORMAL
    if s in ("경고", "warning", "yellow", "y"):
        return EquipmentStatus.WARNING
    if s in ("파손", "broken", "red", "r", "break"):
        return EquipmentStatus.BROKEN
    if s in ("비가동", "offline", "gray", "grey", "off"):
        return EquipmentStatus.OFFLINE
    return None


def read_table_file(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """CSV·엑셀 파일을 DataFrame으로 읽기."""
    name = filename.lower()
    bio = BytesIO(file_bytes)
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(bio)
    return pd.read_csv(bio)


def parse_count_table(df: pd.DataFrame) -> tuple[dict[str, int], dict[str, EquipmentStatus], list[str]]:
    """
    업로드 테이블 → (파손횟수 dict, 상태 dict, 오류 메시지 목록).
    컬럼 2개만 있고 헤더가 없으면 (설비, 파손횟수)로 간주.
    """
    errors: list[str] = []
    work = df.copy()

    if work.shape[1] >= 2 and _pick_column(work, _EQUIPMENT_COLS) is None:
        first = work.iloc[:, 0]
        if first.astype(str).str.match(r"^(DRA|FPAS)", case=False, na=False).any():
            work = work.rename(columns={work.columns[0]: "설비", work.columns[1]: "파손 횟수"})

    eq_col = _pick_column(work, _EQUIPMENT_COLS)
    cnt_col = _pick_column(work, _COUNT_COLS)
    st_col = _pick_column(work, _STATUS_COLS)

    if eq_col is None:
        errors.append("설비 ID 컬럼을 찾을 수 없습니다. (예: 설비, equipment_id)")
        return {}, {}, errors
    if cnt_col is None and st_col is None:
        errors.append("파손 횟수 또는 상태 컬럼이 필요합니다.")
        return {}, {}, errors

    counts: dict[str, int] = {}
    statuses: dict[str, EquipmentStatus] = {}

    for idx, row in work.iterrows():
        eq_id = normalize_equipment_id(row[eq_col])
        if eq_id is None:
            raw = row[eq_col]
            if pd.notna(raw) and str(raw).strip():
                errors.append(f"행 {idx + 2}: 알 수 없는 설비 ID '{raw}'")
            continue

        if cnt_col is not None:
            val = row[cnt_col]
            if pd.notna(val):
                try:
                    n = int(float(val))
                    if n < 0:
                        errors.append(f"{eq_id}: 파손 횟수는 0 이상이어야 합니다.")
                    else:
                        counts[eq_id] = n
                except (TypeError, ValueError):
                    errors.append(f"{eq_id}: 파손 횟수 '{val}' 를 숫자로 변환할 수 없습니다.")

        if st_col is not None:
            st_val = _parse_status(row[st_col])
            if st_val is not None:
                statuses[eq_id] = st_val
            elif pd.notna(row[st_col]) and str(row[st_col]).strip():
                errors.append(f"{eq_id}: 상태 '{row[st_col]}' 를 인식할 수 없습니다.")

    return counts, statuses, errors


def parse_event_log_table(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """
    파손 이벤트 로그 1행 = 파손 1건.
    반환: (레코드 dict 목록, 오류 메시지)
    """
    errors: list[str] = []
    work = df.copy()

    eq_col = _pick_column(work, _EQUIPMENT_COLS)
    if eq_col is None:
        errors.append("설비 컬럼을 찾을 수 없습니다.")
        return [], errors

    col_map = {
        "product_code": _pick_column(work, _PRODUCT_COLS),
        "work_end": _pick_column(work, _WORK_END_COLS),
        "registrar": _pick_column(work, _REGISTRAR_COLS),
        "tool_description": _pick_column(work, _TOOL_DESC_COLS),
        "drill_lot": _pick_column(work, _DRILL_LOT_COLS),
        "broken_hole_number": _pick_column(work, _BROKEN_HOLE_COLS),
        "drill_usage": _pick_column(work, _DRILL_USAGE_COLS),
        "breakage_type": _pick_breakage_type_column(work),
        "remarks": _pick_column(work, _REMARKS_COLS),
    }

    records: list[dict] = []
    for idx, row in work.iterrows():
        eq_id = normalize_equipment_id(row[eq_col])
        if eq_id is None:
            raw = row[eq_col]
            if pd.notna(raw) and str(raw).strip():
                errors.append(f"행 {idx + 2}: 알 수 없는 설비 ID '{raw}'")
            continue

        rec = {
            "product_code": _cell_str(row[col_map["product_code"]]) if col_map["product_code"] else "",
            "equipment_id": eq_id,
            "work_end": _cell_datetime(row[col_map["work_end"]]) if col_map["work_end"] else "",
            "registrar": _cell_str(row[col_map["registrar"]]) if col_map["registrar"] else "",
            "tool_description": _cell_str(row[col_map["tool_description"]]) if col_map["tool_description"] else "",
            "drill_lot": _cell_str(row[col_map["drill_lot"]]) if col_map["drill_lot"] else "",
            "broken_hole_number": _cell_str(row[col_map["broken_hole_number"]]) if col_map["broken_hole_number"] else "",
            "drill_usage": _cell_float(row[col_map["drill_usage"]]) if col_map["drill_usage"] else None,
            "breakage_type": _cell_str(row[col_map["breakage_type"]]) if col_map["breakage_type"] else "",
            "remarks": _cell_str(row[col_map["remarks"]]) if col_map["remarks"] else "",
        }
        records.append(rec)

    if not records and not errors:
        errors.append("유효한 이벤트 로그 행이 없습니다.")
    return records, errors


def default_event_log_csv_path(base_dir: Path) -> Path:
    return base_dir / "data" / EVENT_LOG_CSV_FILENAME


def records_to_event_csv_dataframe(records: list[dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for rec in records:
        rows.append(
            {
                "제품코드": rec.get("product_code", "") or "",
                "설비": rec.get("equipment_id", "") or "",
                "작업종료": rec.get("work_end", "") or "",
                "등록자": rec.get("registrar", "") or "",
                "툴설명": rec.get("tool_description", "") or "",
                "드릴 랏": rec.get("drill_lot", "") or "",
                "브로큰 홀번호": rec.get("broken_hole_number", "") or "",
                "드릴사용량": rec.get("drill_usage", ""),
                "파손 형태": rec.get("breakage_type", "") or "",
                "특이사항": rec.get("remarks", "") or "",
            }
        )
    return pd.DataFrame(rows, columns=list(EVENT_LOG_CSV_COLUMNS))


def load_event_log_from_csv(csv_path: Path) -> tuple[list[dict], list[str]]:
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    return parse_event_log_table(df)


def write_event_log_csv(records: list[dict], csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    records_to_event_csv_dataframe(records).to_csv(csv_path, index=False, encoding="utf-8-sig")


def event_log_template_dataframe() -> pd.DataFrame:
    """파손 이벤트 로그 CSV 템플릿 (예시 2행)."""
    return pd.DataFrame(
        [
            {
                "제품코드": "PROD-001",
                "설비": "DRA13",
                "작업종료": "2026-06-29 08:15:00",
                "등록자": "홍길동",
                "툴설명": "0.8mm 드릴",
                "드릴 랏": "LOT-20260601",
                "브로큰 홀번호": "H-12",
                "드릴사용량": 1250,
                "파손 형태": "드릴 파손",
                "특이사항": "",
            },
            {
                "제품코드": "PROD-002",
                "설비": "DRA58",
                "작업종료": "2026-06-29 14:30:00",
                "등록자": "김철수",
                "툴설명": "0.8mm 드릴",
                "드릴 랏": "LOT-20260602",
                "브로큰 홀번호": "H-05",
                "드릴사용량": 980,
                "파손 형태": "홀 파손",
                "특이사항": "이물 확인",
            },
        ]
    )


def template_dataframe() -> pd.DataFrame:
    """전체 설비 빈 템플릿."""
    return pd.DataFrame(
        {
            "설비": ALL_EQUIPMENT_IDS,
            "파손 횟수": [0] * len(ALL_EQUIPMENT_IDS),
            "상태": [STATUS_LABELS[EquipmentStatus.NORMAL]] * len(ALL_EQUIPMENT_IDS),
        }
    )
