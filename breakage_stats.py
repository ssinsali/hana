"""파손 이벤트 로그 기반 통계."""
from __future__ import annotations

import calendar
import os
from datetime import date
from typing import Any

import pandas as pd

DETAIL_COLUMNS = [
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
]


def records_to_dataframe(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=DETAIL_COLUMNS)
    df = pd.DataFrame(records)
    rename = {
        "product_code": "제품코드",
        "equipment_id": "설비",
        "work_end": "작업종료",
        "registrar": "등록자",
        "tool_description": "툴설명",
        "drill_lot": "드릴 랏",
        "broken_hole_number": "브로큰 홀번호",
        "drill_usage": "드릴사용량",
        "breakage_type": "파손 형태",
        "remarks": "특이사항",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    for col in DETAIL_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if "작업종료" in df.columns:
        df["작업종료"] = pd.to_datetime(df["작업종료"], errors="coerce")
    if "드릴사용량" in df.columns:
        df["드릴사용량"] = pd.to_numeric(df["드릴사용량"], errors="coerce")
    extra = [c for c in df.columns if c not in DETAIL_COLUMNS]
    return df[DETAIL_COLUMNS + extra]


def normalize_breakage_type(raw: Any) -> str:
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return "(미입력)"
    return s


def breakage_type_options(df: pd.DataFrame) -> list[str]:
    if df.empty or "파손 형태" not in df.columns:
        return []
    types = df["파손 형태"].map(normalize_breakage_type)
    return sorted(types.unique(), key=lambda x: (x == "(미입력)", x))


def filter_by_breakage_type(
    df: pd.DataFrame,
    mode: str,
    selected_types: list[str],
) -> pd.DataFrame:
    """mode: 전체 포함 | 선택 제외 | 선택만"""
    if df.empty or "파손 형태" not in df.columns or mode == "전체 포함":
        return df
    if not selected_types:
        if mode == "선택만":
            return df.iloc[0:0].copy()
        return df
    normalized = df["파손 형태"].map(normalize_breakage_type)
    selected = set(selected_types)
    if mode == "선택 제외":
        return df[~normalized.isin(selected)].copy()
    if mode == "선택만":
        return df[normalized.isin(selected)].copy()
    return df


def normalize_drill_lot(raw: Any) -> str:
    s = str(raw).strip().upper().replace(" ", "")
    if not s or s.lower() == "nan":
        return ""
    return s


def build_drill_lot_group_map(lots: list[str], *, min_common_len: int = 6) -> dict[str, str]:
    """
    드릴 랏 문자열에서 공통 글자·숫자(접두사)가 같은 항목을 하나로 묶는다.
    예: FJEWP011-024, FJEWP011-029 → FJEWP011
    """
    normalized = [normalize_drill_lot(x) for x in lots]
    unique = sorted({x for x in normalized if x})
    if not unique:
        return {}

    mapping: dict[str, str] = {}
    cluster = [unique[0]]

    def _flush(cluster_members: list[str]) -> None:
        if len(cluster_members) == 1:
            mapping[cluster_members[0]] = cluster_members[0]
            return
        key = os.path.commonprefix(cluster_members)
        key = key.rstrip("-_")
        if len(key) < min_common_len:
            for m in cluster_members:
                mapping[m] = m
            return
        for m in cluster_members:
            mapping[m] = key

    for lot in unique[1:]:
        lcp = os.path.commonprefix([cluster[-1], lot]).rstrip("-_")
        if len(lcp) >= min_common_len:
            cluster.append(lot)
        else:
            _flush(cluster)
            cluster = [lot]
    _flush(cluster)
    return mapping


def apply_drill_lot_groups(df: pd.DataFrame) -> pd.DataFrame:
    """이벤트 로그에 '드릴 랏(통합)' 컬럼 추가."""
    if df.empty or "드릴 랏" not in df.columns:
        return df
    out = df.copy()
    raw = out["드릴 랏"].tolist()
    group_map = build_drill_lot_group_map(raw)
    out["드릴 랏(통합)"] = [
        group_map.get(normalize_drill_lot(v), "(미입력)") if normalize_drill_lot(v) else "(미입력)"
        for v in out["드릴 랏"]
    ]
    return out


def count_drill_lot_grouped(df: pd.DataFrame, top_n: int | None = 20) -> pd.DataFrame:
    """공통 접두사로 합산한 드릴 랏별 파손 건수."""
    if df.empty or "드릴 랏" not in df.columns:
        return pd.DataFrame(columns=["드릴 랏(통합)", "파손 건수", "원본 랏 수"])
    grouped_df = apply_drill_lot_groups(df)
    agg = (
        grouped_df.groupby("드릴 랏(통합)", as_index=False)
        .agg(파손_건수=("드릴 랏(통합)", "size"), 원본_랏_수=("드릴 랏", "nunique"))
        .sort_values(["파손_건수", "드릴 랏(통합)"], ascending=[False, True])
    )
    if top_n is not None:
        agg = agg.head(top_n)
    agg.columns = ["드릴 랏(통합)", "파손 건수", "원본 랏 수"]
    return agg


def drill_lot_group_detail(df: pd.DataFrame, top_n: int | None = 20) -> pd.DataFrame:
    """통합 랏 → 포함된 원본 랏 목록."""
    if df.empty or "드릴 랏" not in df.columns:
        return pd.DataFrame(columns=["드릴 랏(통합)", "원본 드릴 랏", "파손 건수"])
    grouped_df = apply_drill_lot_groups(df)
    detail = (
        grouped_df.groupby(["드릴 랏(통합)", "드릴 랏"], as_index=False)
        .size()
        .rename(columns={"size": "파손 건수"})
        .sort_values(["드릴 랏(통합)", "파손 건수"], ascending=[True, False])
    )
    top_groups = count_drill_lot_grouped(df, top_n)["드릴 랏(통합)"].tolist()
    if not top_groups:
        return detail.iloc[0:0]
    return detail[detail["드릴 랏(통합)"].isin(top_groups)]


def equipment_counts(df: pd.DataFrame) -> dict[str, int]:
    from equipment_layout import ALL_EQUIPMENT_IDS

    counts = {eq_id: 0 for eq_id in ALL_EQUIPMENT_IDS}
    if df.empty or "설비" not in df.columns:
        return counts
    for eq_id, n in df["설비"].value_counts().items():
        if eq_id in counts:
            counts[eq_id] = int(n)
    return counts


def equipment_counts_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    from equipment_layout import ALL_EQUIPMENT_IDS

    counts = equipment_counts(df)
    return pd.DataFrame(
        [{"설비": eq_id, "파손 횟수": counts[eq_id]} for eq_id in ALL_EQUIPMENT_IDS]
    ).sort_values(["파손 횟수", "설비"], ascending=[False, True])


def summary_metrics(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "total_events": 0,
            "equipment_count": 0,
            "product_count": 0,
            "registrar_count": 0,
            "drill_lot_count": 0,
            "avg_drill_usage": None,
            "date_min": None,
            "date_max": None,
        }
    valid_dates = df["작업종료"].dropna() if "작업종료" in df.columns else pd.Series(dtype="datetime64[ns]")
    usage = df["드릴사용량"].dropna() if "드릴사용량" in df.columns else pd.Series(dtype=float)
    return {
        "total_events": len(df),
        "equipment_count": df["설비"].nunique() if "설비" in df.columns else 0,
        "product_count": _nonempty_nunique(df, "제품코드"),
        "registrar_count": _nonempty_nunique(df, "등록자"),
        "drill_lot_count": _grouped_drill_lot_nunique(df),
        "avg_drill_usage": float(usage.mean()) if len(usage) else None,
        "date_min": valid_dates.min() if len(valid_dates) else None,
        "date_max": valid_dates.max() if len(valid_dates) else None,
    }


def _nonempty_nunique(df: pd.DataFrame, col: str) -> int:
    if col not in df.columns:
        return 0
    s = df[col].astype(str).str.strip()
    return s[s.ne("") & s.ne("nan")].nunique()


def _grouped_drill_lot_nunique(df: pd.DataFrame) -> int:
    if df.empty or "드릴 랏" not in df.columns:
        return 0
    grouped = apply_drill_lot_groups(df)
    s = grouped["드릴 랏(통합)"].astype(str)
    return s[s.ne("(미입력)")].nunique()


def count_by_column(df: pd.DataFrame, col: str, top_n: int | None = 20) -> pd.DataFrame:
    if df.empty or col not in df.columns:
        return pd.DataFrame(columns=[col, "파손 건수"])
    s = df[col].astype(str).str.strip().replace({"": "(미입력)", "nan": "(미입력)"})
    counts = s.value_counts()
    if top_n is not None:
        counts = counts.head(top_n)
    return counts.rename_axis(col).reset_index(name="파손 건수")


_WEEKDAY_KO = ("월", "화", "수", "목", "금", "토", "일")


def format_date_korean(value: date | Any) -> str:
    """날짜 → 한글 표기 (예: 2026년 6월 29일 (월))."""
    if isinstance(value, date):
        d = value
    else:
        d = pd.Timestamp(value).date()
    wd = _WEEKDAY_KO[d.weekday()]
    return f"{d.year}년 {d.month}월 {d.day}일 ({wd})"


def filter_by_work_end_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """작업종료 기준으로 기간 내 이벤트만 반환."""
    if df.empty or "작업종료" not in df.columns:
        return df.iloc[0:0]
    valid = df.dropna(subset=["작업종료"])
    if valid.empty:
        return valid
    days = valid["작업종료"].dt.date
    return valid[(days >= start) & (days <= end)].copy()


def format_year_month(year: int, month: int) -> str:
    return f"{year}년 {month}월"


def work_end_month_bounds(df: pd.DataFrame) -> tuple[tuple[int, int], tuple[int, int]] | None:
    bounds = work_end_date_bounds(df)
    if bounds is None:
        return None
    start_d, end_d = bounds
    return (start_d.year, start_d.month), (end_d.year, end_d.month)


def iter_year_months(
    min_ym: tuple[int, int],
    max_ym: tuple[int, int],
) -> list[tuple[int, int]]:
    y, m = min_ym
    end_y, end_m = max_ym
    months: list[tuple[int, int]] = []
    while (y, m) <= (end_y, end_m):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def month_bounds_to_dates(
    start_ym: tuple[int, int],
    end_ym: tuple[int, int],
) -> tuple[date, date]:
    start_y, start_m = start_ym
    end_y, end_m = end_ym
    range_start = date(start_y, start_m, 1)
    range_end = date(end_y, end_m, calendar.monthrange(end_y, end_m)[1])
    return range_start, range_end


def work_end_date_bounds(df: pd.DataFrame) -> tuple[date, date] | None:
    if df.empty or "작업종료" not in df.columns:
        return None
    valid = df["작업종료"].dropna()
    if valid.empty:
        return None
    return valid.min().date(), valid.max().date()


def yearly_trend(
    df: pd.DataFrame,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> pd.DataFrame:
    cols = ["년", "파손 건수"]
    if df.empty or "작업종료" not in df.columns:
        return pd.DataFrame(columns=cols)
    valid = df.dropna(subset=["작업종료"]).copy()
    if valid.empty and (range_start is None or range_end is None):
        return pd.DataFrame(columns=cols)
    if range_start is None or range_end is None:
        if valid.empty:
            return pd.DataFrame(columns=cols)
        range_start = valid["작업종료"].min().date()
        range_end = valid["작업종료"].max().date()
    counts = (
        valid.assign(년=valid["작업종료"].dt.year)
        .groupby("년", as_index=False)
        .size()
        .rename(columns={"size": "파손 건수"})
    )
    full_years = range(range_start.year, range_end.year + 1)
    result = pd.DataFrame({"년": [f"{y}년" for y in full_years]})
    counts["년"] = counts["년"].map(lambda y: f"{int(y)}년")
    result = result.merge(counts, on="년", how="left")
    result["파손 건수"] = result["파손 건수"].fillna(0).astype(int)
    return result[cols]


def monthly_trend(
    df: pd.DataFrame,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> pd.DataFrame:
    cols = ["기간", "년", "월", "파손 건수"]
    if df.empty or "작업종료" not in df.columns:
        return pd.DataFrame(columns=cols)
    valid = df.dropna(subset=["작업종료"]).copy()
    if valid.empty and (range_start is None or range_end is None):
        return pd.DataFrame(columns=cols)
    if range_start is None or range_end is None:
        if valid.empty:
            return pd.DataFrame(columns=cols)
        range_start = valid["작업종료"].min().date()
        range_end = valid["작업종료"].max().date()
    counts = (
        valid.assign(
            년=valid["작업종료"].dt.year,
            월=valid["작업종료"].dt.month,
        )
        .groupby(["년", "월"], as_index=False)
        .size()
        .rename(columns={"size": "파손 건수"})
    )
    full_months = pd.period_range(
        start=range_start.replace(day=1),
        end=range_end.replace(day=1),
        freq="M",
    )
    result = pd.DataFrame(
        {
            "년": full_months.year,
            "월": full_months.month,
            "기간": [f"{p.year}년 {p.month}월" for p in full_months],
        }
    )
    result = result.merge(counts, on=["년", "월"], how="left")
    result["파손 건수"] = result["파손 건수"].fillna(0).astype(int)
    return result[cols]


def daily_trend(
    df: pd.DataFrame,
    *,
    range_start: date | None = None,
    range_end: date | None = None,
) -> pd.DataFrame:
    cols = ["날짜_원본", "날짜", "일", "월", "년", "파손 건수"]
    if df.empty or "작업종료" not in df.columns:
        return pd.DataFrame(columns=cols)
    valid = df.dropna(subset=["작업종료"]).copy()
    if valid.empty and (range_start is None or range_end is None):
        return pd.DataFrame(columns=cols)
    if not valid.empty:
        valid["날짜_원본"] = valid["작업종료"].dt.date
        counts = (
            valid.groupby("날짜_원본", as_index=False)
            .size()
            .rename(columns={"size": "파손 건수"})
        )
    else:
        counts = pd.DataFrame(columns=["날짜_원본", "파손 건수"])
    if range_start is not None and range_end is not None:
        start, end = range_start, range_end
    elif not valid.empty:
        start = valid["날짜_원본"].min()
        end = valid["날짜_원본"].max()
    else:
        return pd.DataFrame(columns=cols)
    full_days = pd.date_range(start=start, end=end, freq="D").date
    result = pd.DataFrame({"날짜_원본": full_days}).merge(counts, on="날짜_원본", how="left")
    result["파손 건수"] = result["파손 건수"].fillna(0).astype(int)
    result["일"] = result["날짜_원본"].map(lambda d: str(d.day))
    result["월"] = result["날짜_원본"].map(lambda d: f"{d.month}월")
    result["년"] = result["날짜_원본"].map(lambda d: str(d.year))
    result["날짜"] = result["날짜_원본"].map(format_date_korean)
    return result[cols]


def drill_usage_by_equipment(df: pd.DataFrame, top_n: int | None = 20) -> pd.DataFrame:
    if df.empty or "드릴사용량" not in df.columns:
        return pd.DataFrame(columns=["설비", "평균 드릴사용량", "파손 건수"])
    valid = df.dropna(subset=["드릴사용량", "설비"])
    if valid.empty:
        return pd.DataFrame(columns=["설비", "평균 드릴사용량", "파손 건수"])
    agg = (
        valid.groupby("설비", as_index=False)
        .agg(평균_드릴사용량=("드릴사용량", "mean"), 파손_건수=("드릴사용량", "count"))
        .sort_values("파손_건수", ascending=False)
    )
    if top_n is not None:
        agg = agg.head(top_n)
    agg["평균_드릴사용량"] = agg["평균_드릴사용량"].round(1)
    agg.columns = ["설비", "평균 드릴사용량", "파손 건수"]
    return agg


def product_equipment_matrix(
    df: pd.DataFrame,
    top_products: int | None = 10,
    top_equipment: int | None = 10,
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    sub = df.copy()
    sub["제품코드"] = sub["제품코드"].astype(str).str.strip().replace({"": "(미입력)", "nan": "(미입력)"})
    prod_counts = sub["제품코드"].value_counts()
    equip_counts = sub["설비"].value_counts()
    top_p = prod_counts.index if top_products is None else prod_counts.head(top_products).index
    top_e = equip_counts.index if top_equipment is None else equip_counts.head(top_equipment).index
    filt = sub[sub["제품코드"].isin(top_p) & sub["설비"].isin(top_e)]
    if filt.empty:
        return pd.DataFrame()
    return pd.crosstab(filt["제품코드"], filt["설비"]).sort_index()
