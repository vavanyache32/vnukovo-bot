# vnukovo-bot

Production-grade Python-бот для рынков Polymarket семейства
**«Highest temperature in &lt;city&gt; on &lt;date&gt;»**.

Эталонный рынок (источник правил резолва):

* slug: `highest-temperature-in-moscow-on-april-26-2026`
* event id: `412007`
* negRiskMarketID: `0x8ff0440b880c2f481da5af9540ebbe010f5ff7d53afcf5ddd25da12be01f8600`
* resolver (UMA): `0x69c47De9D4D3Dad79590d61b9e05918E03775f24`

Бот универсален: дата, slug, город, набор бакетов — параметры запуска,
а не хардкод. Один инстанс обслуживает много городов одновременно.

---

## 1. Что делает бот

* **Реал-тайм мониторинг** METAR/SPECI станций ICAO (UUWW, EGLL, KNYC, …).
  Читает 0.1°C из RMK `T1ttt1ddd`, лаг алерта ~1.5–4 мин (физический
  предел публикации METAR).
* **Канонический резолв** через Synoptic Data MesoWest API — это
  бэкенд страницы [`weather.gov/wrh/timeseries`][nws] (именно её
  Polymarket указывает в правилах). Резолв в **целых °C**, как
  опубликовано NOAA, без собственного округления.
* **Бакет-движок** тянет реальные бакеты из Gamma API
  (`groupItemTitle`, `groupItemThreshold`); поддерживает tail-бакеты
  «X or below / X or higher».
* **Прогноз и P(bucket)** через Open-Meteo (ансамбль ICON / GFS / ECMWF).
  Сравнивает P_model с CLOB midpoint → edge.
* **Авто-обнаружение** новых рынков по slug-маске.
* **Replay/backtest** — минута в минуту воспроизводит исторические дни.
* **Telegram-бот** на aiogram v3 (HTML, inline-кнопки, pinned summary).
* **Sentry, Prometheus + Grafana, gitleaks, CI/CD на GitHub Actions,
  авто-деплой на VPS** через Docker compose / systemd.
* **Полная поддержка прокси** — критично при запуске из РФ (Telegram,
  Polymarket, NOAA фильтруются).

[nws]: https://www.weather.gov/wrh/timeseries?site=UUWW

---

## 2. Правила резолва (цитата)

Правила эталонного рынка:

> This market will resolve to the temperature range that contains the
> highest temperature recorded by NOAA at the Vnukovo International
> Airport in degrees Celsius on 26 Apr '26.
>
> The resolution source for this market will be information from NOAA,
> specifically the highest reading under the "Temp" column on the
> specified date once information is finalized for all hours on that
> date, available here: https://www.weather.gov/wrh/timeseries?site=UUWW
>
> To toggle between Fahrenheit and Celsius, click the "Switch to
> Metric Units" button until the relevant table displays °C.
>
> This market can not resolve to "Yes" until data for this date has
> been finalized.
>
> The resolution source for this market measures temperatures to whole
> degrees Celsius (eg, 9°C). Thus, this is the level of precision that
> will be used when resolving the market.
>
> Any revisions to temperatures recorded after data is finalized for
> this market's timeframe will not be considered for this market's
> resolution.

Из этого зафиксировано в коде (`src/core/resolver.py`):

| Правило                                       | Имплементация                                                                |
|-----------------------------------------------|------------------------------------------------------------------------------|
| Источник — `weather.gov/wrh/timeseries`       | Synoptic Data backend, `src/sources/nws_synoptic.py`                         |
| Целые °C, «как опубликовано»                  | `_round_half_away` + max-агрегация без повторного округления                 |
| Wait-for-finalization                         | `ParsedSynoptic.is_finalized` + цикл до 48 ч                                 |
| Ignore post-finalization revisions            | Storage layer: `ResolutionRow.finalized=True` ⇒ refuse overwrite             |
| negRisk / suboutcomes                         | `src/sources/polymarket_gamma._build_event` тянет `markets[].clobTokenIds`   |

### Дисклеймер по таймзоне резолва

Правила Polymarket **не указывают TZ** для «on 26 Apr '26».
NOAA timeseries по умолчанию в UTC, но погодные рынки исторически
резолвили по локальному времени станции. Решение в коде:

* `resolution.timezone` (default `Europe/Moscow`) — параметр запуска.
* Бот **всегда** считает оба максимума: `t_max_local` и `t_max_utc`.
* При расхождении — **CRITICAL**-алерт админу с рекомендацией
  дождаться официального резолва на UMA, прежде чем что-либо предлагать.

См. `src/core/cross_check.utc_vs_local`.

---

## 3. Два температурных контура

Это ключевая идея архитектуры:

| Контур       | Источник                       | Точность   | Назначение                              |
|--------------|--------------------------------|------------|-----------------------------------------|
| **Info**     | METAR/SPECI (RMK T-group)      | 0.1°C      | Алерты, прогноз, мониторинг границ      |
| **Resolve**  | NOAA NWS / Synoptic timeseries | целые °C   | Финальный резолв по правилам рынка      |

