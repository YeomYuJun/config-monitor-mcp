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
const LABEL: Record<string, string> = { new: "신규", modified: "수정", deleted: "삭제", unchanged: "동일" };
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
    list.innerHTML = `<div class="empty">추적 파일 없음 - cas.py track 으로 추가</div>`;
  }
  for (const [p, st] of rows) {
    const row = document.createElement("div");
    row.className = "file" + (p === selectedPath ? " sel" : "");
    row.innerHTML =
      `<span class="fbadge ${st}">${LABEL[st] || st}</span>` +
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
  if (!collapsedInit) {
    for (const sec of sections) {
      if (/^(Skills|Agents|Scheduled|Desktop)/.test(sec.title)) collapsed.add(sec.title);
    }
    collapsedInit = true;
  }
  for (const sec of sections) {
    const isCol = collapsed.has(sec.title);
    const secEl = document.createElement("div");
    secEl.className = "sec" + (isCol ? " collapsed" : "");

    const head = document.createElement("div");
    head.className = "sechead";
    const srcHtml = sec.source
      ? `<div class="secsrc"><span class="lbl">출처</span><span class="val">${esc(sec.source)}</span></div>`
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
      body.innerHTML = `<div class="empty">항목 없음 / 파일 미발견</div>`;
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

// inline edit controls (perm/hook add/remove). edit meta is set by claude_config.py.
// removal uses inline confirm (window.confirm may be blocked in iframe sandbox).
function buildEditUI(edit: any): HTMLElement {
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
    x.title = "제거";
    x.addEventListener("click", () => {
      const ok = document.createElement("button");
      ok.className = "ok";
      ok.textContent = "삭제";
      const no = document.createElement("button");
      no.className = "no";
      no.textContent = "취소";
      chip.replaceChildren(txt, ok, no);
      no.addEventListener("click", () => wrap.replaceWith(buildEditUI(edit)));
      ok.addEventListener("click", async () => {
        ok.textContent = "…";
        try { await doRemove(it); flashToast("제거됨 · " + it); await refresh(); }
        catch (e) { ok.textContent = "실패"; console.error("[config-monitor] remove", e); }
      });
    });
    chip.append(txt, x);
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);

  const adder = document.createElement("div");
  adder.className = "adder";
  const input = document.createElement("input");
  input.placeholder = isPerm ? "예: Bash(npm run*)" : "hook 명령어";
  const add = document.createElement("button");
  add.className = "addbtn";
  add.textContent = "추가";
  const submit = async () => {
    const v = input.value.trim();
    if (!v) return;
    add.textContent = "…";
    try { await doAdd(v); flashToast("추가됨 · " + v); await refresh(); }
    catch (e) { add.textContent = "실패"; console.error("[config-monitor] add", e); }
  };
  add.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if ((e as KeyboardEvent).key === "Enter") submit(); });
  adder.append(input, add);
  wrap.appendChild(adder);
  return wrap;
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
  body.innerHTML = `<div class="empty">불러오는 중…</div>`;
  const h = jparseLast(await callTool("get_file_history", { path: p }));
  currentRevs = (h && h.revisions) || [];
  // 선택 행 다시 표시(refresh 없이 강조만)
  document.querySelectorAll(".file").forEach((el) => {
    const fn = el.querySelector(".fname")?.textContent || "";
    if (fn === basename(p)) el.classList.add("sel");
  });
  if (!currentRevs.length) {
    body.innerHTML = `<div class="empty">스냅샷 이력 없음 (먼저 스냅샷)</div>`;
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
  const opts = [`<option value="work">작업본 (현재 파일)</option>`]
    .concat(revsDesc.map((r) =>
      `<option value="${esc(r.snapshot)}">${esc(revTime(r).slice(5, 16))} · ${esc(r.message || "")}</option>`))
    .join("");
  cmp.innerHTML = `<span class="dlabel">비교 대상</span><select id="cmp-to">${opts}</select>`;
  body.appendChild(cmp);
  const sel = cmp.querySelector("#cmp-to") as HTMLSelectElement;
  sel.value = toRev;
  sel.addEventListener("change", () => { toRev = sel.value; renderDiffFor(); });

  // 타임라인
  const hl = document.createElement("div");
  hl.className = "dlabel";
  hl.textContent = "변경 이력";
  body.appendChild(hl);

  const tl = document.createElement("div");
  tl.className = "timeline";
  tl.innerHTML = `<div class="spine"></div>`;
  revsDesc.forEach((r) => {
    const item = document.createElement("div");
    item.className = "rev" + (r.snapshot === fromRev ? " sel" : "");
    item.innerHTML =
      `<span class="rdot"></span>` +
      `<div class="rbody"><div class="rmsg">${esc(r.message || "(no message)")}</div>` +
      `<div class="rmeta">${esc(revTime(r))} · ${esc(r.hash || "삭제됨")}</div></div>` +
      `<button class="rrestore" title="이 버전으로 파일 복원">복원</button>`;
    item.querySelector(".rbody")!.addEventListener("click", () => {
      fromRev = r.snapshot; renderHistory(); renderDiffFor();
    });
    item.querySelector(".rrestore")!.addEventListener("click", (e) => {
      e.stopPropagation(); inlineRestore(e.currentTarget as HTMLElement, r);
    });
    tl.appendChild(item);
  });
  body.appendChild(tl);

  const dl = document.createElement("div");
  dl.className = "dlabel";
  dl.style.marginTop = "24px";
  dl.textContent = "Diff";
  body.appendChild(dl);

  const diffArea = document.createElement("div");
  diffArea.id = "diff-area";
  body.appendChild(diffArea);
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
    if (area) area.innerHTML = `<div class="empty err">diff 조회 실패: ${esc(String(e))}</div>`;
  }
}

