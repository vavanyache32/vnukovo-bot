"""aiogram v3 bot: full inline + reply UI, every action via tappable button.

Goals:
* Reply keyboard (always at bottom) for muscle-memory navigation.
* Inline keyboard (under each response) for in-place edit_message updates.
* Every command also reachable as `action:<x>` callback so taps avoid retyping.
"""
from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from loguru import logger

from ..config import Settings, get_settings, get_stations
from ..models import Bucket, MarketEvent, ResolutionReport
from ..sources import polymarket_clob, polymarket_gamma
from ..storage import load_state
from . import telegram_ui as ui


# ============================================================================
# Keyboards
# ============================================================================
def kb_main_inline() -> InlineKeyboardMarkup | None:  # type: ignore[return-value]
    """Inline keyboard intentionally disabled.

    User UX choice: only the persistent reply keyboard at the bottom is used.
    Returning None means handlers do not attach an under-message keyboard.
    """
    return None


def kb_main_reply() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard — sticks to the bottom of the chat."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🌡 Сейчас"), KeyboardButton(text="📊 Сегодня")],
            [KeyboardButton(text="🪣 Бакеты"), KeyboardButton(text="📈 Прогноз")],
            [KeyboardButton(text="📋 Рынки"), KeyboardButton(text="⭐ Мои рынки")],
            [KeyboardButton(text="📡 Источники"), KeyboardButton(text="❓ Помощь")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


# ============================================================================
# Session helpers
# ============================================================================
def _build_session(settings: Settings) -> AiohttpSession:
    proxy = settings.proxy_telegram or None
    if proxy and proxy.startswith("socks"):
        try:
            return AiohttpSession(proxy=proxy)
        except Exception:  # pragma: no cover
            logger.exception("telegram: failed to attach SOCKS proxy")
            raise
    return AiohttpSession(proxy=proxy)


# ============================================================================
# Bot
# ============================================================================
class TelegramBot:
    def __init__(
        self,
        settings: Settings | None = None,
        market_manager: Any = None,
    ) -> None:
        self.s = settings or get_settings()
        self.market_manager = market_manager
        if not self.s.telegram_bot_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        token = self.s.telegram_bot_token.get_secret_value()
        self.session = _build_session(self.s)
        self.bot = Bot(
            token=token,
            session=self.session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self._register_handlers()

    # ------------- send helpers -------------
    async def send_text(self, chat_id: int, text: str, **kwargs: Any) -> None:
        try:
            await self.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception:
            logger.exception("telegram: send_message failed")

    async def edit_text(self, chat_id: int, message_id: int, text: str, **kwargs: Any) -> None:
        try:
            await self.bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text, **kwargs
            )
        except Exception:
            logger.exception("telegram: edit_message_text failed")

    async def close(self) -> None:
        await self.bot.session.close()

    # ============================================================
    # Action implementations — all UI surfaces share these
    # ============================================================
    async def _action_now(self) -> tuple[str, InlineKeyboardMarkup]:
        from ..parser.metar import parse_metar
        from ..sources import awc

        slug = await self._default_slug()
        cfg = get_stations()
        station = cfg.by_slug(slug) if slug else cfg.by_key(self.s.default_city)
        if station is None:
            return "❌ Станция не найдена в stations.yaml", kb_main_inline()
        results = await awc.fetch_latest(station.icao)
        if not results:
            return (
                "❌ Не удалось получить METAR — попробуй ещё раз через 30 сек.\n"
                "Источник: NOAA AWC.",
                kb_main_inline(),
            )
        try:
            parsed = parse_metar(results[0].raw)
        except Exception as e:
            return f"❌ Не удалось распарсить METAR: {e}", kb_main_inline()
        lag = int((datetime.now(UTC) - parsed.issue_time).total_seconds())
        text = ui.fmt_now_card(
            station=station.icao,
            name=station.name,
            t_c=parsed.temperature_c,
            dmax=None,
            lag_s=lag,
            src="AWC",
            tz=station.tz,
            units=station.units,
        )
        return text, kb_main_inline()

    async def _action_today(self) -> tuple[str, InlineKeyboardMarkup]:
        slug = await self._default_slug()
        if slug is None:
            return "Нет активных рынков.", kb_main_inline()
        state = await load_state(slug)
        if not state:
            return f"<b>{slug}</b>\nПока нет данных.", kb_main_inline()
        cfg = get_stations()
        station = cfg.by_slug(slug)
        units = station.units if station else "celsius"
        sym = "°F" if units == "fahrenheit" else "°C"
        dmax = state.get("daily_max_info")
        last_t = state.get("last_temp_c")
        last_b = state.get("last_bucket_threshold")
        lines = [f"📊 <b>{slug[:40]}</b>"]
        if last_t is not None:
            lines.append(f"Сейчас: <b>{last_t:+.1f}{sym}</b>")
        if dmax is not None:
            lines.append(f"Макс: <b>{dmax:+.1f}{sym}</b>")
        if last_b is not None:
            lines.append(f"Бакет: <b>{last_b}{sym}</b>")
        return "\n".join(lines), kb_main_inline()

    async def _action_buckets(
        self, slug: str | None = None
    ) -> tuple[str, InlineKeyboardMarkup]:
        if slug is None:
            slug = await self._default_slug()
        if slug is None:
            return "❌ Не найдено активных рынков.", kb_main_inline()
        ev = await polymarket_gamma.fetch_event_by_slug(slug)
        if ev is None:
            return f"❌ Событие не найдено: <code>{slug}</code>", kb_main_inline()
        try:
            prices = await polymarket_clob.fetch_prices_for_buckets(ev.buckets)
        except Exception:
            prices = {}
        rows: list[dict[str, Any]] = []
        for b in ev.buckets:
            p = prices.get(b.market_id)
            rows.append(
                {
                    "title": b.title,
                    "price": p.yes_price if p else None,
                    "p_model": None,
                    "edge": None,
                }
            )
        # Enrich with model probabilities from saved state
        state = await load_state(slug)
        if state:
            probs = {p["title"]: p["p_model"] for p in state.get("bucket_probabilities", [])}
            for r in rows:
                if r["title"] in probs:
                    r["p_model"] = probs[r["title"]]
                    if r["price"] is not None:
                        r["edge"] = round(r["p_model"] - r["price"], 3)
        text = f"<b>{slug}</b>\n" + ui.fmt_buckets_table(rows)
        return text, kb_main_inline()

    async def _action_sources(self) -> tuple[str, InlineKeyboardMarkup]:
        from .. import http_client

        client = await http_client.get_client()
        checks = [
            ("AWC", "https://aviationweather.gov/api/data/metar?ids=KNYC&format=json&hours=1"),
            ("AVWX", "https://avwx.rest"),
            ("Synoptic", "https://api.synopticdata.com/v2/stations/metadata?stid=KNYC"),
            ("Polymarket", "https://gamma-api.polymarket.com/events?limit=1"),
            ("Telegram", "https://api.telegram.org"),
        ]
        rows = []
        for name, url in checks:
            t0 = time.monotonic()
            ok = False
            try:
                r = await client.get(url, timeout=10)
                ok = r.status_code < 500
            except Exception:
                pass
            ms = int((time.monotonic() - t0) * 1000)
            rows.append({"name": name, "ok": ok, "latency_ms": ms})
        return ui.fmt_sources(rows), kb_main_inline()

    async def _action_events(self) -> tuple[str, InlineKeyboardMarkup]:
        """List markets matching the configured slug pattern."""
        months = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ]
        cfg = get_stations()
        station = cfg.by_key(self.s.default_city)
        z = station.zoneinfo if station else self.s.resolution_zone
        now_local = datetime.now(z)

        found: list[MarketEvent] = []
        seen: set[str] = set()
        for delta in range(-1, 3):
            d = now_local + timedelta(days=delta)
            date_part = f"{months[d.month - 1]}-{d.day}-{d.year}"
            for pattern in self.s.slug_patterns:
                slug = pattern.replace("*", date_part)
                if slug in seen:
                    continue
                seen.add(slug)
                try:
                    ev = await polymarket_gamma.fetch_event_by_slug(slug)
                except Exception:
                    continue
                if ev is not None:
                    found.append(ev)

        if not found:
            return "Рынков не найдено.", kb_main_inline()
        lines = [f"• {e.slug[:50]} ({len(e.buckets)} бакетов)" for e in found[:10]]
        return "<b>Рынки</b>:\n" + "\n".join(lines), kb_main_inline()

    async def _action_forecast(self) -> tuple[str, InlineKeyboardMarkup]:
        slug = await self._default_slug()
        if slug is None:
            return "Нет активного рынка.", kb_main_inline()
        state = await load_state(slug)
        probs = state.get("bucket_probabilities", []) if state else []
        if not probs:
            return f"<b>{slug}</b>\nПрогноз появится через ~15 мин.", kb_main_inline()
        lines = [f"📈 <b>Прогноз</b>: {slug}", "<pre>"]
        width = max(len(p["title"]) for p in probs)
        for p in probs:
            title = p["title"].ljust(width)
            lines.append(f"{title}  {p['p_model']:.0%}")
        lines.append("</pre>")
        return "\n".join(lines), kb_main_inline()

    def _settings_text(self) -> str:
        return (
            "⚙ <b>Настройки</b>\n\n"
            f"Город: {self.s.default_city}\n"
            f"Проверка: каждые {self.s.poll_interval_seconds}с\n"
            f"Админы: {len(self.s.admin_ids)}"
        )

    def _help_text(self) -> str:
        return (
            "<b>Команды</b>\n"
            "/markets — найти рынки\n"
            "/mymarkets — мои подписки\n"
            "/now — текущая температура\n"
            "/today — дневной макс + бакет\n"
            "/buckets — цены бакетов\n"
            "/sources — пинг источников\n"
            "/resolve — финальный отчёт"
        )

    async def _action_markets(self) -> tuple[str, InlineKeyboardMarkup]:
        """List discoverable markets with subscribe buttons."""
        from datetime import timedelta

        from ..core.market_discovery import MarketDiscovery, fetch_event_or_raise
        from ..config import get_stations

        # Try discovery via Gamma API first
        disc = MarketDiscovery()
        result = await disc.run_once()
        events = list(result.new_events) + list(result.known_events)

        # Fallback: probe today for all stations in stations.yaml
        if not events:
            cfg = get_stations()
            months = [
                "january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december",
            ]
            for st in cfg.stations.values():
                if not st.slug_pattern:
                    continue
                z = st.zoneinfo
                now_local = datetime.now(z)
                d = now_local
                date_part = f"{months[d.month - 1]}-{d.day}-{d.year}"
                slug = st.slug_pattern.replace("*", date_part)
                try:
                    ev = await fetch_event_or_raise(slug)
                    if ev and not any(e.slug == ev.slug for e in events):
                        events.append(ev)
                except Exception:
                    continue

        if not events:
            return "Рынков не найдено. Попробуй позже.", kb_main_inline()
        lines = ["<b>Рынки</b> — нажми ▶️"]
        buttons = []
        for ev in events[:10]:
            lines.append(f"• {ev.slug[:45]}")
            buttons.append(
                [InlineKeyboardButton(text=f"▶️ Подписаться", callback_data=f"subscribe:{ev.slug}")]
            )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _action_mymarkets(self) -> tuple[str, InlineKeyboardMarkup]:
        """List currently subscribed/active markets."""
        from ..storage import load_subscriptions

        subs = await load_subscriptions()
        active = []
        if self.market_manager is not None:
            active = await self.market_manager.list_active()
        if not subs and not active:
            return "⭐ Нет подписок. /markets — найти рынки.", kb_main_inline()
        lines = ["⭐ <b>Мои рынки</b>"]
        buttons = []
        seen = set(subs) | set(active)
        for slug in sorted(seen):
            status = "🟢" if slug in active else "⏸"
            lines.append(f"{status} {slug[:50]}")
            buttons.append(
                [InlineKeyboardButton(text="⏹ Отписаться", callback_data=f"unsubscribe:{slug}")]
            )
        return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _default_slug(self) -> str | None:
        """Pick the most relevant slug: active subscription > today markets > fallback."""
        from ..storage import load_subscriptions

        # 1. Active subscriptions take priority
        subs = await load_subscriptions()
        if self.market_manager is not None:
            active = await self.market_manager.list_active()
            for slug in active:
                return slug
        if subs:
            return subs[0]

        # 2. Legacy fallback via slug patterns
        months = [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ]
        cfg = get_stations()
        station = cfg.by_key(self.s.default_city)
        z = station.zoneinfo if station else self.s.resolution_zone
        now_local = datetime.now(z)

        for delta in (0, 1, -1):
            d = now_local + timedelta(days=delta)
            date_part = f"{months[d.month - 1]}-{d.day}-{d.year}"
            for pattern in self.s.slug_patterns:
                slug = pattern.replace("*", date_part)
                try:
                    ev = await polymarket_gamma.fetch_event_by_slug(slug)
                except Exception:
                    ev = None
                if ev is not None:
                    return slug
        return None

    async def _edit_or_answer(
        self, c: CallbackQuery, text: str, kb: InlineKeyboardMarkup
    ) -> None:
        try:
            if c.message:
                await c.message.edit_text(text, reply_markup=kb)
            await c.answer()
        except Exception:
            try:
                if c.message:
                    await c.message.answer(text, reply_markup=kb)
            except Exception:
                logger.exception("telegram: edit_or_answer fallback failed")
            await c.answer()

    # ============================================================
    # Handler registration
    # ============================================================
    def _register_handlers(self) -> None:
        dp = self.dp

        # ----- /start -----
        @dp.message(CommandStart())
        async def _start(m: Message) -> None:
            await m.answer(
                "🌡 <b>Weather Bot</b>\n\n"
                "📋 /markets — найти рынки\n"
                "⭐ /mymarkets — мои подписки\n"
                "❓ /help — помощь",
                reply_markup=kb_main_reply(),
            )

        # ----- /now (text + reply-button) -----
        @dp.message(Command("now"))
        @dp.message(F.text == "🌡 Сейчас")
        async def _now(m: Message) -> None:
            text, kb = await self._action_now()
            await m.answer(text, reply_markup=kb)

        # ----- /today -----
        @dp.message(Command("today"))
        @dp.message(F.text == "📊 Сегодня")
        async def _today(m: Message) -> None:
            text, kb = await self._action_today()
            await m.answer(text, reply_markup=kb)

        # ----- /buckets -----
        @dp.message(Command("buckets"))
        async def _buckets_cmd(m: Message) -> None:
            args = (m.text or "").split(maxsplit=1)
            slug = args[1].strip() if len(args) > 1 else None
            text, kb = await self._action_buckets(slug)
            await m.answer(text, reply_markup=kb)

        @dp.message(F.text == "🪣 Бакеты")
        async def _buckets_btn(m: Message) -> None:
            text, kb = await self._action_buckets()
            await m.answer(text, reply_markup=kb)

        # ----- /sources -----
        @dp.message(Command("sources"))
        @dp.message(F.text == "📡 Источники")
        async def _sources(m: Message) -> None:
            await m.answer("Проверяю источники…")
            text, kb = await self._action_sources()
            await m.answer(text, reply_markup=kb)

        # ----- /events -----
        @dp.message(Command("events"))
        @dp.message(F.text == "🎯 События")
        async def _events(m: Message) -> None:
            text, kb = await self._action_events()
            await m.answer(text, reply_markup=kb)

        # ----- /forecast -----
        @dp.message(F.text == "📈 Прогноз")
        async def _forecast_btn(m: Message) -> None:
            text, kb = await self._action_forecast()
            await m.answer(text, reply_markup=kb)

        # ----- /settings -----
        @dp.message(F.text == "⚙ Настройки")
        async def _settings_btn(m: Message) -> None:
            await m.answer(self._settings_text(), reply_markup=kb_main_inline())

        # ----- /help -----
        @dp.message(Command("help"))
        @dp.message(F.text == "❓ Помощь")
        async def _help(m: Message) -> None:
            await m.answer(self._help_text(), reply_markup=kb_main_inline())

        # ----- /resolve -----
        @dp.message(Command("resolve"))
        async def _resolve_cmd(m: Message) -> None:
            args = (m.text or "").split()
            if len(args) < 3:
                await m.answer(
                    "Использование: <code>/resolve YYYY-MM-DD &lt;slug&gt;</code>",
                    reply_markup=kb_main_inline(),
                )
                return
            from ..core.resolver import resolve as run_resolve

            date, slug = args[1], args[2]
            cfg = get_stations()
            station = cfg.by_slug(slug)
            if station is None:
                await m.answer(
                    f"❌ Не найдено маппинга станции для <code>{slug}</code>",
                    reply_markup=kb_main_inline(),
                )
                return
            ev = await polymarket_gamma.fetch_event_by_slug(slug)
            if ev is None:
                await m.answer(
                    f"❌ Событие не найдено: <code>{slug}</code>",
                    reply_markup=kb_main_inline(),
                )
                return
            await m.answer("⏳ Жду финализации NOAA Synoptic, это может занять до 48 ч…")
            try:
                report = await run_resolve(
                    slug=slug,
                    event_id=ev.event_id,
                    station=station,
                    date_local=date,
                    buckets=ev.buckets,
                )
            except Exception as e:
                await m.answer(f"❌ Резолв провален: {e}", reply_markup=kb_main_inline())
                return
            await m.answer(ui.fmt_resolution(report), reply_markup=kb_main_inline())

        # ----- /markets -----
        @dp.message(Command("markets"))
        @dp.message(F.text == "📋 Рынки")
        async def _markets(m: Message) -> None:
            text, kb = await self._action_markets()
            await m.answer(text, reply_markup=kb)

        # ----- /mymarkets -----
        @dp.message(Command("mymarkets"))
        @dp.message(F.text == "⭐ Мои рынки")
        async def _mymarkets(m: Message) -> None:
            text, kb = await self._action_mymarkets()
            await m.answer(text, reply_markup=kb)

        # ----- inline subscribe / unsubscribe -----
        @dp.callback_query(F.data.startswith("subscribe:"))
        async def _cb_subscribe(c: CallbackQuery) -> None:
            slug = c.data.split(":", 1)[1] if c.data else ""
            if self.market_manager is not None and slug:
                await self.market_manager.start_market(slug)
                await c.answer(f"Подписался на {slug}")
                await self._edit_or_answer(c, f"✅ Подписка на <b>{slug}</b> активна", kb_main_inline())
            else:
                await c.answer("Ошибка: менеджер рынков недоступен")

        @dp.callback_query(F.data.startswith("unsubscribe:"))
        async def _cb_unsubscribe(c: CallbackQuery) -> None:
            slug = c.data.split(":", 1)[1] if c.data else ""
            if self.market_manager is not None and slug:
                await self.market_manager.stop_market(slug)
                from ..storage import remove_subscription
                await remove_subscription(slug)
                await c.answer(f"Отписался от {slug}")
                await self._edit_or_answer(c, f"⏹ Отписка от <b>{slug}</b>", kb_main_inline())
            else:
                await c.answer("Ошибка: менеджер рынков недоступен")

        # ============================================================
        # Inline callbacks (action:*)
        # ============================================================
        @dp.callback_query(F.data == "action:now")
        async def _cb_now(c: CallbackQuery) -> None:
            text, kb = await self._action_now()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:today")
        async def _cb_today(c: CallbackQuery) -> None:
            text, kb = await self._action_today()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:buckets")
        async def _cb_buckets(c: CallbackQuery) -> None:
            await c.answer("Загружаю…")
            text, kb = await self._action_buckets()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:sources")
        async def _cb_sources(c: CallbackQuery) -> None:
            await c.answer("Пингую источники…")
            text, kb = await self._action_sources()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:events")
        async def _cb_events(c: CallbackQuery) -> None:
            text, kb = await self._action_events()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:forecast")
        async def _cb_forecast(c: CallbackQuery) -> None:
            text, kb = await self._action_forecast()
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "action:settings")
        async def _cb_settings(c: CallbackQuery) -> None:
            await self._edit_or_answer(c, self._settings_text(), kb_main_inline())

        @dp.callback_query(F.data == "action:help")
        async def _cb_help(c: CallbackQuery) -> None:
            await self._edit_or_answer(c, self._help_text(), kb_main_inline())

        # Backward-compat: keep old callback names alive
        @dp.callback_query(F.data.startswith("buckets:"))
        async def _cb_buckets_legacy(c: CallbackQuery) -> None:
            slug = c.data.split(":", 1)[1] if c.data else ""
            text, kb = await self._action_buckets(slug or None)
            await self._edit_or_answer(c, text, kb)

        @dp.callback_query(F.data == "now:refresh")
        async def _cb_now_refresh(c: CallbackQuery) -> None:
            text, kb = await self._action_now()
            await self._edit_or_answer(c, text, kb)

    # ------------- runtime -------------
    async def run_polling(self) -> None:
        logger.info("telegram: starting polling")
        await self.dp.start_polling(self.bot, allowed_updates=self.dp.resolve_used_update_types())

    async def run_webhook(self) -> None:  # pragma: no cover
        url = self.s.telegram_webhook_url
        secret = (
            self.s.telegram_webhook_secret.get_secret_value()
            if self.s.telegram_webhook_secret
            else None
        )
        if not url:
            raise RuntimeError("TELEGRAM_WEBHOOK_URL is required for webhook mode")
        await self.bot.set_webhook(url, secret_token=secret, drop_pending_updates=True)
        logger.info("telegram: webhook set to {}", url)
        await asyncio.Event().wait()


# ============================================================================
# Helpers used by notifier_router (kept for backward compat)
# ============================================================================
def render_event_text(
    slug: str,
    station_icao: str,
    station_name: str,
    ev: dict[str, Any],
    buckets: list[Bucket],
    units: str = "celsius",
) -> str:
    payload = ev.get("payload", {})
    return ui.fmt_event_alert(
        station=station_icao,
        name=station_name,
        t_c=payload.get("cur") or payload.get("obs", {}).get("temperature_c", 0.0),
        delta=payload.get("delta"),
        daily_max=payload.get("daily_max_info"),
        bucket_title=payload.get("title"),
        price=payload.get("price"),
        p_model=payload.get("p_model"),
        edge=payload.get("edge"),
        time_to_close=None,
        lag_s=None,
        src=payload.get("src"),
        severity=ev["severity"],
        units=units,
    )


def render_resolution_text(report: ResolutionReport) -> str:
    return ui.fmt_resolution(report)
