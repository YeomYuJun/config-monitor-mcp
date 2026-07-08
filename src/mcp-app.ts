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
  },
  en: {
    newFile: "New", modified: "Modified", deleted: "Deleted", unchanged: "Same",
    snapshot: "Snapshot", refresh: "Refresh", report: "Report", openBrowser: "Open in browser",
    dispSettings: "Display settings", accentColor: "Accent color", showSources: "Show source paths", descLines: "Description lines", linesSuffix: " lines",
    fullscreen: "Fullscreen", windowed: "Exit fullscreen",
    watcherOn: "watcher running", watcherOff: "watcher stopped",
    trackedFiles: "Tracked files", clickHint: "click → history / diff",
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

let toastT: number | undefined;
function flashToast(msg: string): void {
  const el = $("toast");
  el.textContent = msg;
  el.style.display = "block";
  if (toastT) clearTimeout(toastT);
  toastT = window.setTimeout(() => { el.style.display = "none"; }, 1900);
}

// ----- tracked file rows -----
// 파일 상태 배지 라벨(현재 lang 반영). new/deleted 는 핸드오프 사전에 없어 newFile/deleted 키를 추가했다.
const statusLabel = (st: string): string =>
  (({ new: t("newFile"), modified: t("modified"), deleted: t("deleted"), unchanged: t("unchanged") } as Record<string, string>)[st] || st);
function renderTracked(status: any): number {
  const host = $("tracked");
  host.innerHTML = "";
  const list = document.createElement("div");
  list.className = "files";
  const rows: [string, string][] = [];
  for (const st of ["modified", "new", "deleted", "unchanged"]) {
    for (const p of status[st] || []) rows.push([p, st]);
  }
  if (!rows.length) {
    list.innerHTML = `<div class="empty">${esc(t("emptyTracked"))}</div>`;
  }
  for (const [p, st] of rows) {
    const row = document.createElement("div");
    row.className = "file" + (p === selectedPath ? " sel" : "");
    row.innerHTML =
      `<span class="fbadge ${st}">${esc(statusLabel(st))}</span>` +
      `<div class="fmeta"><div class="fname">${esc(basename(p))}</div>` +
      `<div class="fdir">${esc(dirname(p))}</div></div>` +
      `<span class="chev">›</span>`;
    row.addEventListener("click", () => selectFile(p));
    list.appendChild(row);
  }
  host.appendChild(list);
  return rows.length;
}

// ----- config sections (collapsible, with source) -----
function renderConfig(sections: any[]): void {
  const host = $("config");
  host.innerHTML = "";
  secTitles.clear();
  if (!collapsedInit) {
    for (const sec of sections) {
      if (/^(Skills|Agents|Scheduled|Desktop)/.test(sec.title)) collapsed.add(sec.title);
    }
    collapsedInit = true;
  }
  for (const sec of sections) {
    secTitles.add(sec.title);
    const isCol = collapsed.has(sec.title);
    const secEl = document.createElement("div");
    secEl.className = "sec" + (isCol ? " collapsed" : "");
    secEl.dataset.col = "1";

    const head = document.createElement("div");
    head.className = "sechead";
    const srcHtml = sec.source
      ? `<div class="secsrc"><span class="lbl">${esc(t("source"))}</span><span class="val">${esc(sec.source)}</span></div>`
      : "";
    head.innerHTML =
      `<div class="secrow"><span class="chev2">▾</span>` +
      `<span class="sectitle">${esc(sec.title)}</span>` +
      `<span class="seccount">${(sec.cards || []).length}</span></div>` + srcHtml;
    head.addEventListener("click", () => {
      if (collapsed.has(sec.title)) collapsed.delete(sec.title); else collapsed.add(sec.title);
      secEl.classList.toggle("collapsed");
    });
    secEl.appendChild(head);

    const body = document.createElement("div");
    body.className = "secbody";
    if (!(sec.cards || []).length) {
      body.innerHTML = `<div class="empty">${esc(t("emptyCards"))}</div>`;
    }
    for (const c of sec.cards || []) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML =
        `<div class="cname"><span class="nm">${esc(c.name)}</span>` +
        (c.badge ? `<span class="badge ${c.ok ? "ok" : ""}">${esc(c.badge)}</span>` : "") +
        `</div>` +
        (c.kv || [])
          .map(([k, v]: [string, string]) =>
            `<div class="kv"><span class="k">${esc(k)}</span>` +
            `<span class="v ${valClass(k)}">${esc(v)}</span></div>`)
          .join("");
      if (c.edit) card.appendChild(buildEditUI(c.edit));
      body.appendChild(card);
    }
    secEl.appendChild(body);
    host.appendChild(secEl);
  }
}

