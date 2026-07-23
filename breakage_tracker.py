"""설비 상태 변화 추적 및 파손(빨간색) 이벤트 카운트."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from equipment_layout import ALL_EQUIPMENT_IDS, EquipmentStatus, effective_equipment_status


def _empty_record(equipment_id: str, **extra: str | float | None) -> dict:
    return {
        "product_code": extra.get("product_code", "") or "",
        "equipment_id": equipment_id,
        "work_end": extra.get("work_end", "") or "",
        "registrar": extra.get("registrar", "") or "",
        "tool_description": extra.get("tool_description", "") or "",
        "drill_lot": extra.get("drill_lot", "") or "",
        "broken_hole_number": extra.get("broken_hole_number", "") or "",
        "drill_usage": extra.get("drill_usage"),
        "breakage_type": extra.get("breakage_type", "") or "",
        "remarks": extra.get("remarks", "") or "",
    }


@dataclass
class BreakageEvent:
    equipment_id: str
    timestamp: str
    event_type: str
    previous_status: str = ""
    previous_count: int = 0
    new_count: int = 0
    note: str = ""


@dataclass
class TrackerState:
    breakage_counts: dict[str, int] = field(default_factory=dict)
    current_status: dict[str, str] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    detail_records: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        for eq_id in ALL_EQUIPMENT_IDS:
            self.breakage_counts.setdefault(eq_id, 0)
            self.current_status.setdefault(eq_id, EquipmentStatus.NORMAL.value)


class BreakageTracker:
    def __init__(self, data_path: Path) -> None:
        self.data_path = data_path
        self.state = self._load()

    def _load(self) -> TrackerState:
        if not self.data_path.exists():
            return TrackerState()
        try:
            raw = json.loads(self.data_path.read_text(encoding="utf-8"))
            state = TrackerState(
                breakage_counts=raw.get("breakage_counts", {}),
                current_status=raw.get("current_status", {}),
                events=raw.get("events", []),
                detail_records=raw.get("detail_records", []),
            )
            state.__post_init__()
            self._migrate_legacy_if_needed(state)
            self._sync_counts_and_status(state)
            return state
        except (json.JSONDecodeError, OSError):
            return TrackerState()

    @staticmethod
    def _migrate_legacy_if_needed(state: TrackerState) -> None:
        """예전 breakage_counts만 있는 데이터 → 이벤트 로그로 이전."""
        if state.detail_records:
            return
        for eq_id in ALL_EQUIPMENT_IDS:
            cnt = state.breakage_counts.get(eq_id, 0)
            for _ in range(cnt):
                state.detail_records.append(
                    _empty_record(eq_id, remarks="legacy_migration")
                )

    @staticmethod
    def _count_from_details(state: TrackerState, equipment_id: str) -> int:
        return sum(1 for r in state.detail_records if r.get("equipment_id") == equipment_id)

    @staticmethod
    def _sync_counts_and_status(state: TrackerState) -> None:
        for eq_id in ALL_EQUIPMENT_IDS:
            count = BreakageTracker._count_from_details(state, eq_id)
            state.breakage_counts[eq_id] = count
            status = effective_equipment_status(count, EquipmentStatus.NORMAL.value)
            state.current_status[eq_id] = status.value

    def _append_synthetic(
        self,
        equipment_id: str,
        *,
        note: str = "",
        work_end: str | None = None,
    ) -> None:
        ts = work_end or datetime.now().isoformat(timespec="seconds")
        self.state.detail_records.append(
            _empty_record(equipment_id, work_end=ts, remarks=note)
        )

    def _trim_equipment_records(self, equipment_id: str, keep: int) -> None:
        kept: list[dict] = []
        removed = 0
        target_remove = self._count_from_details(self.state, equipment_id) - keep
        for rec in self.state.detail_records:
            if rec.get("equipment_id") == equipment_id and removed < target_remove:
                removed += 1
                continue
            kept.append(rec)
        self.state.detail_records = kept

    def save(self) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        self._sync_counts_and_status(self.state)
        payload = {
            "breakage_counts": self.state.breakage_counts,
            "current_status": self.state.current_status,
            "events": self.state.events,
            "detail_records": self.state.detail_records,
        }
        self.data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def update_status(self, equipment_id: str, new_status: EquipmentStatus) -> bool:
        """비파손 → 파손 전환 시 True 반환(카운트 증가)."""
        prev = self.state.current_status.get(equipment_id, EquipmentStatus.NORMAL.value)
        new_val = new_status.value
        if prev == new_val:
            return False

        incremented = prev != EquipmentStatus.BROKEN.value and new_val == EquipmentStatus.BROKEN.value
        if incremented:
            prev_count = self._count_from_details(self.state, equipment_id)
            self._append_synthetic(equipment_id, note="status_breakage")
            self._sync_counts_and_status(self.state)
            new_count = self.state.breakage_counts[equipment_id]
            self._log_event(
                equipment_id,
                "status_breakage",
                previous_status=prev,
                previous_count=prev_count,
                new_count=new_count,
            )

        self.state.current_status[equipment_id] = new_val
        return incremented

    def update_batch(self, status_map: dict[str, EquipmentStatus]) -> list[str]:
        """여러 설비 상태를 한 번에 반영. 파손 카운트가 증가한 설비 ID 목록 반환."""
        newly_broken: list[str] = []
        for eq_id, status in status_map.items():
            if self.update_status(eq_id, status):
                newly_broken.append(eq_id)
        return newly_broken

    def get_count(self, equipment_id: str) -> int:
        return self._count_from_details(self.state, equipment_id)

    def get_detail_dataframe(self):
        from breakage_stats import records_to_dataframe

        return records_to_dataframe(self.state.detail_records)

    def _log_event(
        self,
        equipment_id: str,
        event_type: str,
        *,
        previous_status: str = "",
        previous_count: int = 0,
        new_count: int = 0,
        note: str = "",
    ) -> None:
        self.state.events.append(
            asdict(
                BreakageEvent(
                    equipment_id=equipment_id,
                    timestamp=datetime.now().isoformat(timespec="seconds"),
                    event_type=event_type,
                    previous_status=previous_status,
                    previous_count=previous_count,
                    new_count=new_count,
                    note=note,
                )
            )
        )

    def set_count(self, equipment_id: str, count: int, note: str = "") -> None:
        """이벤트 로그 건수를 목표값으로 맞춤."""
        if count < 0:
            raise ValueError("파손 횟수는 0 이상이어야 합니다.")
        prev = self._count_from_details(self.state, equipment_id)
        if prev == count:
            return
        if count < prev:
            self._trim_equipment_records(equipment_id, count)
        else:
            for _ in range(count - prev):
                self._append_synthetic(equipment_id, note=note or "manual_set")
        self._sync_counts_and_status(self.state)
        self._log_event(
            equipment_id,
            "manual_set",
            previous_count=prev,
            new_count=count,
            note=note,
        )

    def add_count(self, equipment_id: str, delta: int, note: str = "") -> None:
        """이벤트 로그에 delta건만큼 추가."""
        prev = self._count_from_details(self.state, equipment_id)
        new_count = prev + delta
        if new_count < 0:
            raise ValueError("결과 파손 횟수가 0 미만이 됩니다.")
        if delta > 0:
            for _ in range(delta):
                self._append_synthetic(equipment_id, note=note or "manual_add")
        elif delta < 0:
            self._trim_equipment_records(equipment_id, new_count)
        self._sync_counts_and_status(self.state)
        if delta != 0:
            self._log_event(
                equipment_id,
                "manual_add",
                previous_count=prev,
                new_count=new_count,
                note=note,
            )

    def import_counts(
        self,
        counts: dict[str, int],
        *,
        mode: str = "set",
        statuses: dict[str, EquipmentStatus] | None = None,
    ) -> int:
        """
        일괄 파손 횟수 반영.
        mode: set(덮어쓰기) | add(더하기) | replace_all(전체 교체, 미포함 설비는 0)
        반영된 설비 수 반환.
        """
        if mode not in ("set", "add", "replace_all"):
            raise ValueError("mode는 set, add, replace_all 중 하나여야 합니다.")

        updated = 0
        if mode == "replace_all":
            self.state.detail_records = []
            for eq_id in ALL_EQUIPMENT_IDS:
                val = counts.get(eq_id, 0)
                for _ in range(val):
                    self._append_synthetic(eq_id, note="bulk_import")
                if val > 0:
                    updated += 1
        else:
            for eq_id, val in counts.items():
                if eq_id not in ALL_EQUIPMENT_IDS:
                    continue
                if mode == "set":
                    prev = self._count_from_details(self.state, eq_id)
                    if prev != val:
                        self.set_count(eq_id, val, note="bulk_import")
                        updated += 1
                else:
                    if val != 0:
                        self.add_count(eq_id, val, note="bulk_import")
                        updated += 1

        if statuses:
            for eq_id, status in statuses.items():
                if eq_id in ALL_EQUIPMENT_IDS:
                    self.state.current_status[eq_id] = status.value

        self._sync_counts_and_status(self.state)
        return updated

    def import_detail_records(self, records: list[dict]) -> int:
        """파손 이벤트 로그 1행 = 파손 1건."""
        imported = 0
        for rec in records:
            eq_id = rec.get("equipment_id")
            if not eq_id or eq_id not in ALL_EQUIPMENT_IDS:
                continue
            self.state.detail_records.append(rec)
            imported += 1
        if imported:
            self._sync_counts_and_status(self.state)
        return imported

    def replace_from_event_csv(self, csv_path: Path) -> tuple[int, list[str]]:
        """저장소 CSV를 이벤트 로그 전체 소스로 반영."""
        from data_import import load_event_log_from_csv

        records, errors = load_event_log_from_csv(csv_path)
        self.state.detail_records = records
        self.state.events = []
        self._sync_counts_and_status(self.state)
        return len(records), errors

    def reset_counts(self) -> None:
        self.state.detail_records = []
        self.state.events = []
        self._sync_counts_and_status(self.state)

    def reset_all(self) -> None:
        self.state = TrackerState()
