"""HTML templates and inline-keyboard helpers for the Telegram bot."""
from __future__ import annotations

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..models import Bucket, ResolutionReport, Severity

SEV_BADGE: dict[Severity, str] = {
    Severity.INFO: "🟢",
    Severity.NOTICE: "🟡",
    Severity.IMPORTANT: "🔥",
    Severity.WARNING: "⚠",
    Severity.CRITICAL: "🚨",
}


def _h(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unit_sym(units: str) -> str:
    return "°F" if units == "fahrenheit" else "°C"


def fmt_now_card(
    *, station: str, name: str, t_c: float, dmax: float | None, lag_s: int, src: str, tz: str, units: str = "celsius"
) -> str:
    sym = _unit_sym(units)
    dmax_line = f" · max {dmax:+.1f}{sym}" if dmax is not None else ""
    return (
        f"🌡 <b>{station}</b> ({_h(name)})\n"
        f"Температура: <b>{t_c:+.1f}{sym}</b>{dmax_line}\n"
        f"⏱ {lag_s // 60}м назад"
    )


def fmt_event_alert(
    *,
    station: str,
    name: str,
    t_c: float,
    delta: float | None,
    daily_max: float | None,
    bucket_title: str | None,
    price: float | None,
    p_model: float | None,
    edge: float | None,
    time_to_close: str | None,
    lag_s: int | None,
    src: str | None,
    severity: Severity,
    units: str = "celsius",
) -> str:
    sym = _unit_sym(units)
    lines = [f"{SEV_BADGE[severity]} <b>{station}</b> — {t_c:+.1f}{sym}"]
    if delta is not None:
        lines.append(f"↕ {delta:+.1f}{sym}")
    if daily_max is not None:
        lines.append(f"📈 max {daily_max:+.1f}{sym}")
    if bucket_title:
        lines.append(f"🪣 {bucket_title}")
        if price is not None:
            lines.append(f"💰 {price:.2f}")
        if p_model is not None:
            lines.append(f"📊 {p_model:.0%}")
        if edge is not None:
            lines.append(f"⚡ edge {edge:+.2f}")
    if time_to_close:
        lines.append(f"⏳ {time_to_close}")
    return " | ".join(lines)


def fmt_buckets_table(rows: list[dict]) -> str:
    if not rows:
        return "<i>Нет данных.</i>"
    lines = ["<b>Бакеты</b>:", "<pre>"]
    width = max(len(r["title"]) for r in rows)
    for r in rows:
        title = r["title"].ljust(width)
        price = f"{r['price']:.2f}" if r.get("price") is not None else " -"
        pm = f"{r['p_model']:.0%}" if r.get("p_model") is not None else " -"
        edge = f"{r['edge']:+.2f}" if r.get("edge") is not None else " -"
        lines.append(f"{title}  💰{price}  📊{pm}  ⚡{edge}")
    lines.append("</pre>")
    return "\n".join(lines)


def fmt_resolution(report: ResolutionReport) -> str:
    sym = _unit_sym(report.units)
    source_name = "WU" if report.source == "wunderground" else "NOAA"
    return (
        f"✅ <b>Резолв</b> {report.date_local}\n"
        f"🏆 {report.winning_bucket_title or '?'}\n"
        f"🌡 {report.t_max_resolve_whole_c:+d}{sym} ({source_name})"
    )


def fmt_sources(rows: list[dict]) -> str:
    if not rows:
        return "<i>Нет данных.</i>"
    lines = ["<b>Пинг</b>:", "<pre>"]
    for r in rows:
        ok = "🟢" if r.get("ok") else "🔴"
        ms = f"{r.get('latency_ms', '-')}мс"
        lines.append(f"{ok} {r['name']:12s} {ms}")
    lines.append("</pre>")
    return "\n".join(lines)


def kb_event_actions(*, slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🪣 Бакеты", callback_data=f"buckets:{slug}"),
                InlineKeyboardButton(text="📈 Прогноз", callback_data=f"forecast:{slug}"),
            ]
        ]
    )


def kb_now() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="now:refresh"),
            ]
        ]
    )


def kb_resolution(*, slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔗 Открыть на Polymarket", url=f"https://polymarket.com/event/{slug}"),
            ]
        ]
    )


def kb_buckets(buckets: list[Bucket]) -> InlineKeyboardMarkup:
    rows = []
    for b in buckets:
        rows.append(
            [InlineKeyboardButton(text=b.title, callback_data=f"bucket:{b.market_id}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fmt_pinned_summary(
    *,
    slug: str,
    station: str,
    last_temp: float | None,
    daily_max: float | None,
    last_update: datetime | None,
    units: str = "celsius",
) -> str:
    sym = _unit_sym(units)
    last_str = last_update.astimezone(UTC).strftime("%H:%M") if last_update else "—"
    if last_temp is not None and daily_max is not None:
        return f"📌 {slug[:40]}\n{station} · {last_temp:+.1f}{sym} / max {daily_max:+.1f}{sym} · {last_str}"
    return f"📌 {slug[:40]}\n{station} · ждём данные…"
