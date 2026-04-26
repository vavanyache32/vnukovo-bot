"""Routes events to channels with per-severity policy and dedup/cooldown."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from loguru import logger

from ..config import Settings, Station, get_settings
from ..models import Bucket, Notification, ResolutionReport, Severity
from ..storage import save_notification


class NotifierRouter:
    def __init__(self, *, telegram=None, settings: Settings | None = None) -> None:  # type: ignore[no-untyped-def]
        self.s = settings or get_settings()
        self.telegram = telegram  # TelegramBot instance (lazy)
        self._last_sent: dict[tuple[str, Severity], datetime] = {}
        self._cooldowns: dict[Severity, timedelta] = {
            Severity.INFO: timedelta(minutes=5),
            Severity.NOTICE: timedelta(seconds=60),
            Severity.IMPORTANT: timedelta(seconds=0),
            Severity.WARNING: timedelta(minutes=2),
            Severity.CRITICAL: timedelta(seconds=0),
        }
        self._dedup: dict[str, datetime] = {}

    # ---------- public surface ----------
    async def send_info(self, text: str) -> None:
        await self._send(Severity.INFO, title="info", text=text)

    async def send_event(
        self,
        slug: str,
        ev,  # AggEvent  # type: ignore[no-untyped-def]
        *,
        station: Station | None = None,
        buckets: list[Bucket] | None = None,
    ) -> None:
        if not self._allow(ev.severity, ev.kind, ev.text):
            return
        text = self._format_event(slug, ev, station=station)
        n = Notification(
            severity=ev.severity,
            title=ev.kind,
            body=text,
            payload=ev.payload,
            dedup_key=self._dedup_key(ev.kind, ev.text),
            created_at=datetime.now(UTC),
        )
        await save_notification(n)
        await self._send(ev.severity, title=ev.kind, text=text)

    async def send_resolution(self, report: ResolutionReport) -> None:
        sym = "°F" if report.units == "fahrenheit" else "°C"
        source_name = "Wunderground" if report.source == "wunderground" else "NWS"
        text = (
            f"✅ <b>Резолв опубликован</b>\n"
            f"{report.station} · {report.date_local} ({report.timezone})\n"
            f"T_max ({source_name}): <b>{report.t_max_resolve_whole_c:+d}{sym}</b>\n"
            f"Bucket: <b>{report.winning_bucket_title}</b>"
        )
        await self._send(Severity.CRITICAL, title="resolution", text=text)

    # ---------- internals ----------
    def _allow(self, sev: Severity, kind: str, text: str) -> bool:
        key = self._dedup_key(kind, text)
        now = datetime.now(UTC)
        last = self._dedup.get(key)
        if last is not None and (now - last) < timedelta(minutes=5):
            return False
        self._dedup[key] = now
        cd = self._cooldowns.get(sev, timedelta())
        sev_key = (kind, sev)
        prev = self._last_sent.get(sev_key)
        if prev and (now - prev) < cd:
            return False
        self._last_sent[sev_key] = now
        return True

    @staticmethod
    def _dedup_key(kind: str, text: str) -> str:
        return hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()[:16]

    def _format_event(self, slug: str, ev, *, station: Station | None) -> str:  # type: ignore[no-untyped-def]
        from .telegram_ui import SEV_BADGE

        badge = SEV_BADGE.get(ev.severity, "•")
        prefix = f"{badge} <b>{slug}</b>"
        if station is not None:
            prefix += f" · {station.icao}"
        return f"{prefix}\n<b>{ev.kind}</b>: {ev.text}"

    async def _send(self, sev: Severity, *, title: str, text: str) -> None:
        if self.telegram is None:
            logger.info("[{}] {}: {}", sev.value, title, text.replace("\n", " | "))
            return
        for chat_id in self.s.admin_ids:
            await self.telegram.send_text(chat_id, text)
