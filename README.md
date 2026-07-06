# claude-monitor

로컬 파일 + Claude 설정을 **독립 스냅샷(커스텀 CAS)** 으로 추적하고, 변경을 감지/diff 하며,
Claude 설정을 **MCP App(임베디드 HTML)** 카드로 조회하고, 파일 클릭 시 **git-history 스타일 diff** 를
보여주는 하이브리드 도구. Claude에게 지시해 설정 파일(권한/hook/skill)을 안전하게 편집할 수 있다.

## 런타임 모델 (하이브리드 C)

```
[로컬 watcher]  파일 변경 감지 → cas 스냅샷 적재   (결정론적, 상시)
      │
      ▼
[src/cas.py]  CAS 스냅샷/이력/diff  ← 데이터 진실원
[src/claude_config.py]  설정 introspection(7 카테고리)
[src/config_edit.py]  안전 편집(스냅샷-선행 + .bak + atomic)
      │  (child_process shell-out)
      ▼
[MCP 서버 (TS)]  도구 + UI 리소스 등록  ← src/server.ts(http) / src/server-stdio.ts
      │
      ▼
[MCP App UI]  src/dashboard.html + src/mcp-app.ts  (카드 + 파일 클릭→diff history)
      ▲
[Claude]  자연어 지시 → 편집 도구 호출 / updateModelContext 로 "지금 보는 대상" 인지
```

핵심: **watch 는 OS 레벨 watcher 에서만** 나온다(MCP/Claude 는 폴링뿐). 그래서 watcher 가 감지·적재를
담당하고, MCP App 은 그 결과를 조회·diff·편집하는 인터랙션 면을 담당한다.

## 파일

| 파일 | 역할 |
|---|---|
| `src/cas.py` | CAS 스냅샷 엔진. track/status/snapshot/log/**history**/**diff**/show |
| `src/claude_config.py` | 설정 introspection → `dump`(JSON) / `report`(카드 HTML) |
| `src/config_edit.py` | 안전 편집: perm-add/remove, hook-add/remove, skill-scaffold |
| `src/watcher.ps1` | FileSystemWatcher → 변경 시 자동 snapshot |
| `src/mcp-tools.ts` | MCP 도구 15종 + UI 리소스 등록 (Python shell-out 래퍼) |
| `src/server.ts` / `src/server-stdio.ts` | HTTP(3002) / stdio 엔트리 (**stdio 가 정본**) |
| `src/dashboard.html` + `src/mcp-app.ts` | MCP App UI (카드 + diff history + 인라인 편집/복원) |
| `tests/test_smoke.py` | 의존성 0 스모크 테스트 (unittest; pytest 로도 실행) |
| `scripts/claude-snap.ps1/.bat` | Python CLI 래퍼 |

## MCP 도구

읽기: `get_config`, `get_tracked`, `get_file_history`, `get_diff`, `snapshot_now`, `watcher_status`
편집(destructive, 스냅샷-선행 + .bak): `config_perm_add/remove`, `config_hook_add/remove`, `skill_scaffold`, `config_restore`(스냅샷 버전으로 파일 복원)
watcher 제어: `watcher_start`, `watcher_stop`
브라우저 열기: `open_report`(데이터 인라인 정적 HTML), `open_in_browser`(라이브 대시보드, HTTP 서버 자동 기동)
UI: `show_config_monitor` (대시보드 오픈)

UI 인터랙션: 설정 카드의 권한/hook 은 인라인 칩으로 추가/제거(확인 후), 파일 이력에서 임의
리비전 클릭 → 비교 대상(작업본/다른 스냅샷) 선택 diff, 각 리비전에서 "복원". 헤더의 watcher
배지로 상주 여부 확인 + 시작/중지.

## 설치 & 실행