// inline edit controls. edit meta is set by claude_config.py.
//   perm/hook            : 항목 chips + adder (기존)
//   mcp/skill/agent      : 카드 자체 제거 버튼 (인라인 확인)
//   mcp-add/skill-add/agent-add : adder 만 (입력 파싱 후 해당 add 도구 호출)
// removal uses inline confirm (window.confirm may be blocked in iframe sandbox).
function buildEditUI(edit: any): HTMLElement {
  if (["mcp", "skill", "agent"].includes(edit.kind)) return buildRemoveUI(edit);
  if (["mcp-add", "skill-add", "agent-add"].includes(edit.kind)) return buildAddUI(edit);
  const isPerm = edit.kind === "perm";
  const doRemove = (it: string) =>
    isPerm
      ? callTool("config_perm_remove", { kind: edit.permKind, rule: it })
      : callTool("config_hook_remove", { event: edit.event, needle: it });
  const doAdd = (v: string) =>
    isPerm
      ? callTool("config_perm_add", { kind: edit.permKind, rule: v })
      : callTool("config_hook_add", { event: edit.event, command: v });

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

function renderLibrary(host: HTMLElement, res: any): void {
  const secEl = document.createElement("div");
  secEl.className = "sec" + (collapsed.has("Library") ? " collapsed" : "");
  secEl.dataset.col = "1";
  secTitles.add("Library");
  const libs = (res && res.libraries) || [];
  const items: any[] = [];
  for (const l of libs) {
    for (const [cat, arr] of Object.entries(l.categories || {})) {
      for (const it of arr as any[]) items.push({ ...it, category: cat, lib: l.lib });
    }
  }
  const head = document.createElement("div");
  head.className = "sechead";
  head.innerHTML =
    `<div class="secrow"><span class="chev2">▾</span>` +
    `<span class="sectitle">${esc(t("libSectionTitle"))}</span>` +
    `<span class="seccount">${items.length}</span></div>` +
    (libs.length ? `<div class="secsrc"><span class="lbl">${esc(t("source"))}</span><span class="val">${esc(libs.map((l: any) => l.lib).join(" · "))}</span></div>` : "");
  head.addEventListener("click", () => {
    if (collapsed.has("Library")) collapsed.delete("Library"); else collapsed.add("Library");
    secEl.classList.toggle("collapsed");
  });
  secEl.appendChild(head);

  const body = document.createElement("div");
  body.className = "secbody";
  if (!items.length) body.innerHTML = `<div class="empty">${esc(t("libEmpty"))}</div>`;
  for (const it of items) {
    const [label, cls] = libStatus(it.status);
    const row = document.createElement("div");
    row.className = "card";
    row.innerHTML =
      `<div class="cname"><span class="nm">${esc(it.category)} / ${esc(it.name)}</span>` +
      `<span class="badge ${cls === "ok" ? "ok" : ""}">${esc(label)}${it.kit_ref ? " · " + esc(t("kitRef")) : ""}</span></div>`;
    const act = document.createElement("div");
    act.className = "edit";
    const mkBtn = (txt: string, tool: string, confirmTxt?: string) => {
      const b = document.createElement("button");
      b.className = "addbtn";
      b.textContent = txt;
      b.addEventListener("click", async () => {
        if (confirmTxt && b.textContent !== confirmTxt) { b.textContent = confirmTxt; return; } // 2-click 확인
        b.textContent = "…";
        try {
          const r = jparse(await callTool(tool, { category: it.category, name: it.name }));
          if (r && r.ok === false) { flashToast(r.message || t("failed")); b.textContent = t("failed"); return; }
          flashToast(`${txt} ${t("done")} · ${it.name}`);
          await refresh();
        } catch (e) { b.textContent = t("failed"); console.error("[config-monitor] library", e); }
      });
      return b;
    };
    if (it.status === "not_installed") act.appendChild(mkBtn(t("libInstall"), "library_install"));
    if (it.status === "modified") act.appendChild(mkBtn(t("libSync"), "library_install", t("libSyncConfirm")));
    if (it.status !== "not_installed") act.appendChild(mkBtn(t("remove"), "library_uninstall", t("libUninstallConfirm")));
    row.appendChild(act);
    body.appendChild(row);
  }
  secEl.appendChild(body);
  host.appendChild(secEl);
}

async function refreshLibrary(): Promise<void> {
  const host = $("config");
  try {
    const res = jparse(await callTool("library_scan"));
    // 라이브러리 미설정(빈 목록)은 정상 상태 — 섹션 대신 등록 UI 로 진행
    if (res && res.ok !== false && (res.libraries || []).length) { renderLibrary(host, res); return; }
  } catch { /* 오류 -> 등록 UI */ }
  // 등록 UI: 라이브러리 경로 입력 1회
  const secEl = document.createElement("div");
  secEl.className = "sec";
  secEl.innerHTML =
    `<div class="sechead"><div class="secrow"><span class="chev2">▾</span>` +
    `<span class="sectitle">${esc(t("libSectionTitle"))}</span><span class="seccount">${esc(t("libUnregistered"))}</span></div></div>`;
  const body = document.createElement("div");
  body.className = "secbody";
  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = t("libPathPlaceholder");
  const btn = document.createElement("button");
  btn.className = "addbtn";
  btn.textContent = t("libRegister");
  btn.addEventListener("click", async () => {
    const v = input.value.trim();
    if (!v) return;
    btn.textContent = "…";
    try { await callTool("library_scan", { lib: v }); flashToast(t("libRegistered")); await refresh(); }
    catch (e) { btn.textContent = t("failed"); console.error("[config-monitor] lib register", e); }
  });
  adder.append(input, btn);
  body.appendChild(adder);
  secEl.appendChild(body);
  host.appendChild(secEl);
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
  // 선택 행 다시 표시(refresh 없이 강조만)
  document.querySelectorAll(".file").forEach((el) => {
    const fn = el.querySelector(".fname")?.textContent || "";
    if (fn === basename(p)) el.classList.add("sel");
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

  const dl = document.createElement("div");
  dl.className = "dlabel";
  dl.style.marginTop = "24px";
  dl.textContent = "Diff";
  body.appendChild(dl);

  const diffArea = document.createElement("div");
  diffArea.id = "diff-area";
  body.appendChild(diffArea);

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
  const lines = diff.split("\n").map((line) => {
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
    const cfg = jparse(await callTool("get_config"));
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

// ----- display mode (MCP 네이티브 fullscreen; 브라우저 requestFullscreen 은 iframe 에서 막힘) -----
function applyDisplayMode(mode: string): void {
  const full = mode === "fullscreen";
  $("app").classList.toggle("fullscreen", full);
  const label = full ? t("windowed") : t("fullscreen");
  $("fullscreen-label").textContent = label;
  ($("fullscreen") as HTMLButtonElement).title = label;   // 아이콘 버튼 툴팁
}
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
// 호스트가 지원하는 모드에 fullscreen 이 없으면 버튼 숨김.
function syncDisplayModeButton(): void {
  const ctx = app.getHostContext() as any;
  const modes: string[] = ctx?.availableDisplayModes || [];
  const btn = $("fullscreen");
  if (modes.length && !modes.includes("fullscreen")) btn.style.display = "none";
  else btn.style.display = "";
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
  // refresh() 밖에서 세팅되는 두 라벨은 여기서 직접 갱신:
  //   전체화면 툴팁(모드 의존), 설명 줄 수 접미사(값+접미사).
  const full = $("app").classList.contains("fullscreen");
  const fsLabel = full ? t("windowed") : t("fullscreen");
  $("fullscreen-label").textContent = fsLabel;
  ($("fullscreen") as HTMLButtonElement).title = fsLabel;
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
  // 이미 브라우저 안 -> 뷰포트 꽉 채우고(고정 660px 대신 100vh), MCP 전용 버튼 숨김, 브리지 없이 바로 로드.
  $("app").classList.add("fullscreen");
  $("fullscreen").style.display = "none";
  $("open-browser").style.display = "none";
  refresh();
} else {
  $("fullscreen").addEventListener("click", toggleFullscreen);
  $("open-browser").addEventListener("click", async () => {
    flashToast(t("toastBrowser"));
    try { await callTool("open_in_browser"); flashToast(t("toastTabOpened")); }
    catch (e) { flashToast(t("toastOpenFail")); console.error("[config-monitor] open_in_browser", e); }
  });
  // 호스트 컨텍스트 변경(디스플레이 모드 등) 반영. connect 전에 등록.
  app.onhostcontextchanged = () => syncDisplayModeButton();
  app.connect()
    .then(() => { syncDisplayModeButton(); return refresh(); })
    .catch((e: unknown) => console.error("[config-monitor] connect failed", e));
}
