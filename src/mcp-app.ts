// src/mcp-app.ts - Config Monitor UI (iframe side). Talks to host via App bridge.
//   get_config       -> collapsible config sections (with source path)
//   get_tracked      -> tracked file rows (click -> history)
//   get_file_history -> revision timeline
//   get_diff         -> from->to unified diff
//   config_*/config_restore/watcher_* -> inline edit / restore / watcher control
//   updateModelContext -> inject what the user is viewing into Claude context
import { App } from "@modelcontextprotocol/ext-apps";

const app = new App({ name: "Config Monitor", version: "0.1.0" });

// 브라우저(standalone) 모드: server.ts 가 주입한 플래그. true 면 MCP 브리지 대신 HTTP REST 사용.
const STANDALONE = !!(window as any).__CONFIG_MONITOR_HTTP__;

const $ = (id: string) => document.getElementById(id)!;
const esc = (s: unknown) =>
  String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));

// 카드 값 종류 구분: 서술형 key 는 sans+줄클램프, 그 외(command/args/env/path/tools 등)는 mono 코드형.
const DESC_KEYS = new Set(["desc", "description", "설명", "summary"]);
const valClass = (k: string) => (DESC_KEYS.has(String(k).toLowerCase()) ? "desc" : "code");

// ----- i18n (ko/en): UI 크롬 라벨만. 파일명/경로/설정 데이터/섹션 타이틀은 번역 대상 아님. -----
const I18N: Record<string, Record<string, string>> = {
  ko: {
    newFile: "신규", modified: "수정", deleted: "삭제", unchanged: "동일",
    snapshot: "스냅샷", refresh: "새로고침", report: "리포트", openBrowser: "브라우저에서 열기",
    dispSettings: "표시 설정", accentColor: "강조 색상", showSources: "출처 경로 표시", descLines: "설명 표시 줄 수", linesSuffix: "줄",
    fullscreen: "전체화면", windowed: "창 모드",
    watcherOn: "watcher 실행 중", watcherOff: "watcher 정지",
    trackedFiles: "추적 파일", clickHint: "클릭 → 이력 / diff",
    kindGlobal: "전역", kindProject: "프로젝트",
    scopeAll: "전체", shadowed: "프로젝트에서 재정의됨", shadowedTip: "동일 이름의 프로젝트 항목이 우선 적용됨 · ",
    trackPathPlaceholder: "프로젝트 폴더 / .claude / 설정 파일 경로",
    trackAdd: "추적 추가", trackNone: "추적할 설정 파일을 찾지 못함", trackAlready: "이미 추적 중",
    projPickOpen: "＋ 프로젝트에서 추가", projPickEmpty: ".claude 있는 프로젝트 없음", projPickTracked: "추적 중",
    untrack: "추적 해제", untracked: "추적 해제됨", untrackConfirm: "해제확정",
    settings: "설정", settingsHint: "8 카테고리 · 출처 경로 포함",
    source: "출처", remove: "제거", del: "삭제", cancel: "취소", add: "추가",
    permPlaceholder: "예: Bash(npm run*)", hookPlaceholder: "hook 명령어",
    closePanel: "패널 닫기", openHistory: "이력 패널 열기", closeHistory: "이력 패널 닫기", historyDiff: "이력 / DIFF",
    compareTo: "비교 대상", working: "작업본 (현재 파일)", workingShort: "작업본",
    history: "변경 이력", restore: "복원", restoreConfirm: "복원확정", restoreTitle: "이 버전으로 복원", restoreNote: "현재 상태는 자동 스냅샷+백업",
    noDiff: "선택한 두 버전 사이에 텍스트 변경이 없습니다.",
    generatedPrefix: "추적 ", generatedMid: " · 최근 스냅샷 ",
    toastAdded: "추가됨", toastRemoved: "제거됨", toastSnapshot: "스냅샷 생성됨", snapshotMsg: "스냅샷 (dashboard)",
    toastRefreshed: "새로고침 완료", toastReport: "리포트 생성 중…", toastBrowser: "브라우저에서 열기",
    toastWatcherStart: "watcher 시작", toastWatcherStop: "watcher 중지", toastRestored: "복원됨 · 현재 상태 자동 백업",
    langTitle: "Switch to English",
    failed: "실패", loading: "불러오는 중…", fetchFail: "조회 실패", unknown: "알 수 없음",
    emptyTracked: "추적 파일 없음 - cas.py track 으로 추가", emptyCards: "항목 없음 / 파일 미발견",
    noHistory: "스냅샷 이력 없음 (먼저 스냅샷)", emptyTrackedResp: "상태를 불러오지 못했습니다 (빈 응답)",
    emptyConfigResp: "설정을 불러오지 못했습니다 (빈 응답)", trackedStatus: "추적 상태", deletedHash: "삭제됨",
    delConfirm: "삭제확정", trashMoveHint: ".trash 로 이동(복구 가능)",
    addNamePlaceholder: "name 설명…", needServerJson: "서버 JSON 필요: name {…}",
    libSectionTitle: "Library (토글 설치)", libNotInstalled: "미설치", libInstalled: "설치됨", libModified: "변경됨",
    libEmpty: "라이브러리 항목 없음", kitRef: "kit참조", done: "완료",
    libInstall: "설치", libSync: "동기화", libSyncConfirm: "덮어쓰기 확정(백업됨)", libUninstallConfirm: "제거 확정(.trash)",
    libUnregistered: "미등록", libPathPlaceholder: "라이브러리 경로 (.claude 구조 디렉토리)", libRegister: "등록", libRegistered: "라이브러리 등록됨",
    curFileTitle: "현재 파일 내용", readOnly: "읽기 전용", emptyFile: "(빈 파일)", diffFetchFail: "diff 조회 실패",
    restoring: "복원 중…", watcherErr: "watcher 상태 오류", stopped: "정지됨", ago: "전",
    displayModeFail: "디스플레이 모드 변경 불가(호스트 미지원)",
    toastReportOpened: "브라우저에서 리포트 열림", toastReportFail: "리포트 실패", toastTabOpened: "브라우저 탭 열림", toastOpenFail: "열기 실패",
    collapseAll: "전부 접기", collapseAllTitle: "펼쳐진 설정 분류를 전부 접기",
    selectFilePrompt: "파일 선택", selectFileHint: "왼쪽에서 추적 파일을 클릭하세요",
    libPaths: "라이브러리 경로", libPathRemoved: "경로 제거됨", libEnvTag: "env",
    libEnvHint: "환경변수(CLAUDE_CONFIG_LIBRARIES)로 지정되어 대시보드에서 제거 불가",
    libInstallSelected: "선택 설치", libInstallGroup: "그룹 설치", libInstallGroupConfirm: "그룹 설치 확정",
    libInstallGroupHint: "이 그룹의 미설치 스킬 전체 설치", libAllInstalled: "이미 전부 설치됨",
    installTarget: "설치 대상", targetGlobal: "전역 (~/.claude)", rootItems: "루트 항목 · 폴더 없음",
    toastGroup: "그룹 설치 완료", toastSel: "선택 설치 완료", cntUnit: "개",
  },
  en: {
    newFile: "New", modified: "Modified", deleted: "Deleted", unchanged: "Same",
    snapshot: "Snapshot", refresh: "Refresh", report: "Report", openBrowser: "Open in browser",
    dispSettings: "Display settings", accentColor: "Accent color", showSources: "Show source paths", descLines: "Description lines", linesSuffix: " lines",
    fullscreen: "Fullscreen", windowed: "Exit fullscreen",
    watcherOn: "watcher running", watcherOff: "watcher stopped",
    trackedFiles: "Tracked files", clickHint: "click → history / diff",
    kindGlobal: "Global", kindProject: "Project",
    scopeAll: "All", shadowed: "overridden by project", shadowedTip: "A same-named project item takes precedence · ",
    trackPathPlaceholder: "Project folder / .claude / config file path",
    trackAdd: "Track", trackNone: "No config files found to track", trackAlready: "Already tracked",
    projPickOpen: "＋ Add from projects", projPickEmpty: "No projects with .claude", projPickTracked: "tracked",
    untrack: "Untrack", untracked: "Untracked", untrackConfirm: "Confirm",
    settings: "Settings", settingsHint: "8 categories · with source paths",
    source: "Source", remove: "Remove", del: "Delete", cancel: "Cancel", add: "Add",
    permPlaceholder: "e.g. Bash(npm run*)", hookPlaceholder: "hook command",
    closePanel: "Close panel", openHistory: "Open history panel", closeHistory: "Close history panel", historyDiff: "HISTORY / DIFF",
    compareTo: "Compare with", working: "Working copy (current file)", workingShort: "working copy",
    history: "Change history", restore: "Restore", restoreConfirm: "Confirm", restoreTitle: "Restore this version", restoreNote: "Current state is auto-snapshotted + backed up",
    noDiff: "No text changes between the selected versions.",
    generatedPrefix: "Tracking ", generatedMid: " · last snapshot ",
    toastAdded: "Added", toastRemoved: "Removed", toastSnapshot: "Snapshot created", snapshotMsg: "snapshot (dashboard)",
    toastRefreshed: "Refreshed", toastReport: "Generating report…", toastBrowser: "Opening in browser",
    toastWatcherStart: "watcher started", toastWatcherStop: "watcher stopped", toastRestored: "Restored · current state backed up",
    langTitle: "한국어로 전환",
    failed: "Failed", loading: "Loading…", fetchFail: "fetch failed", unknown: "unknown",
    emptyTracked: "No tracked files - add with cas.py track", emptyCards: "No items / file not found",
    noHistory: "No snapshot history (snapshot first)", emptyTrackedResp: "Failed to load status (empty response)",
    emptyConfigResp: "Failed to load settings (empty response)", trackedStatus: "tracked status", deletedHash: "deleted",
    delConfirm: "Confirm delete", trashMoveHint: "Move to .trash (recoverable)",
    addNamePlaceholder: "name description…", needServerJson: "Server JSON required: name {…}",
    libSectionTitle: "Library (toggle install)", libNotInstalled: "Not installed", libInstalled: "Installed", libModified: "Modified",
    libEmpty: "No library items", kitRef: "kit ref", done: "done",
    libInstall: "Install", libSync: "Sync", libSyncConfirm: "Confirm overwrite (backed up)", libUninstallConfirm: "Confirm remove (.trash)",
    libUnregistered: "Unregistered", libPathPlaceholder: "Library path (.claude-structured directory)", libRegister: "Register", libRegistered: "Library registered",
    curFileTitle: "Current file content", readOnly: "Read-only", emptyFile: "(empty file)", diffFetchFail: "diff fetch failed",
    restoring: "Restoring…", watcherErr: "watcher status error", stopped: "stopped", ago: "ago",
    displayModeFail: "Cannot change display mode (host unsupported)",
    toastReportOpened: "Report opened in browser", toastReportFail: "Report failed", toastTabOpened: "Browser tab opened", toastOpenFail: "Open failed",
    collapseAll: "Collapse all", collapseAllTitle: "Collapse all expanded categories",
    selectFilePrompt: "Select a file", selectFileHint: "Click a tracked file on the left",
    libPaths: "Library paths", libPathRemoved: "Path removed", libEnvTag: "env",
    libEnvHint: "Set via CLAUDE_CONFIG_LIBRARIES env; can't be removed from the dashboard",
    libInstallSelected: "Install selected", libInstallGroup: "Install group", libInstallGroupConfirm: "Confirm install",
    libInstallGroupHint: "Install all not-installed skills in this group", libAllInstalled: "All already installed",
    installTarget: "Install to", targetGlobal: "Global (~/.claude)", rootItems: "root items · no folder",
    toastGroup: "Group install done", toastSel: "Selected install done", cntUnit: "",
  },
};