```powershell
# 0) Python 3 + Node 18+ 필요

# 1) 스냅샷 저장소 초기화 + 추적 경로 등록 (당신 경로로)
python .\src\cas.py init
python .\src\cas.py track "$env:USERPROFILE\.claude\settings.json"
python .\src\cas.py track "$env:USERPROFILE\.claude.json"
python .\src\cas.py track "$env:APPDATA\Claude\claude_desktop_config.json"
python .\src\cas.py snapshot -m baseline

# 2) MCP App 빌드 (UI 단일파일 번들)
npm install
npm run build          # src/dashboard.html → src/dist/dashboard.html

# 3) MCP 서버: stdio 가 정본 — 호스트가 프로세스를 spawn 한다(상시 실행/포트 불필요).
#    .mcp.json 에 command="npx", args=["tsx","src/server-stdio.ts"] 형태로 등록.
#    (HTTP 가 필요하면 npm run serve 로 3002 포트 기동도 가능)

# 4) 자동 감지 watcher 상주 — UI 헤더의 "watcher 시작" 버튼(watcher_start 도구)으로 기동 권장.
#    수동 실행도 가능: powershell -ExecutionPolicy Bypass -File .\src\watcher.ps1
```

> 커넥터 등록은 `mcp.json.example`(stdio 정본 + http 대안)을 참고해 `.mcp.json` 으로 복사.

> **인코딩 주의**: Python 스크립트는 stdout 을 UTF-8 로 강제(스크립트 내 reconfigure + 서버가
> `PYTHONUTF8=1` 주입). Windows cp949 콘솔에서 한글/`—` 출력 시 죽던 문제를 막는다.

대시보드 열기: Claude 에게 "show config monitor" 또는 `show_config_monitor` 도구 호출.
파일 카드를 클릭하면 우측에 리비전 타임라인 → 리비전 선택 시 unified diff(+/- 색상).

## 변경 탐지 원리 (git index 모델)

stat fast-path(size+mtime) → 불일치 시 내용 해시 비교 → index 에 없으면 신규/없어지면 삭제.
스냅샷은 CAS(`objects/<hash>`)에 blob 으로 저장되어 과거 임의 리비전 diff 가 가능.

## 안전장치 (편집)

`src/config_edit.py` 의 모든 쓰기는 (1) 편집 전 cas 스냅샷 시도(롤백 지점), (2) 타임스탬프 `.bak` 백업,
(3) tmp 작성 후 JSON 파싱 검증 통과 시에만 `os.replace` 원자적 교체.

> **편집 대상은 먼저 track 하라.** 스냅샷-선행 롤백 지점은 그 파일이 cas 에 추적 중일 때만 의미가
> 있다. `settings.json` 을 편집할 거면 `src/cas.py track <settings.json>` 이 선행돼야 `config_restore`
> 로 되돌릴 수 있다(미추적이면 `.bak` 만 남는다).

## 테스트

```powershell
python -m unittest tests.test_smoke -v     # 의존성 0
# 또는 pytest 설치 시: python -m pytest tests/test_smoke.py
```

cas round-trip(track/snapshot/diff/history/restore), config_edit(perm/hook add·remove),
watcher-status(BOM + tz-aware heartbeat 견딤), claude_config dump(cp949 회귀 가드)를 검증한다.

## 한계 / 주의

- watcher 는 창이 떠 있는 동안만 동작(폴링 빈틈 존재). 실시간 push 아님.
- `.claude.json` 은 관심 키만 추출 + 민감키 마스킹(전체 추적 비권장: 토큰/노이즈).
- Desktop 스킬은 서버 권위 — 로컬 manifest 는 동기화 캐시(`synced` 뱃지).
- MCP App UI 렌더는 호스트(Claude Desktop/web) 환경에서 검증 필요. 개발 중에는
  ext-apps `examples/basic-host` 로 빠르게 확인 가능.
- 설정 디렉토리는 Cowork 샌드박스에선 보호되어 접근 불가 → 이 도구는 **당신 PC 셸에서 직접** 실행.
