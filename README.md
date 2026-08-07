# terminal_check2_bot

`terminal_check2_bot`는 주요 항구와 터미널의 실제 작업 중단 또는 중단 예정 공지를 수집해 Telegram으로 한국어 알림을 보내는 봇입니다. 날씨 예보와 작업속도 우려는 발송하지 않으며, 공식 또는 준공식 공지에 작업 중단, 폐쇄, 접안 중단, gate/yard/vessel operation suspended 같은 명시 근거가 있을 때만 발송합니다.

## 대상 항구

- 한국: INCHEON, BUSAN, GWANGYANG
- 중국: SHEKOU, QINGDAO, SHANGHAI, NINGBO, TIANJIN
- 베트남: HOCHIMINH, HAIPHONG

## 설치

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

## Telegram Bot 생성

1. Telegram에서 `@BotFather`에게 `/newbot`을 보내고 봇을 생성합니다.
2. 발급된 token을 `.env`의 `TELEGRAM_BOT_TOKEN`에 넣습니다.
3. 알림을 받을 개인/그룹/채널의 chat id를 `TELEGRAM_CHAT_ID`에 넣습니다.
4. 그룹이나 채널에 넣는 경우 봇을 초대하고 메시지 전송 권한을 부여합니다.

개인 채팅은 [terminal_check2_bot](https://t.me/terminal_check2_bot?start=terminal_check2_bot)을
열고 START를 한 번 누릅니다. 일반 START와 전용 payload START를 모두 자동 연결에
사용할 수 있으며, 발견한 숫자 chat id는 이 봇 전용 상태 파일에 저장됩니다.
매 실행마다 token이 `@terminal_check2_bot` 소유인지 확인하므로 다른 봇과 섞이지
않습니다. Telegram 발송 오류가 있어도 항구 수집과 이벤트 저장을 먼저 완료합니다.

## 환경변수

- `BOT_NAME`: 기본값 `terminal_check2_bot`
- `TELEGRAM_BOT_TOKEN`: Telegram Bot API token
- `TELEGRAM_CHAT_ID`: 알림 대상 chat id
- `PORTS_CONFIG`: 기본값 `config/ports.yaml`
- `KEYWORDS_CONFIG`: 기본값 `config/keywords.yaml`
- `STATE_FILE`: 기본값 `data/terminal_check2_bot_state.json`
- `TIMEZONE`: 스케줄 기준 timezone, 기본값 `Asia/Seoul`
- `SEND_EMPTY_REPORT`: true이면 이벤트가 없어도 빈 리포트 발송
- `SEND_UNCHANGED_ALERTS`: false이면 직전 발송 내용과 같을 때 생략
- `WEATHER_RISK_ENABLED`: 호환성 항목이며 현재 실행 경로에서는 `false`로 고정
- `WEATHER_RISK_HOURS`: 비활성 기상 모듈의 호환성 항목
- `HTTP_TIMEOUT_SECONDS`: 출처 fetch timeout
- `HTTP_USER_AGENT`: 공식 사이트 요청 User-Agent
- `LOG_LEVEL`: 기본값 `INFO`

## 실행

```bash
python -m src.main
```

스케줄은 `Asia/Seoul` 기준 매일 09:05부터 18:05까지 매시간 실행됩니다. 수동 명령은 다음과 같습니다.

- `/check`: 즉시 전체 항구 체크
- `/status`: 마지막 실행, 성공/실패, 최근 이벤트 수 확인
- `/ports`: 대상 항구 목록 확인

## 바로 연결하기

토큰과 chat id를 한 번에 설정하고 테스트 메시지를 보내려면:

```powershell
.\.venv\Scripts\python.exe -m scripts.telegram_setup --token "123456:ABC..." --chat-id "123456789" --test
```

chat id를 모르면 봇에게 Telegram 메시지를 하나 보낸 뒤:

```powershell
.\.venv\Scripts\python.exe -m scripts.telegram_setup --token "123456:ABC..." --discover-chat --test
```

스케줄러를 띄우기 전 실제 수집-발송 경로를 1회 확인하려면:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_once
```

Windows 로그인 시 자동 실행되게 등록하려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows_task.ps1
```

## Docker

```bash
cp .env.example .env
docker compose up --build -d
```

상태 파일은 `./data/terminal_check2_bot_state.json`에 저장됩니다. 서비스명과 컨테이너명은 다른 봇과 겹치지 않도록 `terminal_check2_bot`로 고정했습니다.

## GitHub Actions로 자동 실행

기아채널봇처럼 GitHub가 정해진 시간에 실행하게 하려면 이 저장소를 GitHub에 올리고 Repository Secrets에 Telegram 값을 넣습니다.

GitHub 저장소 화면에서:

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

필수 Secret:

- `TERMINAL_CHECK2_TELEGRAM_BOT_TOKEN`: BotFather에서 받은 Telegram bot token
- `TERMINAL_CHECK2_TELEGRAM_CHAT_ID`: 알림 받을 채팅방 id

선택 Variable:

- `TERMINAL_CHECK2_SEND_EMPTY_REPORT`: 기본값 `false`
- `TERMINAL_CHECK2_SEND_UNCHANGED_ALERTS`: 기본값 `false`
- 기상 우려 관련 Variable은 사용하지 않으며 실제 중단 공지만 발송합니다.


GitHub Actions scheduled runs use two UTC watchdog ranges: `5,20,35,50 21-23 * * *` and `5,20,35,50 0-9 * * *` (06:05-18:50 KST). Because GitHub may delay cron jobs, the bot maps the actual start time to the current 09:05-18:05 KST slot, uses 19:00-21:59 as a grace period for the final 18:05 slot, and deduplicates with `last_scheduled_slot`. Pre-window runs exit without checking. `workflow_dispatch` and push runs execute immediately.

## 알림 형식

```text
※ QINGDAO
중단사유 : 기상악화(폭우)
중단기간 : 26.05.29 13:00 ~ 26.05.30 08:00 (미정)

※ TIANJIN
중단사유 : 군사훈련
중단기간 : 26.05.30 ~ 미정
```

Telegram 제한인 4096자를 넘으면 `[1/2]`, `[2/2]` 형태로 분할 전송합니다.

## 출처 URL 추가법

`config/ports.yaml`의 각 항구 `source_urls`에 공식/준공식 URL만 추가합니다.

```yaml
source_urls:
  - "https://official.example/notice"
  - "TODO: verify terminal notice URL before enabling"
```

Verified direct feeds include Incheon SCON/ICPA, PNIT/HPNT/BPT, YGPA,
Shandong MSA, Shanghai Municipal Government, Zhejiang/Ningbo MSA, Ningbo Port
EDI, Maersk advisories, Saigon Newport ePort, and Hai Phong Port.
The Ningbo EDI adapter reads its public list/detail API and publishes the public detail URL.
Shenzhen MSA is anti-bot protected, so SHEKOU retains Maersk as a fallback.
Tianjin uses Maersk while a verified official navigational-warning URL remains TODO.
Broad home/list pages are never treated as one notice; only selected detail pages
are parsed, and HTTP 202 or request-rejected pages are recorded as source failures.

## 중단 판단 원칙

- 날씨 예보, 기상 관측, 혼잡 가능성만으로는 알림을 보내지 않습니다.
- 공지 본문에 작업 중단/폐쇄/접안 중단/gate 또는 yard 또는 vessel operation suspended 등 명시적 중단 근거가 있어야 합니다.
- 선택적 반출입 제한이나 일반 작업 제한만 있는 공지는 중단 이벤트에서 제외합니다.
- `WeatherClassifier`는 이미 확인된 중단 공지의 기상 세부 사유를 분류하는 보조 로직입니다.
- 중국은 군사훈련, 실탄사격, 항행금지, 임시 해상통제를 체크하며 항행경고 문구만으로는 발송하지 않습니다.
- 특정 항구 fetch 실패는 전체 실패로 전파하지 않고 상태 저장소에 실패 정보로 남깁니다.
- 종료 미정 공지는 최대 7일간 현재 이벤트로 유지하며, Ningbo EDI의 후속 전면 복구 공지가 나오면 즉시 해제합니다.

## 기상 우려 알림 비활성화

현재 배포는 기상 예보 기반 우려 알림을 생성하거나 발송하지 않습니다. `WeatherClassifier`는 공식 중단 공지에 명시된 태풍, 폭우, 강풍 등의 중단사유를 한국어로 분류할 때만 사용합니다.

## 한계사항

- Shenzhen MSA list/detail URLs currently return an HTTP 202 anti-bot challenge; SHEKOU retains the Maersk fallback.
- Tianjin has no verified direct MSA list yet and uses Maersk while the official source remains TODO.
- 일부 공식 사이트는 동적 렌더링, 방화벽, 403 응답, 로그인, 캡차로 인해 단순 HTTP fetch가 실패할 수 있습니다. 이런 경우 Playwright 기반 어댑터를 추가해야 합니다.
Several source-specific list/detail parsers are included, but site redesigns can still break selectors. An unsupported listing is recorded as a source failure instead of being treated as healthy.
- `config/ports.yaml`의 `TODO:` URL은 검증 전이므로 fetch 대상에서 제외됩니다.

## 테스트

```bash
pytest
```

필수 케이스:

- QINGDAO 기상악화(폭우) 메시지 포맷
- TIANJIN 군사훈련 메시지 포맷
- 중국 기상 키워드별 세부 분류
- 군사훈련/항행통제 키워드 분류
- 09:05~18:05 매시간 스케줄
- 여러 항구 이벤트를 국가/항구 순서로 1개 메시지 통합
