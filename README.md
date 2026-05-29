# terminal_check2_bot

`terminal_check2_bot`는 주요 항구와 터미널의 작업 중단 또는 중단 예정 공지를 수집해 Telegram으로 한국어 알림을 보내는 봇입니다. 날씨 예보만으로 중단을 추론하지 않고, 항만/터미널/항만공사/해사국/운영사/공식 또는 준공식 공지에 작업 중단, 작업 제한, 폐쇄, 접안 중단, gate/yard/vessel operation suspended 같은 명시 근거가 있을 때만 발송합니다.

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
- `WEATHER_RISK_ENABLED`: true이면 공식 중단 공지와 별개로 기상 기반 작업속도 우려 알림 발송
- `WEATHER_RISK_HOURS`: 기상 우려 확인 범위, 기본값 12시간
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
- `TERMINAL_CHECK2_WEATHER_RISK_ENABLED`: 기본값 `true`
- `TERMINAL_CHECK2_WEATHER_RISK_HOURS`: 기본값 `12`

워크플로우 파일은 `.github/workflows/terminal_check2_bot.yml`입니다. GitHub Actions cron은 UTC 기준이므로 `5 0-9 * * *`로 설정해 `Asia/Seoul` 09:05~18:05에 맞췄습니다. `workflow_dispatch`도 켜져 있어서 Actions 탭에서 수동 실행할 수 있습니다.

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

실제 확인하지 못한 주소는 임의로 만들지 말고 `TODO:`로 남기세요. 어댑터는 `TODO:` 항목을 fetch하지 않습니다.

## 중단 판단 원칙

- 날씨 예보, 기상 관측, 혼잡 가능성만으로는 알림을 보내지 않습니다.
- 공지 본문에 작업 중단/제한/폐쇄/접안 중단/gate 또는 yard 또는 vessel operation suspended 등 명시적 중단 근거가 있어야 합니다.
- `WeatherClassifier`는 이미 확인된 중단 공지의 기상 세부 사유를 분류하는 보조 로직입니다.
- 중국은 군사훈련, 실탄사격, 항행금지, 임시 해상통제, 항행경고 키워드를 별도로 체크합니다.
- 특정 항구 fetch 실패는 전체 실패로 전파하지 않고 상태 저장소에 실패 정보로 남깁니다.

## 기상 작업속도 우려

`WEATHER_RISK_ENABLED=true`이면 항구 좌표 기준 예보를 별도로 확인해 폭우, 강풍, 안개, 폭설, 해상악천후가 작업 속도에 영향을 줄 수 있는 시간대를 보냅니다. 이 알림은 공식 중단 공지로 처리하지 않으며 메시지 상단에 `[기상 작업속도 우려 - 실제 중단 공지 아님]`을 붙입니다.

기본 임계값:

- 폭우: 시간당 강수량 10mm 이상 또는 6시간 누적 25mm 이상
- 강풍: 순간풍속 17m/s 이상
- 해상악천후: 순간풍속 21m/s 이상 또는 뇌우 코드
- 안개: 예보 weather code 45 또는 48
- 폭설: 시간당 적설 2mm 이상

예시:

```text
[기상 작업속도 우려 - 실제 중단 공지 아님]

※ QINGDAO
우려사유 : 폭우
예상기간 : 26.05.29 13:00 ~ 26.05.29 19:00

참고 : 공식 작업 중단 공지가 아닌 기상 기반 주의 알림입니다.
```

## 한계사항

- 일부 공식 사이트는 동적 렌더링, 방화벽, 403 응답, 로그인, 캡차로 인해 단순 HTTP fetch가 실패할 수 있습니다. 이런 경우 Playwright 기반 어댑터를 추가해야 합니다.
- 현재 파서는 공지 목록/본문 HTML에서 텍스트를 추출해 키워드와 날짜를 잡는 범용 구현입니다. 운영 투입 전에는 각 출처별 목록 페이지와 상세 페이지 구조에 맞춘 전용 파서를 보강하는 것이 좋습니다.
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