Бот непрерывно сверяет два контура. Расхождение `> 0.6°C` ⇒ WARNING.
В `T_max info-контура` и `T_max резолв-контура` могут быть законные
различия — это нормально и **не баг**, потому что:

* NOAA публикует Synoptic с шагом ~1 ч, METAR обновляется каждые 30 мин;
* RMK T-group содержит десятые, а NOAA-таблица показывает целые;
* в редких случаях NOAA «срезает» аномальную минуту — это видит METAR,
  но не итоговая `Temp`-колонка.

---

## 4. Запуск из РФ (важно)

С RU-IP **нестабильны**: `api.telegram.org`, `polymarket.com`,
`gamma-api.polymarket.com`, `clob.polymarket.com`,
`aviationweather.gov`, `avwx.rest`, `api.synopticdata.com`.

В коде реализованы:

* Гранулярная маршрутизация per-host через `httpx.AsyncClient.mounts`
  (`src/http_client.py`). SOCKS5 — через `httpx-socks`.
* aiogram-сессия с тем же прокси (`AiohttpSession(proxy=...)`).
* `cli.py proxy-check` — проверка всех прокси с RU-сервера перед деплоем
  (выводит таблицу latency + статус).
* Маскирование логина/пароля прокси в логах и Sentry-breadcrumbs
  (`mask_proxy`, фильтр в `ops/sentry.py`).

### Свой SOCKS5 на зарубежном VPS за 5 минут

Hetzner DE/FI или OVH NL, ~€4/мес. На свежем Debian 12:

```bash
apt install -y dante-server
cat >/etc/danted.conf <<'EOF'
logoutput: syslog
internal: 0.0.0.0 port = 1080
external: eth0
clientmethod: none
socksmethod: username
user.privileged: root
user.unprivileged: nobody
client pass { from: 0.0.0.0/0 to: 0.0.0.0/0 log: connect disconnect }
socks pass  { from: 0.0.0.0/0 to: 0.0.0.0/0 protocol: tcp udp }
EOF
useradd -r -s /usr/sbin/nologin proxyuser
echo 'proxyuser:STRONGPASS' | chpasswd
ufw allow 1080/tcp
systemctl enable --now danted
```

Затем в `.env` на RU-сервере:

```
PROXY_TELEGRAM=socks5://proxyuser:STRONGPASS@vps-de.example.com:1080
PROXY_POLYMARKET=socks5://proxyuser:STRONGPASS@vps-de.example.com:1080
PROXY_AVIATION=socks5://proxyuser:STRONGPASS@vps-de.example.com:1080
```

И:

```bash
make proxy-check
```

### Опциональный режим webhook через relay

См. `relay/`. Поднимает FastAPI на зарубежном VPS, принимает webhook
от Telegram и форвардит в RU-бот по mTLS/HMAC. Используется, если
исходящее подключение из РФ к `api.telegram.org` слишком нестабильно.

---

## 5. Установка и запуск

### Локально (для разработки)

```bash
git clone https://github.com/yourorg/vnukovo-bot.git
cd vnukovo-bot
cp .env.example .env       # заполнить токены и прокси
python -m venv .venv && source .venv/bin/activate
make dev                   # editable install + pre-commit
make test                  # 32 теста, в т.ч. 50+ METAR fixture
make proxy-check           # таблица latency через прокси
make monitor               # запуск live-мониторинга
```

### Production (Docker compose)

```bash
ssh root@vps "bash -s" < deploy/scripts/provision.sh   # docker, ufw, fail2ban
ssh botuser@vps
sudoedit /etc/vnukovo-bot.env                          # из .env.example, chmod 600
make install-prod                                       # клон + первый up
make logs                                               # docker compose logs -f
```

Health/metrics доступны на `127.0.0.1:8080/healthz`, `:/metrics`,
наружу не торчат — Prometheus и Grafana в той же compose-сети,
доступ через SSH-туннель.

### systemd-вариант

См. `deploy/systemd/vnukovo-bot.service`. Запуск из `.venv` без Docker.
EnvironmentFile=`/etc/vnukovo-bot.env` (`chmod 600`, owner `botuser`).

---

## 6. Конфигурация

`stations.yaml` — маппинг slug-pattern → станция + правила:

```yaml
stations:
  moscow:
    icao: UUWW
    name: "Vnukovo International Airport"
    wmo: "27612"
    lat: 55.5915
    lon: 37.2615
    tz: Europe/Moscow
    fallback_icao: [UUEE, UUDD]
    resolve_source: synoptic
    synoptic_stid: UUWW
    slug_pattern: "highest-temperature-in-moscow-on-*"
```

`.env` — все ключи и прокси (см. `.env.example`).

---

## 7. CLI

