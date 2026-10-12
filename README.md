# Bstom

혼자서도 다른 사람들과 같은 테이블에서 자유롭게 수다를 나누는 느낌을 실험하는 **자율 다중 에이전트 대화 MVP**입니다. 좋은 아이디어를 대신 완성하거나 찬반 토론을 진행하는 것이 아니라, 사용자가 자기 생각을 떠올릴 여백을 만드는 것이 목표입니다.

나루·솔·미오가 각자의 비동기 루프, 기억, 관심사와 발언 의지를 가지고 대화를 관찰합니다. 사용자는 넓은 주제를 던진 뒤 지켜보거나, 언제든 질문·농담·의견으로 끼어들 수 있습니다.

## 빠른 실행

Python **3.11 이상**이 필요합니다. 기본 설정은 API를 사용하지 않는 Mock 모드입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe start.py
```

Windows에서는 `run.ps1`로 시작할 수도 있습니다. 브라우저가 자동으로 열리며 기본 주소는 `http://127.0.0.1:8878`입니다. 포트가 사용 중이면 8879~8899 중 사용 가능한 포트를 고릅니다. 종료는 실행 터미널에서 `Ctrl+C`입니다.

macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python start.py
```

브라우저 자동 열기 없이 고정 포트로 실행하려면:

```powershell
.\.venv\Scripts\python.exe -m uvicorn bstom.app:app --host 127.0.0.1 --port 8878
```

## 실제 AI 연결: Gemini 무료 등급

[Google AI Studio API Keys](https://aistudio.google.com/api-keys)에서 Bstom 전용 키를 발급하고 프로젝트가 **무료 등급**인지 확인하세요. 결제 연결이나 유료 업그레이드는 필요하지 않습니다. 기본 모델은 `gemini-3.5-flash-lite`이며 무료 제공 모델과 할당량은 바뀔 수 있습니다. [공식 요금표](https://ai.google.dev/gemini-api/docs/pricing), [API 키 안내](https://ai.google.dev/gemini-api/docs/api-key)

프로젝트 최상위에 `.env.local`을 만들고 다음 값을 설정한 뒤 서버를 다시 시작합니다. 키는 채팅, 스크린샷, GitHub에 넣지 마세요.

```dotenv
BSTOM_MODE=compatible
LLM_PROVIDER=gemini
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
LLM_MODEL=gemini-3.5-flash-lite
GEMINI_API_KEY=여기에_본인_키를_로컬에서만_입력
```

화면에서 `실제 AI · Gemini`를 선택합니다. Gemini의 네이티브 REST API로 JSON 판단과 SSE 발언 스트림을 처리합니다. 키는 서버의 요청 헤더에만 넣으며 브라우저에 전달하지 않습니다. `.env.local`, `.env`, 대화 기록과 로그는 Git에서 제외됩니다. 공개 코드를 받는 사람은 자신의 키가 필요합니다.

무료 한도를 넘으면 대기하거나 공급자 오류가 발생할 수 있으며 앱은 결제·업그레이드를 자동으로 하지 않습니다. 이미 유료 등급인 프로젝트에서는 공급자의 사용료가 발생할 수 있으므로 프로젝트 등급을 확인하세요.

OpenAI 호환 공급자도 선택할 수 있습니다. 기존 Groq 설정을 사용하려면 다음처럼 설정합니다. Gemini와 호환 API의 키는 분리되어 사용됩니다.

```dotenv
BSTOM_MODE=compatible
LLM_PROVIDER=compatible
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_API_KEY=여기에_본인_키를_로컬에서만_입력
```

`compatible` 세션 모드는 실제 AI를 뜻하는 기존 인터페이스 이름입니다. 실제 공급자는 `LLM_PROVIDER`가 선택합니다. 공급자별 모델·파라미터 지원 여부를 확인해야 합니다.

## 사용법

1. `생명과학으로 뭔가 재밌는 걸 해보고 싶어`처럼 넓은 주제를 입력하고 테이블을 엽니다.
2. 세 에이전트의 상태와 실시간 대화를 봅니다. 같은 순서로 돌아가며 발언하지 않습니다.
3. 아래 입력창으로 언제든 끼어듭니다. 생성 중이거나 표시를 기다리는 AI 발언은 취소되고, 이미 화면에 나온 채팅과 사용자 발언이 다음 판단에 포함됩니다. 생성됐지만 아직 보이지 않은 내용은 버립니다.
4. `잠깐 쉬기` / `다시 이야기하기` / `대화 마치기`로 제어합니다. 쉬는 동안 메시지를 남기는 것도 가능합니다.
5. `기록 저장`으로 JSON을 내려받거나, `이전 테이블`에서 기록을 다시 엽니다.

기본 설정에서는 분당 요청·토큰 한도를 예상해 기다리지 않고 바로 API를 호출합니다. 공급자가 실제로 429 응답을 보낸 경우에만 재시도 대기를 적용합니다. 이때도 사용자 메시지와 제어 버튼은 동작합니다. 연결이 끊어지면 WebSocket을 재연결하고 전체 스냅샷으로 누락된 발언을 복구합니다. 브라우저를 닫는 것만으로는 세션이 종료되지 않으므로, 필요하면 `대화 마치기`를 누르세요. 발언·시간 한도 도달 시에는 자동 종료하며, 호출 예산은 직접 설정한 경우에만 적용됩니다.

## 엔진 구조

| 파일 | 책임 |
| --- | --- |
| `bstom/models.py` | 에이전트 상태, 개인 기억, 준비 중인 생각, 메시지와 발언 요청 |
| `bstom/engine.py` | 공통 기록, 독립적인 에이전트 루프, 중재, 스트림 중단, 세션 제어 |
| `bstom/llm.py` | Mock / Gemini / OpenAI 호환 공급자, 전역 동시 호출·분당 요청·토큰 제한 |
| `bstom/pacing.py` | 응답 생성과 완성된 채팅 사이의 표시 간격을 분리 |
| `bstom/prompts.py` | 대화 스타일, 사적인 판단과 실제 발언 분리, 유한한 문맥 구성 |
| `bstom/app.py` | REST, WebSocket, 로컬 기록 조회 |
| `static/` | 엔진과 분리된 단순한 한국어 웹 디버그 화면 |

에이전트는 새 문맥이나 재평가 시점에 각자 `desire`, `relevance`, `novelty`, `interrupt`, `thought`, `interest`, `memory`, `kind`를 판단합니다. 발언 의지가 낮으면 듣습니다. 준비 중인 생각은 개인 상태이며, 실제 대화 기록에 이미 발언한 것처럼 넣지 않습니다.

오케스트레이터는 요청을 짧게 모아 `0.5 × 의지 + 0.3 × 관련성 + 0.2 × 새로움`에서 최근 발언 빈도·연속 발언·낡은 문맥의 감점을 적용합니다. 동점은 요청 시각으로 처리합니다. 고정 라운드나 동등 발언 횟수를 강제하지 않습니다.

현재 화면은 완성된 채팅을 한 번에 보여줍니다. 에이전트들은 독립적으로 발언을 요청하고 중재 점수에 따라 다음 발언자가 정해집니다. 사용자 발언은 즉시 기록하며 생성 중이거나 표시를 기다리는 AI 발언과 오래된 요청을 취소합니다. 이미 표시된 채팅은 보존합니다.

대화에 별도의 정적 모드나 정적 연출을 넣지 않습니다. 발언 요청이 없으면 독립적인 에이전트 루프가 문맥 변화 또는 안전한 재평가 주기에 따라 다시 판단합니다. 새 요청이 생기면 바로 중재 대상이 됩니다. 실제 발언 의지가 낮을 때나 API 사용량 제한으로 기다릴 수는 있습니다.

각 채팅은 생성이 끝나면 전체 내용을 한 번에 표시합니다. 첫 채팅에는 별도의 표시 지연이 없고, 이후 채팅 사이에는 기본 1초에 이전 글 길이 ÷ 200초를 더해 최대 1.5초의 읽을 시간을 둡니다. 이 시간에도 다음 채팅 생성은 진행하므로 API 응답 시간과 읽는 시간이 겹칩니다. 응답이 늦으면 추가로 같은 간격을 기다리지 않습니다. 사용자 입력 직후에도 같은 읽기 간격을 적용하되 사용자 메시지 자체는 즉시 표시합니다. 생성된 말은 최대 발언 길이 안에서 임시 버퍼에만 보관하며, 표시한 내용만 공유 문맥과 기록에 포함합니다. 다음 발언을 생성하거나 표시를 기다리는 에이전트는 이름과 함께 `입력 중…`으로 표시합니다. 입력 표시는 표시 완료·취소·일시정지·오류 시 해제되고 재접속 시 스냅샷에서 복원됩니다. 빈 말풍선과 `말하다 멈춤` 배지는 표시하지 않고, 취소 정보는 디버그 이벤트에 유지합니다.

최근 문맥, 초기 발언, 에이전트별 개인 기억을 함께 전달해 연상과 재언급을 지원합니다. 생성 문맥은 최근 5개 발언 각각 최대 150자, 초기 단서와 짧은 개인 기억으로 제한하므로 긴 대화의 세부 내용은 생략될 수 있습니다. 원본 대화 기록은 별도로 보존합니다.

## 설정과 예산

`.env.example`에 설정 목록이 있습니다. `.env`보다 `.env.local`이 우선하며 시스템 환경변수가 가장 우선합니다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `API_MAX_CONCURRENCY` | 4 | 세 에이전트 판단과 발언 생성이 동시에 진행 가능 |
| `API_REQUESTS_PER_MINUTE` | 0 | 0이면 앱의 분당 요청 대기 없음; 양수로 선택적 제한 |
| `API_TOKENS_PER_MINUTE` | 0 | 0이면 앱의 토큰 사용량 대기 없음; 양수로 선택적 제한 |
| `SESSION_MAX_CALLS` | 0 | 0이면 호출 횟수 제한 없음; 양수이면 실패 포함 호출 예산 |
| `SESSION_MAX_MESSAGES` | 40 | 사용자 발언과 AI 발언을 합친 최대 메시지 수 |
| `SESSION_MAX_SECONDS` | 900 | 일시정지 시간을 포함하는 세션 수명 |
| `MAX_SPEECH_CHARS` | 360 | 최대 AI 발언 길이 |
| `MAX_OUTPUT_TOKENS` | 512 | 각 API 요청의 최대 출력 토큰 |
| `REEVALUATE_SECONDS` | 12 | 문맥 변화가 없을 때 안전하게 다시 생각하는 주기 |
| `SPEECH_COOLDOWN` | 0 | 발언 직후 다시 판단 가능; 양수로 선택적 간격 |
| `TURN_PAUSE_SECONDS` | 1 | 채팅 사이 기본 간격; 글 길이에 따라 최대 0.5초 추가, 0이면 간격 없음 |

선택적으로 토큰 제한을 설정하면 보수적인 예약 추정치를 사용합니다. 429 응답은 공급자의 `retry-after`를 반영해 전역 재시도 대기를 적용합니다. 네트워크·JSON·스트림 오류는 해당 에이전트에 기록하고 다른 에이전트를 손상시키지 않습니다. 공급자 한도 자체를 해제하거나 결제 등급을 바꾸는 기능은 없습니다. 최대 4개 활성 세션을 허용합니다.

## 이벤트와 인터페이스

모든 엔진 이벤트에 `type`, `session_id`, `agent_id`(해당 없으면 null), `timestamp`(Unix seconds), 단조 증가 `seq`, `calls`, `data`가 있습니다.

대화 이벤트: `agent_thinking`, `agent_typing`, `agent_wants_to_speak`, `agent_speaking`, `speech_chunk`, `agent_finished`, `agent_interrupted`, `user_spoke`, `session_paused`, `session_resumed`, `session_ended`.

진단 이벤트: `session_started`, `agent_state`, `arbitration`, `api_wait`, `provider_error`, `budget_exhausted`. WebSocket 연결 시에는 `snapshot`이 먼저 전달됩니다. 느린 구독자는 1013 코드로 연결을 닫고 스냅샷을 다시 받도록 합니다.

| 인터페이스 | 용도 |
| --- | --- |
| `POST /api/sessions` | `{ "topic": "...", "mode": "mock" }` 또는 `compatible` |
| `POST /api/sessions/{id}/message` | `{ "text": "..." }` |
| `POST /api/sessions/{id}/pause` | 일시정지 |
| `POST /api/sessions/{id}/resume` | 재개 |
| `POST /api/sessions/{id}/end` | 종료 |
| `GET /api/sessions` | 현재·저장된 세션 목록 |
| `GET /api/sessions/{id}` | 대화·상태·최근 이벤트 스냅샷 |
| `WS /ws/{id}` | 스트리밍 이벤트 |

자동 API 문서는 `/docs`에서 볼 수 있습니다. 브라우저의 다른 출처에서 오는 변경 요청과 WebSocket 연결은 거부합니다. 로그인 없는 개인 로컬 실험용이며 실행 주소는 `127.0.0.1`로 유지하세요.

`data/{session_id}.jsonl`에 전체 이벤트 로그, `data/{session_id}.json`에 대화 스냅샷을 저장합니다. 서버 재시작 후 과거 기록은 읽을 수 있지만 중단된 세션을 이어서 실행하는 기능은 없습니다.

## 검증

API 키 없이 엔진과 공급자 인터페이스를 검사합니다:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

테스트는 추가 사용자 입력 없는 대화, 비고정 발언 순서, 충돌 중재, 사용자 개입 시 생성 취소와 우선 처리, 완성된 채팅 보존, 경쟁하는 에이전트의 다음 발언 선택, 낮은 발언 의지 후 재평가와 새 요청 처리, Mock의 주기적 발언 억제 제거, 연상과 초기 주제 재언급, 일시정지·재개·종료, 오류 격리, 호출·발언 길이 예산, 오래된 요청 폐기, 동시 호출 제한, 느린 구독자, 첫 채팅의 즉시 표시, 채팅 사이 간격과 생성 시간의 중첩, 채팅 전체를 한 번에 전달, REST/WebSocket, 공급자 JSON/SSE/429 처리를 검증합니다. 실제 API 호출은 자동 테스트에 포함되지 않습니다.

실제 공급자 스모크 테스트는 **명시적으로** 실행합니다. 현재 공급자 설정에 실제 요청을 보내며 무료 플랜에서도 할당량을 사용합니다:

```powershell
.\.venv\Scripts\python.exe scripts/live_smoke.py --live
```

최대 24회 호출/160초이며 실제 스트림 도중 사용자 발언을 넣고 중단·다른 에이전트의 이어 말하기를 확인합니다. 결과는 Git에서 제외된 `artifacts/live-report.json`에 저장합니다.

Mock은 대화 내용의 해시와 상태를 이용한 결정적 가짜 응답입니다. 실제 창의성·자연스러움의 증거가 아닙니다. 실제 모델에서도 사실 오류, 반복, 길게 듣는 에이전트가 생길 수 있습니다. 이 MVP는 텍스트 스트림 중단이며 음성 동시 발화, 3D 테이블, 계정, 벡터 DB는 구현하지 않습니다.
