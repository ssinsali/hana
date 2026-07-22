"""대시보드 스크린샷에서 설비 색상 포인트를 추출."""
from __future__ import annotations

from io import BytesIO

import numpy as np
from PIL import Image

from equipment_layout import (
    DENSE_COLS,
    DRA_GRID,
    DRA_ROW_SPACING,
    STANDARD_COLS,
    EquipmentStatus,
    dense_col_w,
    grid_width_standard,
)


def _rgb_to_status(r: int, g: int, b: int) -> EquipmentStatus:
    """픽셀 RGB를 설비 상태로 분류."""
    mx = max(r, g, b)
    mn = min(r, g, b)
    if mx < 70:
        return EquipmentStatus.OFFLINE

    if r > 140 and g < 90 and b < 90:
        return EquipmentStatus.BROKEN
    if g > 120 and r < 140 and g >= b:
        return EquipmentStatus.NORMAL
    if r > 140 and g > 120 and b < 100:
        return EquipmentStatus.WARNING
    if abs(r - g) < 35 and abs(g - b) < 35 and mx < 160:
        return EquipmentStatus.OFFLINE

    # 채도 기반 보조 판별
    if mx == 0:
        return EquipmentStatus.OFFLINE
    saturation = (mx - mn) / mx
    if saturation < 0.15 and mx < 140:
        return EquipmentStatus.OFFLINE
    if r > g and r > b:
        return EquipmentStatus.BROKEN
    if g > r and g > b:
        return EquipmentStatus.NORMAL
    if r > 100 and g > 100:
        return EquipmentStatus.WARNING
    return EquipmentStatus.OFFLINE


def _sample_region(img: np.ndarray, x: int, y: int, radius: int = 4) -> tuple[int, int, int]:
    h, w = img.shape[:2]
    x0 = max(0, x - radius)
    x1 = min(w, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(h, y + radius + 1)
    patch = img[y0:y1, x0:x1]
    if patch.size == 0:
        return 128, 128, 128
    return tuple(int(v) for v in patch.reshape(-1, 3).mean(axis=0))


def analyze_dashboard_image(
    image_bytes: bytes,
    *,
    grid_top: float = 0.04,
    grid_bottom: float = 0.97,
    grid_left: float = 0.01,
    grid_right: float = 0.88,
) -> dict[str, EquipmentStatus]:
    """
    대시보드 스크린샷에서 DRA 설비 상태를 추출.

    grid_* 파라미터는 이미지 내 DRA 그리드 영역 비율(0~1).
    동일 해상도 스크린샷에 맞춰 조정 가능.
    """
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    arr = np.array(img)
    h, w = arr.shape[:2]

    x0 = int(w * grid_left)
    x1 = int(w * grid_right)
    y0 = int(h * grid_top)
    y1 = int(h * grid_bottom)

    region_h = y1 - y0
    region_w = x1 - x0
    row_count = len(DRA_GRID)

    max_row_w = grid_width_standard()

    result: dict[str, EquipmentStatus] = {}
    for row_idx, row in enumerate(DRA_GRID):
        spacing = DRA_ROW_SPACING[row_idx] if row_idx < len(DRA_ROW_SPACING) else "standard"
        col_w_px = dense_col_w() if spacing == "dense" else max_row_w / STANDARD_COLS

        cell_h = region_h / row_count
        row_y = int(y0 + row_idx * cell_h + cell_h * 0.22)

        for col_idx, eq_id in enumerate(row):
            if eq_id is None:
                continue
            if spacing == "standard" and col_idx >= STANDARD_COLS:
                continue
            cell_x = int(
                x0 + (col_idx * col_w_px + col_w_px * 0.5) / max_row_w * region_w
            )
            rgb = _sample_region(arr, cell_x, row_y)
            result[eq_id] = _rgb_to_status(*rgb)

    return result