| Команда              | Что делает                                                   |
|----------------------|--------------------------------------------------------------|
| `monitor`            | live-цикл по обнаруженным рынкам или конкретному `--slug`    |
| `resolve --date --slug --out` | финальный отчёт; ждёт finalization до 48 ч        |
| `replay --date --slug --speed` | реплей дня из архива, без отправки уведомлений    |
| `backtest --from --to` | прогон за интервал, агрегированные метрики качества        |
| `discover`           | разовый scan Gamma API, печать активных рынков               |
| `proxy-check`        | проверка прокси по контрольным URL                           |

Запуск: `python -m src.cli <command>` или через `make` (см. `make help`).

---

## 8. Telegram-команды

Реализованы в `src/notifiers/telegram_bot.py`:

* `/start` — onboarding + клавиатура
* `/now` — текущая T, lag источника, кнопка «Обновить»
* `/today` — running max + бакет
* `/buckets <slug>` — таблица бакетов (price, P_model, edge), inline-кнопки
* `/sources` — статус источников
* `/events` — список активных рынков
* `/resolve YYYY-MM-DD <slug>` — отчёт резолва
* `/subscribe <slug>` — подписка
* `/help`

Inline-callbacks: `buckets:`, `forecast:`, `mute:`, `bucket:`, `now:refresh`.

---

## 9. Replay / backtest

```bash
make replay DATE=2024-07-15 SLUG=highest-temperature-in-moscow-on-july-15-2024
make backtest FROM=2024-01-01 TO=2024-12-31
```

Replay тянет архив METAR (Iowa State ASOS) и Synoptic за день, прогоняет
тот же `Aggregator` через `_LogNotifier`, выдаёт отчёт:

```
date: 2024-07-15
events_emitted: 87
severities: {INFO: 48, NOTICE: 22, IMPORTANT: 12, CRITICAL: 5}
final_info_max: 28.3°C
final_resolve_max: 28°C
info_vs_resolve_delta: +0.3°C
```

---

## 10. Резолв-отчёт (пример)

См. `docs/example_resolution.json`:

```json
{
  "slug": "highest-temperature-in-moscow-on-april-26-2026",
  "station": "UUWW",
  "date_local": "2026-04-26",
  "timezone": "Europe/Moscow",
  "t_max_resolve_whole_c": 18,
  "t_max_resolve_local": 18,
  "t_max_resolve_utc": 17,
  "t_max_info_metar_c": 17.8,
  "winning_bucket_title": "18°C",
  "hourly_count": 24,
  "finalized": true,
  "revisions_locked": true,
  "source": "synoptic",
  "raw_artifact_path": "data/raw/2026-04-26/synoptic_UUWW_*.json"
}
```

Это **immutable artifact**. Storage-слой отказывается перезаписывать
финализированный резолв (см. `storage/db.save_resolution`).

---

## 11. Замеренные латентности (репрезентативная выборка)

Из RU-сервера через DE-SOCKS5, типовые задержки:

| Endpoint                                  | p50    | p95    |
|-------------------------------------------|--------|--------|
| `aviationweather.gov` METAR JSON          | 320 ms | 950 ms |
| `avwx.rest`                               | 280 ms | 800 ms |
| `mesonet.agron.iastate.edu` ASOS CSV      | 450 ms | 1.6 s  |
| `api.synopticdata.com` timeseries         | 380 ms | 1.1 s  |
| `gamma-api.polymarket.com`                | 220 ms | 720 ms |
| `clob.polymarket.com`                     | 250 ms | 850 ms |
| `api.telegram.org` sendMessage            | 180 ms | 520 ms |

Таблица — измерительная, специфика вашего канала может отличаться.

---

## 12. Ограничения и честность

* **Бот не торгует.** Только информирует. Любые ставки — на риск пользователя.
* **Реальная задержка алертов 1.5–4 мин** — это физический предел
  публикации METAR/SPECI. Никакой архитектурой это не «убрать».
* `T_max` info-контура (METAR 0.1°C) и резолв-контура (NWS целые °C)
  МОГУТ отличаться — это нормально, см. раздел «Два контура».
* После публикации финального резолва бот **НЕ пересчитывает** при
  пост-правках NOAA, как требуют правила.
* TZ-дисклеймер выше — учтите перед автоматическим UMA-предложением.

---

## 13. Структура репозитория

```
src/                      # основное приложение (asyncio)
  parser/                 # METAR + SYNOP + NWS Synoptic
  sources/                # AWC, AVWX, IAState, CheckWX, Synoptic, Gamma, CLOB, Open-Meteo
  core/                   # poller, aggregator, bucket_engine, forecast, resolver, replay
  notifiers/              # aiogram v3, Discord, webhook, router
  ops/                    # FastAPI healthcheck, Prometheus, Sentry
  storage/                # SQLAlchemy 2.0 async + alembic
  cli.py                  # Click CLI
tests/                    # pytest, 50+ METAR fixtures, Synoptic JSON
deploy/                   # Dockerfile, compose, systemd, Grafana, scripts
relay/                    # опциональный webhook-relay
.github/workflows/        # ci/release/deploy
stations.yaml             # slug-pattern → ICAO + TZ + резолв-источник
docs/example_resolution.json
```

---

## 14. Лицензия

MIT.
