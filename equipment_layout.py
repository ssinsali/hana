"""대시보드 이미지와 동일한 드릴 설비 그리드 레이아웃."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EquipmentStatus(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    BROKEN = "broken"
    OFFLINE = "offline"


STATUS_COLORS = {
    EquipmentStatus.NORMAL: "#22c55e",
    EquipmentStatus.WARNING: "#eab308",
    EquipmentStatus.BROKEN: "#ef4444",
    EquipmentStatus.OFFLINE: "#6b7280",
}

STATUS_LABELS = {
    EquipmentStatus.NORMAL: "미파손",
    EquipmentStatus.WARNING: "경고",
    EquipmentStatus.BROKEN: "파손",
    EquipmentStatus.OFFLINE: "비가동",
}

# 설비 맵 하단 범례 (비가동 제외)
MAP_LEGEND_ITEMS: list[tuple[EquipmentStatus, str]] = [
    (EquipmentStatus.NORMAL, "미파손"),
    (EquipmentStatus.WARNING, "파손 (2회 이하)"),
    (EquipmentStatus.BROKEN, "파손 (3회 이상)"),
]


def effective_equipment_status(count: int, stored_status: str) -> EquipmentStatus:
    """
    맵 포인트 색상: 파손 횟수 기준.
    - 0회: 미파손(녹색) / 1~2회: 경고(노랑) / 3회 이상: 파손(빨강)
    """
    _ = stored_status
    if count >= 3:
        return EquipmentStatus.BROKEN
    if count >= 1:
        return EquipmentStatus.WARNING
    return EquipmentStatus.NORMAL


@dataclass(frozen=True)
class GridCell:
    equipment_id: str
    row: int
    col: int


GRID_COLS = 20
STANDARD_COLS = 16
STANDARD_COL_W = 38  # 1~9행: 넓은 간격
DENSE_COLS = 20


def grid_width_standard() -> int:
    return STANDARD_COLS * STANDARD_COL_W


def grid_width_dense() -> int:
    """하단 20칸도 상단 16칸과 동일한 전체 너비 (우측 돌출 방지)."""
    return grid_width_standard()


def dense_col_w() -> float:
    return grid_width_dense() / DENSE_COLS


def _ids_ltr(start: int, end: int) -> list[str]:
    return [f"DRA{n:02d}" for n in range(start, end + 1)]


def _ids_rtl(start: int, end: int) -> list[str]:
    return [f"DRA{n:02d}" for n in range(end, start - 1, -1)]


def _pad_left(items: list[str | None], width: int = STANDARD_COLS) -> list[str | None]:
    row: list[str | None] = [None] * width
    for i, eq in enumerate(items):
        if i < width:
            row[i] = eq
    return row


def _place_at(cols: dict[int, str], width: int = GRID_COLS) -> list[str | None]:
    row: list[str | None] = [None] * width
    for col, eq in cols.items():
        row[col] = eq
    return row


def _row_groups_of_three_exact(ids: list[str]) -> list[str | None]:
    """16열: 3개 + 빈칸 패턴 (3·6행)."""
    row16: list[str | None] = [None] * 16
    src = 0
    col = 0
    while src < len(ids):
        for _ in range(3):
            if src >= len(ids) or col >= 16:
                break
            row16[col] = ids[src]
            src += 1
            col += 1
        if src < len(ids) and col < 16:
            col += 1
    return _pad_left(row16)


def _row10_dra135_154() -> list[str | None]:
    """DRA135~154: 한 줄 20칸 (원본 대시보드)."""
    return list(_ids_ltr(135, 154))


def _row11_dra155_158() -> list[str | None]:
    """DRA158~155: DRA151~154(col 16~19) 바로 아래 4칸만."""
    return _place_at(
        {
            16: "DRA158",
            17: "DRA157",
            18: "DRA156",
            19: "DRA155",
        }
    )


# 원본 대시보드 11행 × 20열 (None = 빈 칸)
DRA_GRID: list[list[str | None]] = [
    _pad_left(_ids_rtl(1, 14)),                          # 1: DRA14~01
    _pad_left(_ids_ltr(15, 30)),                         # 2: DRA15~30
    _row_groups_of_three_exact(_ids_rtl(31, 42)),        # 3: DRA42~31
    _pad_left(_ids_ltr(43, 58)),                         # 4: DRA43~58
    _pad_left(_ids_rtl(59, 74)),                         # 5: DRA74~59
    _row_groups_of_three_exact(_ids_ltr(75, 86)),        # 6: DRA75~86
    _pad_left(_ids_rtl(87, 102)),                        # 7: DRA102~87
    _pad_left(_ids_ltr(103, 118)),                       # 8: DRA103~118
    _pad_left(_ids_rtl(119, 134)),                       # 9: DRA134~119
    _row10_dra135_154(),                                 # 10: DRA135~154 (한 줄)
    _row11_dra155_158(),                                 # 11: DRA158~155 (우하단)
]

# 행별 간격: standard(1~9행) / dense(135~158)
DRA_ROW_SPACING: list[str] = ["standard"] * 9 + ["dense"] * 2

# 회색 구분선 — 원본 대시보드: 1·3·5·7·9·11행 아래 (2행 DRA30·4행 아래 없음)
DRA_DIVIDER_AFTER: list[bool] = [
    True,   # 1행(DRA14~01) 아래
    False,  # 2행(DRA15~30) 아래 — DRA30 아래 선 없음
    True,   # 3행(DRA42~31) 아래 — DRA31 아래
    False,
    True,   # 5행(DRA74~59) 아래
    False,
    True,   # 7행(DRA102~87) 아래
    False,
    True,   # 9행(DRA134~119) 아래
    False,  # 10행(DRA135~154) 아래 — DRA155 위 선 없음
    True,   # 11행(DRA158~155) 아래
]

# 행 아래 추가 세로 간격 (DRA118·DRA119 사이 등)
DRA_EXTRA_GAP_AFTER: list[bool] = [
    False,
    False,
    False,
    False,
    False,
    False,
    False,
    True,   # 8행(DRA103~118) 아래 — DRA118·DRA119 한 칸 간격
    False,
    False,
    False,
]

DRA_ROWS: list[list[str]] = [
    [c for c in row if c is not None] for row in DRA_GRID
]

FPAS3_IDS: list[str] = [f"FPAS3-{n:02d}" for n in range(1, 6)]

# 사이드바: DRA 행 인덱스(0~10)에 맞춰 배치
# valign: start | center | end — 해당 DRA 행 경계선에 세로 정렬
FPAS3_SIDEBAR_SLOTS: dict[int, tuple[int, list[dict[str, str]], str]] = {
    0: (1, [{"type": "equipment", "id": "FPAS3-01"}], "end"),       # DRA30 위
    2: (1, [{"type": "equipment", "id": "FPAS3-02"}], "end"),       # DRA58 위
    4: (1, [{"type": "equipment", "id": "FPAS3-03"}], "end"),       # DRA59 아래
    6: (1, [{"type": "equipment", "id": "FPAS3-04"}], "end"),       # DRA87 아래
    7: (1, [{"type": "label", "text": "FP CH4 AUTO"}], "end"),
    8: (2, [{"type": "equipment", "id": "FPAS3-05"}], "center"),
    10: (1, [{"type": "label", "text": "FP CH1 AUTO"}], "center"),
}


def sidebar_occupies_row(row_idx: int) -> int | None:
    """row_idx가 속한 사이드바 슬롯 시작 행. 없으면 None."""
    for start, (span, _, _) in FPAS3_SIDEBAR_SLOTS.items():
        if start <= row_idx < start + span:
            return start
    return None

ALL_EQUIPMENT_IDS: list[str] = [eq for row in DRA_ROWS for eq in row] + FPAS3_IDS


def build_grid_cells() -> list[GridCell]:
    cells: list[GridCell] = []
    for row_idx, row in enumerate(DRA_GRID):
        for col_idx, eq_id in enumerate(row):
            if eq_id is not None:
                cells.append(GridCell(equipment_id=eq_id, row=row_idx, col=col_idx))
    return cells


def max_columns() -> int:
    return GRID_COLS