function renderDiff(diff: string): void {
  const area = document.getElementById("diff-area");
  if (!area) return;
  if (!diff.trim() || diff.trim() === "텍스트 변경 없음") {
    area.innerHTML = `<div class="diffempty">선택한 두 버전 사이에 텍스트 변경이 없습니다.</div>`;
    return;
  }
  const fromLabel = revLabel(fromRev);
  const toLabel = toRev === "work" ? "작업본" : revLabel(toRev);
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
  box.innerHTML = `<button class="ok" title="현재 상태는 자동 스냅샷+백업">복원확정</button><button class="no">취소</button>`;
  btn.replaceWith(box);
  box.querySelector(".no")!.addEventListener("click", (e) => { e.stopPropagation(); renderHistory(); });
  box.querySelector(".ok")!.addEventListener("click", async (e) => {
    e.stopPropagation();
    box.innerHTML = `<span class="rmeta">복원 중…</span>`;
    try {
      const res = jparse(await callTool("config_restore", { path: selectedPath, from: r.snapshot }));
      if (res && res.ok) { flashToast("복원됨 · 현재 상태 자동 백업"); await refresh(); await selectFile(selectedPath); }
      else box.innerHTML = `<span class="rmeta err">실패: ${esc(res?.message || "알 수 없음")}</span>`;
    } catch (err) {
      box.innerHTML = `<span class="rmeta err">실패: ${esc(String(err))}</span>`;
    }
  });
}

// ----- detail panel open/close -----
function applyDetailState(): void {
  $("detail").classList.toggle("closed", !detailOpen);
}

// ----- load / refresh -----
function showErr(hostId: string, label: string, e: unknown): void {
  console.error(`[config-monitor] ${label} 실패`, e);
  $(hostId).innerHTML = `<div class="empty err">${esc(label)} 조회 실패: ${esc(String(e))}</div>`;
}

async function refresh(): Promise<void> {
  $("config").innerHTML = `<div class="empty">불러오는 중…</div>`;
  $("tracked").innerHTML = `<div class="empty">불러오는 중…</div>`;
  let trackedCount = 0;
  try {
    const trk = jparseLast(await callTool("get_tracked"));
    if (trk) trackedCount = renderTracked(trk);
    else $("tracked").innerHTML = `<div class="empty">상태를 불러오지 못했습니다 (빈 응답)</div>`;
  } catch (e) {
    showErr("tracked", "추적 상태", e);
  }
  try {
    const cfg = jparse(await callTool("get_config"));
    if (cfg) {
      renderConfig(cfg.sections || []);
      $("config-hint").textContent = `${(cfg.sections || []).length} 카테고리 · 출처 경로 포함`;
    } else $("config").innerHTML = `<div class="empty">설정을 불러오지 못했습니다 (빈 응답)</div>`;
  } catch (e) {
    showErr("config", "설정", e);
  }
  const now = new Date().toTimeString().slice(0, 8);
  $("subtitle").textContent = `추적 ${trackedCount}개 · 갱신 ${now}`;
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
    label.textContent = running ? "watcher 실행 중" : "watcher 정지";
    btn.title = running
      ? `pid ${st.pid} · ${(st.dirs || []).length} dirs · ${Math.round(st.age_sec || 0)}s 전`
      : (st?.reason || "정지됨");
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
        flashToast("watcher 중지");
        await refreshWatcher();
      } else {
        // watcher.ps1 가 spawn 후 heartbeat 를 쓰기까지 1~2s 걸린다.
        // 시작 직후 status 는 아직 '정지'이므로 잠시 기다렸다가 재폴링한다.
        await callTool("watcher_start");
        flashToast("watcher 시작 중…");
        for (let i = 0; i < 5; i++) {
          await delay(900);
          if (await refreshWatcher()) { flashToast("watcher 실행 중"); break; }
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
  const label = full ? "창 모드" : "전체화면";
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
    flashToast("디스플레이 모드 변경 불가(호스트 미지원)");
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

// ----- wiring -----
$("refresh").addEventListener("click", () => { refresh(); flashToast("새로고침"); });
$("snap").addEventListener("click", async () => {
  await callTool("snapshot_now", { message: "from dashboard" });
  flashToast("스냅샷 생성됨");
  await refresh();
  if (selectedPath) selectFile(selectedPath);
});
$("report").addEventListener("click", async () => {
  flashToast("리포트 생성 중…");
  try { await callTool("open_report"); flashToast("브라우저에서 리포트 열림"); }
  catch (e) { flashToast("리포트 실패"); console.error("[config-monitor] report", e); }
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
    flashToast("브라우저에서 여는 중…");
    try { await callTool("open_in_browser"); flashToast("브라우저 탭 열림"); }
    catch (e) { flashToast("열기 실패"); console.error("[config-monitor] open_in_browser", e); }
  });
  // 호스트 컨텍스트 변경(디스플레이 모드 등) 반영. connect 전에 등록.
  app.onhostcontextchanged = () => syncDisplayModeButton();
  app.connect()
    .then(() => { syncDisplayModeButton(); return refresh(); })
    .catch((e: unknown) => console.error("[config-monitor] connect failed", e));
}
