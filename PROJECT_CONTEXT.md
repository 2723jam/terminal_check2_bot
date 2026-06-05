# terminal_check2_bot Project Context

## Project

- Local path: `C:\Users\jsoh\Documents\Codex\2026-05-29\python-telegram-bot-terminal-check2-bot\port-alert-bot`
- GitHub repository: https://github.com/2723jam/terminal_check2_bot
- GitHub Actions workflow: https://github.com/2723jam/terminal_check2_bot/actions/workflows/terminal_check2_bot.yml
- Bot name: `terminal_check2_bot`

## Operating Mode

This project monitors port and terminal operation disruption notices and sends Korean Telegram alerts.

The original strict alert rule remains:

- Do not infer terminal suspension from weather forecast alone.
- Send operation suspension/planned suspension alerts only when official or semi-official port, terminal, MSA, port authority, or operator notices explicitly mention suspension, closure, berth/gate/yard/vessel operation suspension, navigation ban, military exercise, or similar terms.
- If source is unclear, do not alert as a suspension.

Weather-based concern alerts are now a separate advisory class:

- Header: `[기상 작업속도 우려 - 실제 중단 공지 아님]`
- Purpose: warn about possible port work-speed impact from forecast weather.
- This must not be formatted or treated as an official suspension event.

## Target Ports

- Korea: `INCHEON`, `BUSAN`, `GWANGYANG`
- China: `SHEKOU`, `QINGDAO`, `SHANGHAI`, `NINGBO`, `TIANJIN`
- Vietnam: `HOCHIMINH`, `HAIPHONG`

## GitHub Actions

Secrets already entered by the user:

- `TERMINAL_CHECK2_TELEGRAM_BOT_TOKEN`
- `TERMINAL_CHECK2_TELEGRAM_CHAT_ID`

Important note:

- The configured `TERMINAL_CHECK2_TELEGRAM_CHAT_ID` returned `Telegram API error: HTTP 400 Bad Request: chat not found` during a test.
- A fallback was added so the bot can send to the latest chat found through Telegram `getUpdates`.
- The historical Qingdao test alert successfully sent through this fallback.

Actions links:

- Main workflow: https://github.com/2723jam/terminal_check2_bot/actions/workflows/terminal_check2_bot.yml
- Successful historical Qingdao test run: https://github.com/2723jam/terminal_check2_bot/actions/runs/26614161300
- Latest checked main workflow after weather-risk change: https://github.com/2723jam/terminal_check2_bot/actions/runs/26614449224

## Schedule

- Timezone: `Asia/Seoul`
- Runs daily at `09:05` through `18:05`, hourly.
- GitHub primary cron: `5 0-9 * * *`
- GitHub watchdog cron: `20,35,50 0-9 * * *`
- `scripts.run_once` stores `last_scheduled_slot` so GitHub watchdog retries can fill a missed hour without running the same Asia/Seoul hourly slot more than once after a successful check.
- Manual run is enabled by `workflow_dispatch`.

## Current Features

- Telegram commands for local/polling mode:
  - `/check`
  - `/status`
  - `/ports`
- GitHub Actions scheduled execution.
- JSON state store with last run, success/failure, last message hash, recent events, recent weather risks, source failures, and last successful scheduled slot.
- Telegram message split at 4096 characters.
- Duplicate suppression by message hash when `SEND_UNCHANGED_ALERTS=false`.
- Empty report controlled by `SEND_EMPTY_REPORT`.
- Weather concern alerts controlled by:
  - `WEATHER_RISK_ENABLED`
  - `WEATHER_RISK_HOURS`

## Weather Risk Advisory

Forecast source:

- Open-Meteo Forecast API: https://open-meteo.com/en/docs

Default thresholds:

- Heavy rain: hourly precipitation >= 10mm or 6-hour precipitation >= 25mm
- Strong wind: gust >= 17m/s
- Marine bad weather: gust >= 21m/s or thunderstorm weather code
- Fog: weather code 45 or 48
- Snow: snowfall >= 2mm/hour

Example advisory:

```text
[기상 작업속도 우려 - 실제 중단 공지 아님]

※ QINGDAO
우려사유 : 폭우
예상기간 : 26.05.29 13:00 ~ 26.05.29 19:00

참고 : 공식 작업 중단 공지가 아닌 기상 기반 주의 알림입니다.
```

## Recent Test Status

Local tests:

```text
9 passed
```

The latest main GitHub Actions run after adding schedule watchdog coverage and failure visibility completed successfully.

## Known TODO

- Verify or replace remaining `TODO:` source URLs in `config/ports.yaml`.
- Correct `TERMINAL_CHECK2_TELEGRAM_CHAT_ID` in GitHub Secrets when the exact target chat id is known; fallback works but exact secret is cleaner.
- Add source-specific parsers for official notice pages that block simple HTTP or require dynamic rendering.
- Consider adding observed/historical rainfall analysis if the user wants retrospective monthly delay-risk summaries, not only forward-looking forecast alerts.