let lang = "ko";
try { if (localStorage.getItem("cm.lang") === "en") lang = "en"; } catch { /* iframe 에서 localStorage 차단될 수 있음 */ }
const t = (k: string): string => (I18N[lang] || I18N.ko)[k] ?? k;

async function callTool(name: string, args: Record<string, unknown> = {}): Promise<string> {
  if (STANDALONE) {
    const res = await fetch(`/api/tool/${name}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(args),
    });
    if (!res.ok) throw new Error(`REST ${name}: ${res.status}`);
    return (await res.json()).text ?? "";
  }
  const r = await app.callServerTool({ name, arguments: args });
  return (r.content?.find((c: any) => c.type === "text") as any)?.text ?? "";
}

// MCP 브리지 전용 기능들은 standalone 에서 no-op.
function pushCtx(t: string): void {
  if (STANDALONE) return;
  app.updateModelContext({ content: [{ type: "text", text: t }] });
}
function jparse(t: string): any { try { return JSON.parse(t); } catch { return null; } }
function jparseLast(t: string): any {
  const lines = t.trim().split("\n");
  for (let i = lines.length - 1; i >= 0; i--) { const v = jparse(lines[i]); if (v) return v; }
  return null;
}

const basename = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() || p;
const dirname = (p: string) => { const a = p.split(/[\\/]/); a.pop(); return a.join("\\"); };

// ----- state -----
let selectedPath = "";
let currentRevs: any[] = [];
let fromRev = "";
let toRev = "work";
let detailOpen = true;
const collapsed = new Set<string>();         // 접힌 섹션 title
const secTitles = new Set<string>();         // 접기 가능한 섹션 title (전부 접기 대상)
let collapsedInit = false;                    // 기본 접힘 1회만 적용
const libGroupOpen = new Set<string>();      // 펼친 라이브러리 스킬 그룹 경로(기본 접힘)
const libChecked = new Set<string>();        // 선택 설치용 체크된 항목 key(카테고리 무관)
const libOpen = new Set<string>(["skills"]); // 펼친 카테고리(기본값: Skills 만)
let libProjectTargets: string[] = [];        // 설치 대상 후보(추적 중인 프로젝트 .claude 경로들). renderTracked 가 매 새로고침 갱신
let libTarget = "";                           // 선택된 라이브러리 설치 대상("" = 전역 ~/.claude, 아니면 프로젝트 .claude)
let libSelBarUpdate: (() => void) | null = null; // 체크박스 -> 상단 "선택 설치 (N)" 카운트 갱신 훅
let scopeFilter = "all";                      // 설정 스코프 필터: 'all' | 'global' | <projectPath>
const srcOpen: Record<string, boolean> = {};  // 출처 그룹 접힘 상태(키: `${secTitle}::g` | `${secTitle}::${project}`)
let lastConfigSections: any[] = [];           // 스코프 칩/그룹 즉시 재렌더용 최신 섹션 캐시

let toastT: number | undefined;
function flashToast(msg: string): void {
  const el = $("toast");
  el.textContent = msg;
  el.style.display = "block";
  if (toastT) clearTimeout(toastT);
  toastT = window.setTimeout(() => { el.style.display = "none"; }, 1900);
}

// ----- tracked file rows -----
// 파일 상태 배지 라벨(현재 lang 반영). 상태값 new/deleted 는 i18n 키 newFile/deleted 에 매핑된다(키 이름이 상태값과 다름).
const statusLabel = (st: string): string =>
  (({ new: t("newFile"), modified: t("modified"), deleted: t("deleted"), unchanged: t("unchanged") } as Record<string, string>)[st] || st);
// 경로 추가 입력행: 프로젝트 폴더/.claude/파일 경로 -> config_track(프리셋 자동 감지).
function buildTrackAdder(): HTMLElement {
  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = t("trackPathPlaceholder");
  const btn = document.createElement("button");
  btn.className = "addbtn";
  btn.textContent = t("trackAdd");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) return;
    btn.textContent = "…";
    try {
      const r = jparse(await callTool("config_track", { path: v }));
      const added = r && Array.isArray(r.added) ? r.added.length : 0;
      const already = r && Array.isArray(r.already) ? r.already.length : 0;
      flashToast(added ? `${t("trackAdd")} ${added} · ${t("done")}` : already ? t("trackAlready") : t("trackNone"));
      input.value = "";
      await refresh();
    } catch (e) { btn.textContent = t("failed"); console.error("[config-monitor] track add", e); }
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter") submit(); });
  adder.append(input, btn);
  return adder;
}

// 프로젝트(전역 아님) 행에만 붙는 추적 해제(×) 버튼. 2-click 확인. 파일 자체는 안 지움.
function buildUntrackBtn(p: string): HTMLElement {
  const b = document.createElement("button");
  b.className = "untrackbtn";
  b.textContent = "×";
  b.title = t("untrack");
  b.addEventListener("click", async (e) => {
    e.stopPropagation();                         // 행 클릭(패널 열기)과 분리
    if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = t("untrackConfirm"); return; }
    b.textContent = "…";
    try {
      const r = jparse(await callTool("config_untrack", { path: p }));
      if (r && r.ok === false) { flashToast(r.message || t("failed")); return; }
      flashToast(t("untracked"));
      await refresh();
    } catch (err) { b.textContent = t("failed"); console.error("[config-monitor] untrack", err); }
  });
  return b;
}

// 프로젝트에서 추가: .claude.json 의 projects 중 .claude 있는 것을 원클릭 track(config_track).
// 지연 로드(펼칠 때 list_projects 호출). 이미 추적 중인 프로젝트는 비활성 표시.
function buildProjectPicker(): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "projpick";
  const toggle = document.createElement("button");
  toggle.className = "projpicktoggle";
  toggle.textContent = t("projPickOpen");
  const list = document.createElement("div");
  list.className = "projpicklist";
  list.hidden = true;
  let loaded = false;
  toggle.addEventListener("click", async () => {
    list.hidden = !list.hidden;
    if (list.hidden || loaded) return;
    loaded = true;
    list.innerHTML = `<div class="empty">${esc(t("loading"))}</div>`;
    try {
      const res = jparse(await callTool("list_projects"));
      const projs = (res && Array.isArray(res.projects) ? res.projects : []).filter((p: any) => p.has_claude);
      // 이미 추적 중(라이브러리 후보 = 추적된 프로젝트 .claude dir)인지 비교.
      const trackedNorm = new Set(libProjectTargets.map((c) => c.replace(/\\/g, "/").toLowerCase()));
      list.innerHTML = "";
      if (!projs.length) { list.innerHTML = `<div class="empty">${esc(t("projPickEmpty"))}</div>`; return; }
      for (const p of projs) {
        const already = trackedNorm.has(String(p.claude_dir).replace(/\\/g, "/").toLowerCase());
        const row = document.createElement("button");
        row.className = "projpickrow" + (already ? " tracked" : "");
        row.disabled = already;
        row.innerHTML =
          `<span class="pnm">${esc(p.name)}</span><span class="ppath">${esc(p.claude_dir)}</span>` +
          (already ? `<span class="ptag">${esc(t("projPickTracked"))}</span>` : "");
        if (!already) row.addEventListener("click", async () => {
          try { await callTool("config_track", { path: p.claude_dir }); flashToast(`${t("trackAdd")} · ${p.name}`); await refresh(); }
          catch (e) { flashToast(t("failed")); console.error("[config-monitor] project track", e); }
        });
        list.appendChild(row);
      }
    } catch (e) { list.innerHTML = `<div class="empty">${esc(t("failed"))}</div>`; console.error("[config-monitor] list_projects", e); }
  });
  wrap.append(toggle, list);
  return wrap;
}

function renderTracked(status: any): number {
  const host = $("tracked");
  host.innerHTML = "";
  host.appendChild(buildTrackAdder());
  host.appendChild(buildProjectPicker());
  // status.defaults = 전역(기본 추적) 대상 경로 목록 -> 전역(editable) vs 프로젝트(view-only) 구분.
  const defaults = new Set<string>(Array.isArray(status.defaults) ? status.defaults : []);
  const list = document.createElement("div");
  list.className = "files";
  const rows: [string, string][] = [];
  for (const st of ["modified", "new", "deleted", "unchanged"]) {
    for (const p of status[st] || []) rows.push([p, st]);
  }
  // 설치 대상 후보 = 추적 중인 프로젝트(전역 아님, 삭제 아님)의 .claude 폴더. 라이브러리 설치 대상 select 옵션으로 노출.
  const seenT = new Set<string>();
  libProjectTargets = [];
  for (const [p, st] of rows) {
    if (st === "deleted" || defaults.has(p)) continue;
    const d = dirname(p);
    if (basename(d).toLowerCase() === ".claude" && !seenT.has(d)) { seenT.add(d); libProjectTargets.push(d); }
  }
  if (!rows.length) {
    list.innerHTML = `<div class="empty">${esc(t("emptyTracked"))}</div>`;
  }
  for (const [p, st] of rows) {
    const global = defaults.has(p);
    const row = document.createElement("div");
    row.className = "file" + (p === selectedPath ? " sel" : "");
    row.dataset.path = p;   // 선택 강조를 full path 로 매칭(같은 basename 파일 오강조 방지)
    row.innerHTML =
      `<span class="fbadge ${st}">${esc(statusLabel(st))}</span>` +
      `<span class="kind ${global ? "kglobal" : "kproject"}">${esc(global ? t("kindGlobal") : t("kindProject"))}</span>` +
      `<div class="fmeta"><div class="fname">${esc(basename(p))}</div>` +
      `<div class="fdir">${esc(dirname(p))}</div></div>`;
    row.addEventListener("click", () => selectFile(p));
    // 프로젝트 행: 추적 해제(×). 전역 행: 열기 화살표(기본 감시 대상이라 제거 불가).
    if (global) {
      const chev = document.createElement("span");
      chev.className = "chev";
      chev.textContent = "›";
      row.appendChild(chev);
    } else {
      row.appendChild(buildUntrackBtn(p));
    }
    list.appendChild(row);
  }
  host.appendChild(list);
  return rows.length;
}

// ----- config sections (collapsible, with source) -----
// 스코프 필터/출처 그룹/재정의 배지 지원. 설정 섹션은 #config 안의 #cfg-scoped 래퍼에 렌더.
// Library 섹션은 래퍼 밖 #config 에 append 되므로 칩/그룹 즉시 재렌더가 Library 를 지우지 않는다.
function renderConfig(sections: any[]): void {
  const host = $("config");
  lastConfigSections = sections;
  let wrap = document.getElementById("cfg-scoped") as HTMLElement | null;
  const freshMount = !wrap;
  if (freshMount) {
    host.innerHTML = "";
    wrap = document.createElement("div");
    wrap.id = "cfg-scoped";
    host.appendChild(wrap);
    secTitles.clear();   // 전체 새로고침 때만 초기화(재렌더 시엔 Library 타이틀 보존)
  } else {
    wrap!.innerHTML = "";
  }
  const w = wrap!;
  if (!collapsedInit) {
    for (const sec of sections) {
      if (/^(Skills|Agents|Scheduled|Desktop)/.test(sec.title)) collapsed.add(sec.title);
    }
    collapsedInit = true;
  }
  // 스캔 결과의 distinct 프로젝트 경로(등장 순), 칩/필터의 유일 원천(카드 project 값과 동일 소스).
  const projects: string[] = [];
  const projSeen = new Set<string>();
  for (const sec of sections) for (const c of sec.cards || []) {
    if (c.scope === "project" && c.project && !projSeen.has(c.project)) { projSeen.add(c.project); projects.push(c.project); }
  }
  if (scopeFilter !== "all" && scopeFilter !== "global" && !projSeen.has(scopeFilter)) scopeFilter = "all";
  if (projects.length) w.appendChild(buildScopeChips(projects));
  for (const sec of sections) renderConfigSection(w, sec);
}

// 스코프 필터 칩: 전체 / 전역 / 프로젝트별. 클릭 시 캐시 섹션으로 즉시 재렌더(서버 왕복 없음).
// TODO: 프로젝트가 수십 개가 되면 이 칩 행을 검색형 select 로 교체.
function buildScopeChips(projects: string[]): HTMLElement {
  const row = document.createElement("div");
  row.className = "scopechips";
  const mk = (val: string, label: string, title?: string): HTMLElement => {
    const chip = document.createElement("button");
    chip.className = "scopechip" + (scopeFilter === val ? " on" : "");
    chip.textContent = label;
    if (title) chip.title = title;
    chip.addEventListener("click", () => { scopeFilter = val; renderConfig(lastConfigSections); });
    return chip;
  };
  row.appendChild(mk("all", t("scopeAll")));
  row.appendChild(mk("global", t("kindGlobal")));
  for (const p of projects) row.appendChild(mk(p, basename(p), p));
  return row;
}

const isAddCard = (c: any): boolean => !!(c.edit && String(c.edit.kind || "").endsWith("-add"));

// 카드 1개 렌더. shadowMap 있으면 shadowable 섹션의 전역 카드(add 카드 제외)에 재정의 배지+점선.
function renderConfigCard(c: any, shadowMap: Map<string, string[]> | null): HTMLElement {
  const card = document.createElement("div");
  card.className = "card";
  const shadowedBy = (shadowMap && c.scope !== "project" && !isAddCard(c)) ? shadowMap.get(c.name) : undefined;
  let shadowBadge = "";
  if (shadowedBy && shadowedBy.length) {
    card.classList.add("shadowed");
    card.title = t("shadowedTip") + shadowedBy.join(" · ");
    const suffix = shadowedBy.length > 1 ? ` ×${shadowedBy.length}` : "";
    shadowBadge = `<span class="shbadge">${esc(t("shadowed"))}${suffix}</span>`;
  }
  card.innerHTML =
    `<div class="cname"><span class="nm">${esc(c.name)}</span>` +
    `<span class="cbadges">${shadowBadge}` +
    (c.badge ? `<span class="badge ${c.ok ? "ok" : ""}">${esc(c.badge)}</span>` : "") +
    `</span></div>` +
    (c.kv || [])
      .map(([k, v]: [string, string]) =>
        `<div class="kv"><span class="k">${esc(k)}</span>` +
        `<span class="v ${valClass(k)}">${esc(v)}</span></div>`)
      .join("");
  if (c.edit) card.appendChild(buildEditUI(c.edit));
  return card;
}

// 출처 그룹 헤더(카드 그리드 full-width 행). 클릭 시 그룹 접기/펼치기(로컬 srcOpen) 후 재렌더.
function buildSrcGroupHeader(sec: any, isGlobal: boolean, project: string, count: number): HTMLElement {
  const key = `${sec.title}::${isGlobal ? "g" : project}`;
  const open = (key in srcOpen) ? srcOpen[key] : isGlobal;   // 기본: 전역 열림, 프로젝트 접힘
  const head = document.createElement("div");
  head.className = "srcgrp" + (open ? "" : " collapsed");
  const pathTxt = isGlobal ? (sec.source || "") : project;
  head.innerHTML =
    `<span class="chev2">▾</span>` +
    `<span class="scopepill ${isGlobal ? "global" : "project"}">${esc(isGlobal ? t("kindGlobal") : t("kindProject"))}</span>` +
    `<span class="srcpath">${esc(pathTxt)}</span>` +
    `<span class="srccount">${count}</span><span class="srcline"></span>`;
  head.addEventListener("click", () => { srcOpen[key] = !open; renderConfig(lastConfigSections); });
  return head;
}

function renderConfigSection(host: HTMLElement, sec: any): void {
  const cards = sec.cards || [];
  const hasProject = cards.some((c: any) => c.scope === "project");
  const visible = cards.filter((c: any) =>
    scopeFilter === "all" ? true
      : scopeFilter === "global" ? c.scope !== "project"
        : (c.scope === "project" && c.project === scopeFilter));
  // 필터 모드에서 결과 0개 섹션은 통째로 스킵. 전체 모드는 항상 렌더.
  if (scopeFilter !== "all" && !visible.length) return;

  secTitles.add(sec.title);
  const secEl = document.createElement("div");
  secEl.className = "sec" + (collapsed.has(sec.title) ? " collapsed" : "");
  secEl.dataset.col = "1";

  const gCount = cards.filter((c: any) => c.scope !== "project").length;
  const pCount = cards.length - gCount;
  const summary = (scopeFilter === "all" && pCount)
    ? `<span class="secsum">${esc(t("kindGlobal"))} ${gCount} · ${esc(t("kindProject"))} ${pCount}</span>` : "";
  const srcHtml = sec.source
    ? `<div class="secsrc"><span class="lbl">${esc(t("source"))}</span><span class="val">${esc(sec.source)}</span></div>`
    : "";
  const head = document.createElement("div");
  head.className = "sechead";
  head.innerHTML =
    `<div class="secrow"><span class="chev2">▾</span>` +
    `<span class="sectitle">${esc(sec.title)}</span>` +
    `<span class="seccount">${visible.length}</span>${summary}</div>` + srcHtml;
  head.addEventListener("click", () => {
    if (collapsed.has(sec.title)) collapsed.delete(sec.title); else collapsed.add(sec.title);
    secEl.classList.toggle("collapsed");
  });
  secEl.appendChild(head);

  const body = document.createElement("div");
  body.className = "secbody";
  // 재정의 맵(Agents/Skills 만): 이름 -> [프로젝트...]. 필터 무관 전체 프로젝트 카드 기준.
  const shadowable = /^(Agents|Skills)/.test(sec.title);
  let shadowMap: Map<string, string[]> | null = null;
  if (shadowable && hasProject) {
    shadowMap = new Map();
    for (const c of cards) if (c.scope === "project") {
      const arr = shadowMap.get(c.name) || [];
      arr.push(c.project);
      shadowMap.set(c.name, arr);
    }
  }

  if (scopeFilter === "all" && hasProject) {
    // 그룹 모드: 전역 그룹 + 프로젝트 그룹들(등장 순). 접힌 그룹은 카드 렌더 스킵(DOM 제외).
    const globalCards = cards.filter((c: any) => c.scope !== "project");
    const gKey = `${sec.title}::g`;
    body.appendChild(buildSrcGroupHeader(sec, true, "", globalCards.length));
    if ((gKey in srcOpen) ? srcOpen[gKey] : true) {
      for (const c of globalCards) body.appendChild(renderConfigCard(c, shadowMap));
    }
    const projGroups = new Map<string, any[]>();
    for (const c of cards) if (c.scope === "project") {
      const arr = projGroups.get(c.project) || [];
      arr.push(c); projGroups.set(c.project, arr);
    }
    for (const [proj, pcards] of projGroups) {
      const pKey = `${sec.title}::${proj}`;
      body.appendChild(buildSrcGroupHeader(sec, false, proj, pcards.length));
      if ((pKey in srcOpen) ? srcOpen[pKey] : false) {
        for (const c of pcards) body.appendChild(renderConfigCard(c, shadowMap));
      }
    }
  } else {
    // 평면 모드(전체+프로젝트 없음, 또는 필터 모드): 그룹 헤더 없이 카드만.
    if (!visible.length) body.innerHTML = `<div class="empty">${esc(t("emptyCards"))}</div>`;
    for (const c of visible) body.appendChild(renderConfigCard(c, shadowMap));
  }
  secEl.appendChild(body);
  host.appendChild(secEl);
}

// inline edit controls. edit meta is set by claude_config.py.
//   perm/hook            : 항목 chips + adder
//   mcp/skill/agent      : 카드 자체 제거 버튼 (인라인 확인)
//   mcp-add/skill-add/agent-add : adder 만 (입력 파싱 후 해당 add 도구 호출)
// removal uses inline confirm (window.confirm may be blocked in iframe sandbox).
function buildEditUI(edit: any): HTMLElement {
  if (["mcp", "skill", "agent"].includes(edit.kind)) return buildRemoveUI(edit);
  if (["mcp-add", "skill-add", "agent-add"].includes(edit.kind)) return buildAddUI(edit);
  const isPerm = edit.kind === "perm";
  const tgt = edit.settings ? { settings: edit.settings } : {};   // 프로젝트 카드면 그 프로젝트 settings 파일 대상
  const doRemove = (it: string) =>
    isPerm
      ? callTool("config_perm_remove", { kind: edit.permKind, rule: it, ...tgt })
      : callTool("config_hook_remove", { event: edit.event, needle: it, ...tgt });
  const doAdd = (v: string) =>
    isPerm
      ? callTool("config_perm_add", { kind: edit.permKind, rule: v, ...tgt })
      : callTool("config_hook_add", { event: edit.event, command: v, ...tgt });

  const wrap = document.createElement("div");
  wrap.className = "edit";

  const chips = document.createElement("div");
  chips.className = "chips";
  for (const it of edit.items || []) {
    const chip = document.createElement("div");
    chip.className = "chip";
    const txt = document.createElement("span");
    txt.className = "ctxt";
    txt.textContent = it;
    const x = document.createElement("button");
    x.className = "cx";
    x.textContent = "✕";
    x.title = t("remove");
    x.addEventListener("click", () => {
      const ok = document.createElement("button");
      ok.className = "ok";
      ok.textContent = t("del");
      const no = document.createElement("button");
      no.className = "no";
      no.textContent = t("cancel");
      chip.replaceChildren(txt, ok, no);
      no.addEventListener("click", () => wrap.replaceWith(buildEditUI(edit)));
      ok.addEventListener("click", async () => {
        ok.textContent = "…";
        try { await doRemove(it); flashToast(t("toastRemoved") + " · " + it); await refresh(); }
        catch (e) { ok.textContent = t("failed"); console.error("[config-monitor] remove", e); }
      });
    });
    chip.append(txt, x);
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);

  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = isPerm ? t("permPlaceholder") : t("hookPlaceholder");
  const add = document.createElement("button");
  add.className = "addbtn";
  add.textContent = t("add");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) return;
    add.textContent = "…";
    try { await doAdd(v); flashToast(t("toastAdded") + " · " + v); await refresh(); }
    catch (e) { add.textContent = t("failed"); console.error("[config-monitor] add", e); }
  };
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter") submit(); });
  adder.append(input, add);
  wrap.appendChild(adder);
  return wrap;
}

// 카드 단위 제거(mcp/skill/agent): 제거 버튼 -> 인라인 확인 -> 해당 remove 도구 호출.
function buildRemoveUI(edit: any): HTMLElement {
  const doRemove = () => {
    if (edit.kind === "mcp") return callTool("config_mcp_remove", { name: edit.name, scope: edit.scope });
    if (edit.kind === "skill") return callTool("config_skill_remove", { name: edit.name });
    return callTool("config_agent_remove", { name: edit.name });
  };
  const wrap = document.createElement("div");
  wrap.className = "edit";
  const btn = document.createElement("button");
  btn.className = "cx";
  btn.textContent = "✕ " + t("remove");
  btn.title = edit.kind === "mcp" ? `mcpServers.${edit.name} ${t("remove")} (${edit.scope})` : t("trashMoveHint");
  btn.addEventListener("click", () => {
    const ok = document.createElement("button");
    ok.className = "ok";
    ok.textContent = t("delConfirm");
    const no = document.createElement("button");
    no.className = "no";
    no.textContent = t("cancel");
    wrap.replaceChildren(ok, no);
    no.addEventListener("click", () => wrap.replaceWith(buildRemoveUI(edit)));
    ok.addEventListener("click", async () => {
      ok.textContent = "…";
      try {
        const res = jparse(await doRemove());
        if (res && res.ok === false) { ok.textContent = t("failed"); flashToast(res.message || t("failed")); return; }
        flashToast(t("toastRemoved") + " · " + edit.name);
        await refresh();
      } catch (e) { ok.textContent = t("failed"); console.error("[config-monitor] remove", e); }
    });
  });
  wrap.appendChild(btn);
  return wrap;
}

// 추가 전용 카드(mcp-add/skill-add/agent-add).
//   mcp   입력: name {"command":...}   (첫 토큰=이름, 나머지=서버 JSON)
//   skill/agent 입력: name 설명…       (첫 토큰=이름, 나머지=description)
function buildAddUI(edit: any): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "edit";
  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = edit.kind === "mcp-add" ? 'name {"command":"npx","args":[...]}' : t("addNamePlaceholder");
  const add = document.createElement("button");
  add.className = "addbtn";
  add.textContent = t("add");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) return;
    const sp = v.indexOf(" ");
    const name = sp < 0 ? v : v.slice(0, sp);
    const rest = sp < 0 ? "" : v.slice(sp + 1).trim();
    add.textContent = "…";
    try {
      let res: any;
      if (edit.kind === "mcp-add") {
        if (!rest) { flashToast(t("needServerJson")); add.textContent = t("add"); return; }
        res = jparse(await callTool("config_mcp_add", { name, serverJson: rest, scope: edit.scope }));
      } else if (edit.kind === "skill-add") {
        res = jparse(await callTool("skill_scaffold", { name, desc: rest || undefined }));
      } else {
        res = jparse(await callTool("config_agent_add", { name, desc: rest || undefined }));
      }
      if (res && res.ok === false) { add.textContent = t("failed"); flashToast(res.message || t("failed")); return; }
      flashToast(t("toastAdded") + " · " + name);
      await refresh();
    } catch (e) { add.textContent = t("failed"); console.error("[config-monitor] add", e); }
  };
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter") submit(); });
  adder.append(input, add);
  wrap.appendChild(adder);
  return wrap;
}

// ----- Library section (라이브러리 토글: /plugin 식 설치/제거) -----
const libStatus = (s: string): [string, string] =>
  (({ not_installed: [t("libNotInstalled"), ""], installed: [t("libInstalled"), "ok"], modified: [t("libModified"), "warn"] } as Record<string, [string, string]>)[s] || [s, ""]);

// 라이브러리 경로 등록 입력행. 여러 경로 등록 가능(백엔드가 config.json libraries 배열에 멱등 append).
// 빈 상태 등록 UI 와 채워진 목록의 "경로 추가" 양쪽에서 재사용.
function buildLibAdder(): HTMLElement {
  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = t("libPathPlaceholder");
  const btn = document.createElement("button");
  btn.className = "addbtn";
  btn.textContent = t("libRegister");
  const submit = async () => {
    const v = input.value.trim();
    if (!v) return;
    btn.textContent = "…";
    try { await callTool("library_scan", { lib: v }); flashToast(t("libRegistered")); await refresh(); }
    catch (e) { btn.textContent = t("failed"); console.error("[config-monitor] lib register", e); }
  };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter") submit(); });
  adder.append(input, btn);
  return adder;
}

// 항목(스킬/에이전트/커맨드) 액션 버튼: 상태별 설치/동기화/제거. 설치는 relpath(가변 깊이) + lib 로 지정.
function mkItemActions(it: any): HTMLElement {
  const act = document.createElement("div");
  act.className = "edit";
  const mk = (txt: string, run: () => Promise<string>, confirmTxt?: string) => {
    const b = document.createElement("button");
    b.className = "addbtn";
    b.textContent = txt;
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (confirmTxt && b.textContent !== confirmTxt) { b.textContent = confirmTxt; return; } // 2-click 확인
      b.textContent = "…";
      try {
        const r = jparse(await run());
        if (r && r.ok === false) { flashToast(r.message || t("failed")); b.textContent = t("failed"); return; }
        flashToast(`${txt} ${t("done")} · ${it.name}`);
        await refresh();
      } catch (err) { b.textContent = t("failed"); console.error("[config-monitor] library", err); }
    });
    return b;
  };
  const doInstall = () => callTool("library_install", { category: it.category, path: it.relpath, lib: it.lib, targetDir: libTarget || undefined });
  const doRemove = () => callTool("library_uninstall", { category: it.category, name: it.name, targetDir: libTarget || undefined });
  if (it.status === "not_installed") act.appendChild(mk(t("libInstall"), doInstall));
  if (it.status === "modified") act.appendChild(mk(t("libSync"), doInstall, t("libSyncConfirm")));
  if (it.status !== "not_installed") act.appendChild(mk(t("remove"), doRemove, t("libUninstallConfirm")));
  return act;
}

const libKey = (it: any): string => `${it.lib}|${it.category}|${it.relpath}`;

// 여러 항목 순차 설치 후 1회 새로고침(설치마다 새로고침하면 100+개에서 폭주).
// 설치 대상은 공용 상태 libTarget("" 이면 전역 ~/.claude). done: 완료 토스트 빌더(그룹/선택 설치가 서로 다른 문구).
interface InstallOpts { done?: (ok: number, fail: number) => string; }
async function installMany(items: any[], opts: InstallOpts = {}): Promise<void> {
  let ok = 0, fail = 0;
  for (const it of items) {
    const args: Record<string, unknown> = { category: it.category, path: it.relpath, lib: it.lib };
    if (libTarget) args.targetDir = libTarget;
    try {
      const r = jparse(await callTool("library_install", args));
      if (r && r.ok === false) fail++; else ok++;
    } catch { fail++; }
  }
  libChecked.clear();
  const failSfx = fail ? ` · ${fail} ${t("failed")}` : "";
  flashToast((opts.done ? opts.done(ok, fail) : `${t("libInstall")} ${ok} ${t("done")}`) + failSfx);
  await refresh();
}

// 콤팩트 항목 행: [체크박스] name [배지] [설치/동기화/제거]. 전 카테고리(agents/commands/skills) 공용.
function mkLibRow(it: any): HTMLElement {
  const [label, cls] = libStatus(it.status);
  const row = document.createElement("div");
  row.className = "libskill";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.className = "libcb";
  cb.checked = libChecked.has(libKey(it));
  cb.addEventListener("change", () => {
    if (cb.checked) libChecked.add(libKey(it)); else libChecked.delete(libKey(it));
    libSelBarUpdate?.();
  });
  const nm = document.createElement("span");
  nm.className = "sknm";
  nm.textContent = it.name;
  const bd = document.createElement("span");
  bd.className = "badge" + (cls ? " " + cls : "");
  bd.textContent = label + (it.kit_ref ? " · " + t("kitRef") : "");
  row.append(cb, nm, bd, mkItemActions(it));
  return row;
}

// 스킬을 group 경로(가변 깊이)로 트리화. 폴더 그룹은 접이식+그룹설치, 폴더 없는 루트 항목은 구분선 아래 나열.
function renderSkillTreeBody(skills: any[]): HTMLElement {
  interface Node { dirs: Map<string, Node>; skills: any[]; }
  const root: Node = { dirs: new Map(), skills: [] };
  for (const it of skills) {
    let node = root;
    for (const seg of it.group ? String(it.group).split("/") : []) {
      if (!node.dirs.has(seg)) node.dirs.set(seg, { dirs: new Map(), skills: [] });
      node = node.dirs.get(seg)!;
    }
    node.skills.push(it);
  }
  const collect = (node: Node): any[] => {
    let out = node.skills.slice();
    for (const c of node.dirs.values()) out = out.concat(collect(c));
    return out;
  };
  // 폴더 그룹만 렌더(루트 loose 항목 제외). 그룹 내부는 renderNode 로 재귀(하위 그룹 + 그 폴더 직속 항목).
  const renderGroups = (node: Node, path: string): HTMLElement => {
    const box = document.createElement("div");
    for (const [seg, child] of node.dirs) {
      const gpath = path ? `${path}/${seg}` : seg;
      const all = collect(child);
      const installed = all.filter((s) => s.status === "installed").length;
      const grp = document.createElement("div");
      grp.className = "libgrp" + (libGroupOpen.has(gpath) ? " open" : "");
      const gh = document.createElement("div");
      gh.className = "libgrphead";
      gh.innerHTML =
        `<span class="chev4">▸</span><span class="gname">${esc(seg)}</span>` +
        `<span class="gcount">${all.length}</span>` +
        (installed ? `<span class="ginst">${installed} ${esc(t("libInstalled"))}</span>` : "");
      const gall = document.createElement("button");
      gall.className = "linkbtn gallbtn";
      gall.textContent = t("libInstallGroup");
      gall.title = t("libInstallGroupHint");
      gall.addEventListener("click", async (e) => {
        e.stopPropagation();
        const pending = all.filter((s) => s.status !== "installed");
        if (!pending.length) { flashToast(t("libAllInstalled")); return; }
        if (gall.textContent !== t("libInstallGroupConfirm")) { gall.textContent = t("libInstallGroupConfirm"); return; }
        await installMany(pending, { done: (n) => `${t("toastGroup")} · ${seg} · ${n}${t("cntUnit")}` });
      });
      gh.appendChild(gall);
      gh.addEventListener("click", () => {
        if (libGroupOpen.has(gpath)) libGroupOpen.delete(gpath); else libGroupOpen.add(gpath);
        grp.classList.toggle("open");
      });
      const gbody = document.createElement("div");
      gbody.className = "libgrpbody";
      gbody.appendChild(renderNode(child, gpath));
      grp.append(gh, gbody);
      box.appendChild(grp);
    }
    return box;
  };
  const renderNode = (node: Node, path: string): HTMLElement => {
    const box = document.createElement("div");
    box.appendChild(renderGroups(node, path));
    for (const it of node.skills) box.appendChild(mkLibRow(it));  // 이 폴더 직속 스킬
    return box;
  };

  const frag = document.createElement("div");
  frag.appendChild(renderGroups(root, ""));
  if (root.skills.length) {
    const div = document.createElement("div");
    div.className = "librootdiv";
    div.innerHTML =
      `<span class="rline"></span>` +
      `<span class="rlbl">${esc(t("rootItems"))} · ${root.skills.length}</span>` +
      `<span class="rline"></span>`;
    frag.appendChild(div);
    for (const it of root.skills) frag.appendChild(mkLibRow(it));
  }
  return frag;
}

// 카테고리 토글 섹션: 헤더(chevron + 제목 + 개수 pill + n 설치됨 + 우측 읽기패턴 힌트) + 접이식 본문.
function renderCategory(cat: string, items: any[], title: string, hint: string): HTMLElement {
  const installed = items.filter((i) => i.status === "installed").length;
  const wrap = document.createElement("div");
  wrap.className = "libcat" + (libOpen.has(cat) ? "" : " collapsed");
  const head = document.createElement("div");
  head.className = "libcathead";
  head.innerHTML =
    `<span class="chev2">▾</span><span class="ctitle">${esc(title)}</span>` +
    `<span class="seccount">${items.length}</span>` +
    (installed ? `<span class="cinst">${installed} ${esc(t("libInstalled"))}</span>` : "") +
    `<span class="chint">${esc(hint)}</span>`;
  head.addEventListener("click", () => {
    if (libOpen.has(cat)) libOpen.delete(cat); else libOpen.add(cat);
    wrap.classList.toggle("collapsed");
  });
  const body = document.createElement("div");
  body.className = "libcatbody";
  if (!items.length) body.innerHTML = `<div class="empty">${esc(t("libEmpty"))}</div>`;
  else if (cat === "skills") body.appendChild(renderSkillTreeBody(items));
  else for (const it of items) body.appendChild(mkLibRow(it));
  wrap.append(head, body);
  return wrap;
}

// 설치 대상 바(Library 본문 최상단): [설치 대상] [대상 select] ......... [선택 설치 (N)].
// 대상 select 옵션 = "전역(~/.claude)" + 추적 중인 프로젝트 .claude 경로들(libProjectTargets).
// 설치 대상은 추적 중인 경로만 노출한다: 추적되지 않는 경로에 설치하면 대시보드에서 되돌릴 방법이 없다.
// (프로젝트 추가는 추적 패널의 "프로젝트에서 추가" 피커로 한다.)
// 대상 변경 시 그 대상으로 재스캔(refresh) -> 설치됨/변경됨 배지가 대상 기준으로 갱신된다.
function buildTargetBar(allItems: any[]): HTMLElement {
  const bar = document.createElement("div");
  bar.className = "libtbar";
  const lbl = document.createElement("span");
  lbl.className = "tlbl";
  lbl.textContent = t("installTarget");
  const sel = document.createElement("select");
  // 선택돼 있던 대상이 목록에서 사라졌으면(추적 해제) 전역으로 리셋.
  if (libTarget && !libProjectTargets.includes(libTarget)) libTarget = "";
  sel.innerHTML =
    `<option value="">${esc(t("targetGlobal"))}</option>` +
    libProjectTargets.map((p) => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
  sel.value = libTarget;
  sel.addEventListener("change", async () => { libTarget = sel.value; await refresh(); });
  const selBtn = document.createElement("button");
  selBtn.className = "addbtn selbtn";
  const update = () => {
    const n = allItems.filter((it) => libChecked.has(libKey(it))).length;
    selBtn.textContent = `${t("libInstallSelected")} (${n})`;
    (selBtn as HTMLButtonElement).disabled = n === 0;
    selBtn.style.opacity = n ? "1" : ".5";
  };
  libSelBarUpdate = update;
  update();
  selBtn.addEventListener("click", async () => {
    const chosen = allItems.filter((it) => libChecked.has(libKey(it)));
    if (!chosen.length) return;
    await installMany(chosen, { done: (n) => `${t("toastSel")} · ${n}${t("cntUnit")}` });
  });
  bar.append(lbl, sel, selBtn);
  return bar;
}

function renderLibrary(host: HTMLElement, res: any): void {
  const secEl = document.createElement("div");
  secEl.className = "sec" + (collapsed.has("Library") ? " collapsed" : "");
  secEl.dataset.col = "1";
  secTitles.add("Library");
  const libs = (res && res.libraries) || [];
  // 카테고리별 수집(agents/commands/skills). allItems 는 상단 "선택 설치" 카운트/설치 대상용.
  const byCat: Record<string, any[]> = { agents: [], commands: [], skills: [] };
  for (const l of libs) {
    for (const [cat, arr] of Object.entries(l.categories || {})) {
      const bucket = byCat[cat] || (byCat[cat] = []);
      for (const it of arr as any[]) bucket.push({ ...it, category: cat, lib: l.lib });
    }
  }
  const allItems = [...byCat.agents, ...byCat.commands, ...byCat.skills];
  libSelBarUpdate = null;  // 이전 렌더의 카운트 훅 무효화(새 설치 대상 바가 다시 설정)
  const head = document.createElement("div");
  head.className = "sechead";
  head.innerHTML =
    `<div class="secrow"><span class="chev2">▾</span>` +
    `<span class="sectitle">${esc(t("libSectionTitle"))}</span>` +
    `<span class="seccount">${allItems.length}</span></div>` +
    (libs.length ? `<div class="secsrc"><span class="lbl">${esc(t("source"))}</span><span class="val">${esc(libs.map((l: any) => l.lib).join(" · "))}</span></div>` : "");
  head.addEventListener("click", () => {
    if (collapsed.has("Library")) collapsed.delete("Library"); else collapsed.add("Library");
    secEl.classList.toggle("collapsed");
  });
  secEl.appendChild(head);

  const body = document.createElement("div");
  body.className = "secbody libbody";  // libbody: 카테고리 토글이 세로로 쌓이도록 그리드 해제
  // 미등록(라이브러리 0개)이라도 섹션 구조는 동일하게: 카테고리 자리에 "라이브러리 항목 없음" + 하단 경로 카드.
  if (!libs.length) {
    body.innerHTML = `<div class="empty">${esc(t("libEmpty"))}</div>`;
  } else {
    body.appendChild(buildTargetBar(allItems));
    body.appendChild(renderCategory("agents", byCat.agents, "Agents", "agents/*.md"));
    body.appendChild(renderCategory("commands", byCat.commands, "Commands", "commands/*.md"));
    body.appendChild(renderCategory("skills", byCat.skills, "Skills", "skills/<name>/SKILL.md"));
  }
  // 다중 라이브러리 경로 관리(전체 폭): 등록된 경로 목록(제거 가능) + 신규 경로 등록 입력행.
  // env(CLAUDE_CONFIG_LIBRARIES) 지정 경로는 대시보드에서 제거 불가 -> env 태그만 표시.
  const mkPathChip = (l: any): HTMLElement => {
    const chip = document.createElement("div");
    chip.className = "chip";
    const txt = document.createElement("span");
    txt.className = "ctxt";
    txt.textContent = l.lib + (l.error ? ` · ${l.error}` : "");
    chip.appendChild(txt);
    if (l.source === "env") {
      const tag = document.createElement("span");
      tag.className = "libenv";
      tag.textContent = t("libEnvTag");
      tag.title = t("libEnvHint");
      chip.appendChild(tag);
      return chip;
    }
    const x = document.createElement("button");
    x.className = "cx";
    x.textContent = "✕";
    x.title = t("remove");
    x.addEventListener("click", () => {
      const ok = document.createElement("button");
      ok.className = "ok";
      ok.textContent = t("remove");
      const no = document.createElement("button");
      no.className = "no";
      no.textContent = t("cancel");
      chip.replaceChildren(txt, ok, no);
      no.addEventListener("click", () => chip.replaceWith(mkPathChip(l)));
      ok.addEventListener("click", async () => {
        ok.textContent = "…";
        try {
          const r = jparse(await callTool("library_unregister", { lib: l.lib }));
          if (r && r.ok === false) { flashToast(r.message || t("failed")); chip.replaceWith(mkPathChip(l)); return; }
          flashToast(t("libPathRemoved") + " · " + basename(l.lib));
          await refresh();
        } catch (e) { flashToast(t("failed")); console.error("[config-monitor] lib unregister", e); chip.replaceWith(mkPathChip(l)); }
      });
    });
    chip.appendChild(x);
    return chip;
  };
  const pathsWrap = document.createElement("div");
  pathsWrap.style.gridColumn = "1 / -1";
  const pathsLbl = document.createElement("div");
  pathsLbl.className = "dlabel";
  pathsLbl.style.margin = "2px 0 7px";
  pathsLbl.textContent = t("libPaths");
  const chipsBox = document.createElement("div");
  chipsBox.className = "chips";
  chipsBox.style.marginBottom = "9px";
  for (const l of libs) chipsBox.appendChild(mkPathChip(l));
  pathsWrap.append(pathsLbl, chipsBox, buildLibAdder());
  body.appendChild(pathsWrap);
  secEl.appendChild(body);
  host.appendChild(secEl);
}

async function refreshLibrary(): Promise<void> {
  const host = $("config");
  // 라이브러리 미설정/스캔 실패는 정상 상태: 빈 목록으로 renderLibrary 가 동일 섹션 구조 + 경로 등록 카드를 렌더.
  let res: any = { libraries: [] };
  try {
    const parsed = jparse(await callTool("library_scan", libTarget ? { targetDir: libTarget } : {}));
    if (parsed && parsed.ok !== false) res = parsed;
  } catch { /* 스캔 오류 -> 빈 목록(미등록)으로 진행 */ }
  renderLibrary(host, res);
}

// ----- detail panel: history + diff -----
const revTime = (r: any) => (r.time || "").replace("T", " ").slice(0, 19);
const revLabel = (id: string) => {
  const r = currentRevs.find((x) => x.snapshot === id);
  return r ? r.message || "(no message)" : id;
};

async function selectFile(p: string): Promise<void> {
  selectedPath = p;
  fromRev = "";
  toRev = "work";
  detailOpen = true;
  applyDetailState();
  $("sel-name").textContent = basename(p);
  $("sel-path").textContent = p;
  // 선택 강조 갱신
  document.querySelectorAll(".file").forEach((el) => el.classList.remove("sel"));
  pushCtx(`[Config Monitor] 사용자가 '${p}' 의 변경 이력을 보는 중.`);
  const body = $("panel-body");
  body.innerHTML = `<div class="empty">${esc(t("loading"))}</div>`;
  const h = jparseLast(await callTool("get_file_history", { path: p }));
  currentRevs = (h && h.revisions) || [];
  // 선택 행 다시 표시(refresh 없이 강조만). full path 로 매칭(같은 basename, 예: 여러 settings.json
  // 파일이 함께 강조되는 버그 방지).
  document.querySelectorAll<HTMLElement>(".file").forEach((el) => {
    if (el.dataset.path === p) el.classList.add("sel");
  });
  if (!currentRevs.length) {
    body.innerHTML = `<div class="empty">${esc(t("noHistory"))}</div>`;
    return;
  }
  if (currentRevs.length) fromRev = currentRevs[currentRevs.length - 1].snapshot;
  renderHistory();
  renderDiffFor();
}

function renderHistory(): void {
  const body = $("panel-body");
  body.innerHTML = "";
  const revsDesc = currentRevs.slice().reverse();

  // 비교 대상 select
  const cmp = document.createElement("div");
  cmp.className = "cmpbar";
  const opts = [`<option value="work">${esc(t("working"))}</option>`]
    .concat(revsDesc.map((r) =>
      `<option value="${esc(r.snapshot)}">${esc(revTime(r).slice(5, 16))} · ${esc(r.message || "")}</option>`))
    .join("");
  cmp.innerHTML = `<span class="dlabel">${esc(t("compareTo"))}</span><select id="cmp-to">${opts}</select>`;
  body.appendChild(cmp);
  const sel = cmp.querySelector("#cmp-to") as HTMLSelectElement;
  sel.value = toRev;
  sel.addEventListener("change", () => { toRev = sel.value; renderDiffFor(); });

  // 타임라인
  const hl = document.createElement("div");
  hl.className = "dlabel";
  hl.textContent = t("history");
  body.appendChild(hl);

  // 고정 높이 스크롤 컨테이너 > relative 트랙 (spine 이 스크롤 내용과 함께 늘어나도록)
  const list = document.createElement("div");
  list.className = "rev-list";
  const tl = document.createElement("div");
  tl.className = "rev-track";
  tl.innerHTML = `<div class="spine"></div>`;
  revsDesc.forEach((r) => {
    const item = document.createElement("div");
    item.className = "rev" + (r.snapshot === fromRev ? " sel" : "");
    item.innerHTML =
      `<span class="rdot"></span>` +
      `<div class="rbody"><div class="rmsg" title="${esc(r.message || "")}">${esc(r.message || "(no message)")}</div>` +
      `<div class="rmeta">${esc(revTime(r))} · ${esc(r.hash || t("deletedHash"))}</div></div>` +
      `<button class="rrestore" title="${esc(t("restoreTitle"))}">${esc(t("restore"))}</button>`;
    item.querySelector(".rbody")!.addEventListener("click", () => {
      fromRev = r.snapshot; renderHistory(); renderDiffFor();
    });
    item.querySelector(".rrestore")!.addEventListener("click", (e) => {
      e.stopPropagation(); inlineRestore(e.currentTarget as HTMLElement, r);
    });
    tl.appendChild(item);
  });
  list.appendChild(tl);
  body.appendChild(list);

  // Diff 접이식(현재 파일 내용 뷰어와 동일한 curwrap 패턴). 기본 펼침.
  const diffWrap = document.createElement("div");
  diffWrap.className = "curwrap open";
  diffWrap.innerHTML =
    `<div class="curhead"><span class="chev3">▸</span><span>Diff</span></div>` +
    `<div class="curbody"><div id="diff-area"></div></div>`;
  diffWrap.querySelector(".curhead")!.addEventListener("click", () => diffWrap.classList.toggle("open"));
  body.appendChild(diffWrap);

  // 현재 파일 내용 뷰어 — 기본 접힘, 첫 펼침 때 lazy 조회. 보기 전용(편집 아님).
  const cur = document.createElement("div");
  cur.className = "curwrap";
  cur.innerHTML =
    `<div class="curhead"><span class="chev3">▸</span><span>${esc(t("curFileTitle"))}</span>` +
    `<span class="rhint">${esc(t("readOnly"))}</span></div>` +
    `<div class="curbody"><pre>${esc(t("loading"))}</pre></div>`;
  let curLoaded = false;
  cur.querySelector(".curhead")!.addEventListener("click", async () => {
    const open = cur.classList.toggle("open");
    if (!open || curLoaded) return;
    const pre = cur.querySelector("pre")!;
    try {
      const content = await callTool("get_file_content", { path: selectedPath });
      pre.textContent = content || t("emptyFile");
      curLoaded = true;
    } catch (e) {
      pre.textContent = t("fetchFail") + ": " + String(e);
    }
  });
  body.appendChild(cur);
}

async function renderDiffFor(): Promise<void> {
  if (!fromRev) return;
  pushCtx(`[Config Monitor] '${selectedPath}' diff ${fromRev} -> ${toRev}.`);
  const args: Record<string, unknown> = { path: selectedPath, from: fromRev };
  if (toRev && toRev !== "work") args.to = toRev;
  try {
    renderDiff(await callTool("get_diff", args));
  } catch (e) {
    const area = document.getElementById("diff-area");
    if (area) area.innerHTML = `<div class="empty err">${esc(t("diffFetchFail"))}: ${esc(String(e))}</div>`;
  }
}

function renderDiff(diff: string): void {
  const area = document.getElementById("diff-area");
  if (!area) return;
  if (!diff.trim() || diff.trim() === "텍스트 변경 없음") {
    area.innerHTML = `<div class="diffempty">${esc(t("noDiff"))}</div>`;
    return;
  }
  const fromLabel = revLabel(fromRev);
  const toLabel = toRev === "work" ? t("workingShort") : revLabel(toRev);
  // CRLF 파일의 diff 는 각 줄 끝에 \r 이 남는데, .dl 이 white-space:pre-wrap 이라
  // 그 \r 이 segment break 로 렌더돼 빈 줄처럼 보인다. 개행 종류와 무관하게 분리.
  const lines = diff.split(/\r?\n/).map((line) => {
    let cls = "";
    if (line.startsWith("+") && !line.startsWith("+++")) cls = "add";
    else if (line.startsWith("-") && !line.startsWith("---")) cls = "del";
    else if (line.startsWith("@@")) cls = "hunk";
    return `<div class="dl ${cls}">${esc(line) || "&nbsp;"}</div>`;
  }).join("");
  area.innerHTML =
    `<div class="diffwrap"><div class="diffhead">` +
    `<span>${esc(fromLabel)}</span><span class="arrow">→</span><span>${esc(toLabel)}</span></div>` +
    `<div class="difflines">${lines}</div></div>`;
}

// 복원: window.confirm 회피 위해 인라인 확인. 복원 전 자동 스냅샷+.bak 은 서버가 보장.
function inlineRestore(btn: HTMLElement, r: any): void {
  const box = document.createElement("span");
  box.className = "rconfirm";
  box.innerHTML = `<button class="ok" title="${esc(t("restoreNote"))}">${esc(t("restoreConfirm"))}</button><button class="no">${esc(t("cancel"))}</button>`;
  btn.replaceWith(box);
  box.querySelector(".no")!.addEventListener("click", (e) => { e.stopPropagation(); renderHistory(); });
  box.querySelector(".ok")!.addEventListener("click", async (e) => {
    e.stopPropagation();
    box.innerHTML = `<span class="rmeta">${esc(t("restoring"))}</span>`;
    try {
      const res = jparse(await callTool("config_restore", { path: selectedPath, from: r.snapshot }));
      if (res && res.ok) { flashToast(t("toastRestored")); await refresh(); await selectFile(selectedPath); }
      else box.innerHTML = `<span class="rmeta err">${esc(t("failed"))}: ${esc(res?.message || t("unknown"))}</span>`;
    } catch (err) {
      box.innerHTML = `<span class="rmeta err">${esc(t("failed"))}: ${esc(String(err))}</span>`;
    }
  });
}

// ----- detail panel open/close -----
function applyDetailState(): void {
  $("detail").classList.toggle("closed", !detailOpen);
}

// ----- load / refresh -----
function showErr(hostId: string, label: string, e: unknown): void {
  console.error(`[config-monitor] ${label}`, e);
  $(hostId).innerHTML = `<div class="empty err">${esc(label)} ${esc(t("fetchFail"))}: ${esc(String(e))}</div>`;
}

async function refresh(): Promise<void> {
  $("config").innerHTML = `<div class="empty">${esc(t("loading"))}</div>`;
  $("tracked").innerHTML = `<div class="empty">${esc(t("loading"))}</div>`;
  let trackedCount = 0;
  try {
    const trk = jparseLast(await callTool("get_tracked"));
    if (trk) trackedCount = renderTracked(trk);
    else $("tracked").innerHTML = `<div class="empty">${esc(t("emptyTrackedResp"))}</div>`;
  } catch (e) {
    showErr("tracked", t("trackedStatus"), e);
  }
  try {
    // libProjectTargets(추적 프로젝트 .claude 경로들)는 앞선 renderTracked 에서 채워짐 -> 프로젝트 스코프 설정 포함.
    const cfg = jparse(await callTool("get_config", { projects: libProjectTargets }));
    if (cfg) {
      renderConfig(cfg.sections || []);
      // settingsHint 앞자리 숫자만 실제 카테고리 수로 치환해 라이브 카운트 유지.
      $("config-hint").textContent = t("settingsHint").replace(/^\d+/, String((cfg.sections || []).length));
    } else $("config").innerHTML = `<div class="empty">${esc(t("emptyConfigResp"))}</div>`;
  } catch (e) {
    showErr("config", t("settings"), e);
  }
  try { await refreshLibrary(); } catch (e) { console.error("[config-monitor] library", e); }
  const now = new Date().toTimeString().slice(0, 8);
  $("subtitle").textContent = `${t("generatedPrefix")}${trackedCount}${t("generatedMid")}${now}`;
  refreshWatcher();
}

// ----- watcher status badge + toggle -----
const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function refreshWatcher(): Promise<boolean> {
  const dot = $("watcher-dot");
  const label = $("watcher-label");
  const btn = $("watcher-toggle") as HTMLButtonElement;
  let running = false;
  try {
    const st = jparse(await callTool("watcher_status"));
    running = !!(st && st.running);
    dot.className = "wdot" + (running ? " on" : "");
    // 파싱/상태 오류는 '정지'로 뭉개지 않고 명시한다 (침묵 실패가 디버깅을 막았던 회귀 가드).
    label.textContent = running ? t("watcherOn") : (st?.error ? t("watcherErr") : t("watcherOff"));
    btn.title = running
      ? `pid ${st.pid} · ${(st.dirs || []).length} dirs · ${Math.round(st.age_sec || 0)}s ${t("ago")}`
      : (st?.error || st?.reason || t("stopped"));
  } catch (e) {
    dot.className = "wdot";
    label.textContent = "watcher ?";
    btn.title = String(e);
  }
  btn.onclick = async () => {
    btn.disabled = true;
    label.textContent = "…";
    try {
      if (running) {
        await callTool("watcher_stop");
        flashToast(t("toastWatcherStop"));
        await refreshWatcher();
      } else {
        // watcher.ps1 가 spawn 후 heartbeat 를 쓰기까지 1~2s 걸린다.
        // 시작 직후 status 는 아직 정지이므로 잠시 기다렸다가 재폴링한다.
        await callTool("watcher_start");
        flashToast(t("toastWatcherStart"));
        for (let i = 0; i < 5; i++) {
          await delay(900);
          if (await refreshWatcher()) { flashToast(t("watcherOn")); break; }
        }
      }
    } catch (e) { console.error("[config-monitor] watcher toggle", e); await refreshWatcher(); }
    finally { btn.disabled = false; }
  };
  return running;
}

// ----- display mode -----
// MCP: 호스트 네이티브 requestDisplayMode. 브라우저(standalone): 네이티브 Fullscreen API.
// (localhost 직접 서빙이라 iframe 제약이 없어 requestFullscreen 이 동작한다.)
function fullscreenActive(): boolean {
  // standalone 은 항상 .app.fullscreen(100vh)이라 클래스로 판별 불가 -> 실제 브라우저 전체화면 상태로 판별.
  return STANDALONE ? !!document.fullscreenElement : $("app").classList.contains("fullscreen");
}
function syncFullscreenLabel(): void {
  const label = fullscreenActive() ? t("windowed") : t("fullscreen");
  $("fullscreen-label").textContent = label;
  ($("fullscreen") as HTMLButtonElement).title = label;
}
function applyDisplayMode(mode: string): void {
  $("app").classList.toggle("fullscreen", mode === "fullscreen");
  syncFullscreenLabel();
}
// 라이브 대시보드를 기본 브라우저에서 연다(서버 필요시 자동 기동). 브라우저에선 네이티브 전체화면 가능.
async function openInBrowser(): Promise<void> {
  flashToast(t("toastBrowser"));
  try { await callTool("open_in_browser"); flashToast(t("toastTabOpened")); }
  catch (e) { flashToast(t("toastOpenFail")); console.error("[config-monitor] open_in_browser", e); }
}
// 브라우저 전용 전체화면 토글(네이티브 Fullscreen API).
async function toggleBrowserFullscreen(): Promise<void> {
  try {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await document.documentElement.requestFullscreen();
  } catch (e) { console.error("[config-monitor] requestFullscreen", e); flashToast(t("displayModeFail")); }
}
// MCP 위젯 전체화면: 호스트에 requestDisplayMode 요청 -> 호스트가 iframe 을 키우면 .app.fullscreen(100vh)로 채움.
// (surface 별 지원 차이: 예) cowork=fullscreen 가능, code=inline 만 -> 후자는 조용히 inline 반환하여 무반응)
async function toggleFullscreen(): Promise<void> {
  const cur = (app.getHostContext() as any)?.displayMode || "inline";
  const next = cur === "fullscreen" ? "inline" : "fullscreen";
  try {
    const res: any = await app.requestDisplayMode({ mode: next as any });
    applyDisplayMode(res?.mode || next);
  } catch (e) {
    console.error("[config-monitor] requestDisplayMode", e);
    flashToast(t("displayModeFail"));
  }
}
// 전체화면 버튼은 항상 노출한다. 호스트가 fullscreen 모드를 지원하지 않으면 클릭 시 toast 로 안내.
function syncDisplayModeButton(): void {
  const ctx = app.getHostContext() as any;
  $("fullscreen").style.display = "";
  applyDisplayMode(ctx?.displayMode || "inline");
}

// ----- display settings popover (accent / 출처 표시 / desc 줄 수) -----
function wireSettings(): void {
  const btn = $("settings");
  const pop = $("setpop");
  const setOpen = (open: boolean) => {
    pop.hidden = !open;
    btn.classList.toggle("open", open);
    let bd = document.getElementById("setpop-backdrop");
    if (open && !bd) {
      bd = document.createElement("div");
      bd.id = "setpop-backdrop";
      bd.addEventListener("click", () => setOpen(false));
      document.body.appendChild(bd);
    } else if (!open && bd) bd.remove();
  };
  btn.addEventListener("click", () => setOpen(pop.hidden));
  pop.addEventListener("click", (e) => e.stopPropagation());
  document.querySelectorAll<HTMLElement>(".setpop .swatch").forEach((sw) => {
    sw.addEventListener("click", () => {
      document.documentElement.style.setProperty("--accent", sw.dataset.c!);
      document.querySelectorAll(".setpop .swatch").forEach((x) => x.classList.toggle("sel", x === sw));
    });
  });
  ($("opt-src") as HTMLInputElement).addEventListener("change", (e) =>
    document.body.classList.toggle("nosrc", !(e.target as HTMLInputElement).checked));
  const lines = $("opt-lines") as HTMLInputElement;
  lines.addEventListener("input", () => {
    document.documentElement.style.setProperty("--desc-lines", lines.value);
    $("opt-lines-val").textContent = lines.value + t("linesSuffix");
  });
}
wireSettings();

// ----- language toggle (ko/en) -----
// 정적 [data-i18n]/[data-i18n-title] 라벨 + 버튼/툴팁/슬라이더 접미사를 현재 lang 으로 적용.
// 동적 렌더(파일/설정/이력/토스트)는 각 렌더 함수가 t() 로 그리므로 여기서 다루지 않는다.
function applyLang(): void {
  document.documentElement.lang = lang;
  $("lang-code").textContent = lang.toUpperCase();
  ($("lang-toggle") as HTMLButtonElement).title = t("langTitle");
  document.querySelectorAll<HTMLElement>("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n!); });
  document.querySelectorAll<HTMLElement>("[data-i18n-title]").forEach((el) => { el.title = t(el.dataset.i18nTitle!); });
  // refresh() 밖에서 세팅되는 라벨은 여기서 직접 갱신: 전체화면 툴팁(모드 의존) / 설명 줄 수 접미사(값+접미사).
  syncFullscreenLabel();
  const linesEl = document.getElementById("opt-lines") as HTMLInputElement | null;
  if (linesEl) $("opt-lines-val").textContent = linesEl.value + t("linesSuffix");
  // sel-name/sel-path 는 파일 선택 시 파일명/경로(데이터)로 덮이므로, 미선택 상태의 안내문일 때만 번역.
  if (!selectedPath) {
    $("sel-name").textContent = t("selectFilePrompt");
    $("sel-path").textContent = t("selectFileHint");
  }
}

$("lang-toggle").addEventListener("click", () => {
  lang = lang === "ko" ? "en" : "ko";
  try { localStorage.setItem("cm.lang", lang); } catch { /* localStorage 차단 시 무시 */ }
  applyLang();
  // 동적 영역 재렌더 1회: 목록/설정/부제/watcher + (선택 시) 이력 패널.
  refresh();
  if (selectedPath && currentRevs.length) { renderHistory(); renderDiffFor(); }
});
applyLang(); // 초기 1회: 정적 라벨을 저장된 lang(기본 ko)으로 맞춘다.

// ----- wiring -----
$("collapse-all").addEventListener("click", () => {
  // 접기 가능(data-col) 섹션만 — 라이브러리 등록 UI 같은 헤더 토글 없는 블럭은 제외
  secTitles.forEach((t) => collapsed.add(t));
  document.querySelectorAll("#config .sec[data-col]").forEach((el) => el.classList.add("collapsed"));
});
$("refresh").addEventListener("click", () => { refresh(); flashToast(t("toastRefreshed")); });
$("snap").addEventListener("click", async () => {
  await callTool("snapshot_now", { message: t("snapshotMsg") });
  flashToast(t("toastSnapshot"));
  await refresh();
  if (selectedPath) selectFile(selectedPath);
});
$("report").addEventListener("click", async () => {
  flashToast(t("toastReport"));
  try { await callTool("open_report"); flashToast(t("toastReportOpened")); }
  catch (e) { flashToast(t("toastReportFail")); console.error("[config-monitor] report", e); }
});
$("panel-close").addEventListener("click", () => { detailOpen = false; applyDetailState(); });
$("panel-reopen").addEventListener("click", () => { detailOpen = true; applyDetailState(); });

if (STANDALONE) {
  // 이미 브라우저 안 -> 뷰포트 꽉 채움(고정 660px 대신 100vh). open-browser 는 불필요하므로 숨김.
  $("app").classList.add("fullscreen");
  $("open-browser").style.display = "none";
  // 브라우저에서도 전체화면 버튼 동작: 네이티브 Fullscreen API + 상태 변화 시 라벨 동기화.
  $("fullscreen").addEventListener("click", toggleBrowserFullscreen);
  document.addEventListener("fullscreenchange", syncFullscreenLabel);
  syncFullscreenLabel();
  refresh();
} else {
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("open-browser").addEventListener("click", openInBrowser);
  // 호스트 컨텍스트 변경(디스플레이 모드 등) 반영. connect 전에 등록.
  app.onhostcontextchanged = () => syncDisplayModeButton();
  app.connect()
    .then(() => { syncDisplayModeButton(); return refresh(); })
    .catch((e: unknown) => console.error("[config-monitor] connect failed", e));
}
