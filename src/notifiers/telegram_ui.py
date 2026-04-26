"""HTML templates and inline-keyboard helpers for the Telegram bot.

We deliberately use HTML mode (not MarkdownV2): far less escaping pain,
emojis render natively, and aiogram's ParseMode.HTML is robust.
"""
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
    dmax_line = f"\nДневной max: <b>{dmax:+.1f}{sym}</b> 🔝" if dmax is not None else ""
    return (
        f"🌡 <b>{station} {_h(name)}</b>\n"
        f"T = <b>{t_c:+.1f}{sym}</b>"
        f"{dmax_line}\n"
        f"⚡ lag: {lag_s}s · src: {src} · tz: {tz}"
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
    lines = [
        f"{SEV_BADGE[severity]} <b>{station} {_h(name)}</b>",
        f"T = <b>{t_c:+.1f}{sym}</b>" + (f"  ⬆ <i>(Δ {delta:+.1f})</i>" if delta is not None else ""),
    ]
    if daily_max is not None:
        lines.append(f"Дневной max: <b>{daily_max:+.1f}{sym}</b> 🔝")
    if bucket_title:
        edge_str = f" · edge {edge:+.2f}" if edge is not None else ""
        price_str = f" · price {price:.2f}" if price is not None else ""
        pmodel_str = f" · P_model {p_model:.2f}" if p_model is not None else ""
        lines.append(f"Бакет (info): <b>{_h(bucket_title)}</b>{price_str}{pmodel_str}{edge_str}")
    if time_to_close:
        lines.append(f"До конца окна: {time_to_close}")
    if lag_s is not None and src is not None:
        lines.append(f"⚡ lag: {lag_s}s · src: {src}")
    return "\n".join(lines)


def fmt_buckets_table(rows: list[dict]) -> str:
    if not rows:
        return "<i>Нет данных по бакетам.</i>"
    lines = ["<b>Бакеты</b> (price | P_model | edge):", "<pre>"]
    width = max(len(r["title"]) for r in rows)
    for r in rows:
        title = r["title"].ljust(width)
        price = f"{r['price']:.2f}" if r.get("price") is not None else "  - "
        pm = f"{r['p_model']:.2f}" if r.get("p_model") is not None else "  - "
        edge = f"{r['edge']:+.2f}" if r.get("edge") is not None else "  -"
        lines.append(f"{title}  {price}  {pm}  {edge}")
    lines.append("</pre>")
    return "\n".join(lines)


def fmt_resolution(report: ResolutionReport) -> str:
    sym = _unit_sym(report.units)
    info_t = (
        f"\nT_max (METAR 0.1°C, info): {report.t_max_info_metar_c:+.1f}°C"
        if report.t_max_info_metar_c is not None
        else ""
    )
    source_name = "Wunderground" if report.source == "wunderground" else "Synoptic Data"
    return (
        f"✅ <b>Резолв опубликован</b>\n"
        f"{report.station} · {report.date_local} ({report.timezone})\n"
        f"T_max ({source_name}, whole {sym}): <b>{report.t_max_resolve_whole_c:+d}{sym}</b>"
        f"{info_t}\n"
        f"Победивший бакет: <b>{_h(report.winning_bucket_title or '?')}</b>\n"
        f"Source: {source_name} · {report.hourly_count}/24 hourly observations\n"
        f"<i>Generated: {report.generated_at:%Y-%m-%d %H:%M UTC}</i>"
    )


def fmt_sources(rows: list[dict]) -> str:
    if not rows:
        return "<i>Нет данных по источникам.</i>"
    lines = ["<b>Источники</b>:", "<pre>"]
    for r in rows:
        ok = "✓" if r.get("ok") else "✗"
        lines.append(
            f"{ok} {r['name']:14s} latency={r.get('latency_ms', '-')}ms last={r.get('last_seen', '-')}"
        )
    lines.append("</pre>")
    return "\n".join(lines)


def kb_event_actions(*, slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 Бакеты", callback_data=f"buckets:{slug}"),
                InlineKeyboardButton(text="📈 Прогноз", callback_data=f"forecast:{slug}"),
                InlineKeyboardButton(text="🔕 Тише", callback_data=f"mute:{slug}"),
            ]
        ]
    )


def kb_now() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Обновить", callback_data="now:refresh"),
                InlineKeyboardButton(text="📊 Сегодня", callback_data="today"),
                InlineKeyboardButton(text="⚙ Настройки", callback_data="settings"),
            ]
        ]
    )


def kb_resolution(*, slug: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 JSON", callback_data=f"json:{slug}"),
                InlineKeyboardButton(
                    text="🔗 Polymarket", url=f"https://polymarket.com/event/{slug}"
                ),
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
    last_str = last_update.astimezone(UTC).strftime("%H:%M UTC") if last_update else "—"
    return (
        f"📌 <b>{slug}</b>\n"
        f"{station} · last {last_temp:+.1f}{sym} · max {daily_max:+.1f}{sym}\n"
        f"<i>upd {last_str}</i>"
        if last_temp is not None and daily_max is not None
        else f"📌 <b>{slug}</b>\n{station} · ожидаем данные…"
    )
