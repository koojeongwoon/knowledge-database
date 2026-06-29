import re
from datetime import datetime, timedelta, timezone
from typing import Optional

class RefreshTarget:
    """지식 최신성 검사 대상을 정의하는 도메인 값 객체 (Value Object)"""
    def __init__(self, file_path: str, refresh_interval: str, refresh_source: str, last_refresh: Optional[str] = None):
        self.file_path = file_path
        self.refresh_interval = refresh_interval.strip().lower()
        self.refresh_source = refresh_source.strip().lower()
        self.last_refresh = last_refresh

    def is_expired(self, current_time: datetime) -> bool:
        """현재 시간 기준 검사 주기가 지났는지 판별합니다."""
        if self.refresh_interval in ("never", "off", ""):
            return False

        if not self.last_refresh:
            return True  # 갱신 기록이 없으면 만료로 판정

        try:
            # last_refresh 파싱 (YYYY-MM-DD 또는 ISO 포맷 지원)
            if "t" in self.last_refresh.lower():
                last_dt = datetime.fromisoformat(self.last_refresh)
            else:
                last_dt = datetime.strptime(self.last_refresh, "%Y-%m-%d")
                # Timezone 부여 (일관성)
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return True  # 파싱 실패 시 갱신하도록 판정

        delta = self.parse_interval(self.refresh_interval)
        if not delta:
            return False

        return current_time >= (last_dt + delta)

    @staticmethod
    def parse_interval(interval_str: str) -> Optional[timedelta]:
        """'7d', '12h', '2w' 등의 주기를 timedelta로 파싱합니다."""
        match = re.match(r"^(\d+)([dhw])$", interval_str)
        if not match:
            return None
        
        val = int(match.group(1))
        unit = match.group(2)

        if unit == "d":
            return timedelta(days=val)
        elif unit == "h":
            return timedelta(hours=val)
        elif unit == "w":
            return timedelta(weeks=val)
        return None
