// mcp-tools.ts - config-monitor 의 모든 도구 정의 + 등록 (stdio / http 공용).
//
// 설계: 검증된 Python 레이어(cas.py / claude_config.py / config_edit.py)를 child_process 로
//       shell-out 해 재사용. buildTools() 가 도구 정의를 만들고, MCP(registerAll) 와
//       HTTP REST(server.ts) 가 동일한 핸들러를 공유한다.
import { z } from "zod";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import fs from "node:fs/promises";
import {
  registerAppTool,
  registerAppResource,
  RESOURCE_MIME_TYPE,
} from "@modelcontextprotocol/ext-apps/server";

const pexec = promisify(execFile);
export const RESOURCE_URI = "ui://config-monitor/dashboard.html";

const READ = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const WRITE = { readOnlyHint: false, destructiveHint: false, idempotentHint: false, openWorldHint: false };
const EDIT = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: false };

type ToolResult = { content: { type: "text"; text: string }[]; structuredContent?: Record<string, unknown> };
const text = (t: string): ToolResult => ({ content: [{ type: "text", text: t }] });
function jsonResult(t: string): ToolResult {
  let parsed: unknown;
  try { parsed = JSON.parse(t); } catch { parsed = undefined; }
  return {
    content: [{ type: "text", text: t }],
    ...(parsed !== undefined ? { structuredContent: parsed as Record<string, unknown> } : {}),
  };
}

export interface ToolDef {
  name: string;
  meta: { title: string; description: string; inputSchema: any; annotations: any };
  run: (args: any) => Promise<ToolResult>;
}

// 모든 데이터/액션 도구 정의(show_config_monitor 같은 UI 전용 도구는 제외).
export function buildTools(scriptDir: string): ToolDef[] {
  const PY = process.env.CONFIG_MONITOR_PYTHON || "python";
  // Windows 콘솔 기본 인코딩(cp949)은 한글/em-dash 출력 시 UnicodeEncodeError 로 Python 을 죽인다.
  // 자식 stdout/stderr 를 UTF-8 로 강제(PYTHONUTF8=1, PYTHONIOENCODING=utf-8).
  const PY_ENV = { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  // watcher.ps1 기본값과 동일한 스토어를 가리키도록.
  const STORE = process.env.CLAUDE_SNAPSHOT_STORE ||
    (process.platform === "win32" ? "D:\\.claude-snapshot" : path.join(process.env.HOME || "", ".claude-snapshot"));
  const runPy = async (script: string, args: string[]): Promise<string> => {
    const { stdout } = await pexec(PY, [path.join(scriptDir, script), ...args], {
      env: PY_ENV,
      maxBuffer: 16 * 1024 * 1024,
    });
    return stdout;
  };

  return [
    // ----- 읽기 -----
    {
      name: "get_config",
      meta: {
        title: "Get Claude Config",
        description: "Claude 설정(MCP/hooks/skills/agents/scheduled/permissions/desktop-skills)을 정규화된 sections JSON 으로 반환",
        inputSchema: z.object({}), annotations: READ,
      },
      run: async () => jsonResult(await runPy("claude_config.py", ["dump"])),
    },
    {
      name: "get_tracked",
      meta: {
        title: "Get Tracked File Status",
        description: "스냅샷 추적 파일들의 변경 상태(new/modified/deleted/unchanged) JSON",
        inputSchema: z.object({}), annotations: READ,
      },
      run: async () => jsonResult(await runPy("cas.py", ["status", "--json"])),
    },
    {
      name: "config_track",
      meta: {
        title: "Track Config Path",
        description: "설정 파일/폴더를 스냅샷 추적 대상에 추가(감시 전용). 프로젝트 폴더나 .claude 를 주면 " +
          "settings.json + settings.local.json 을 자동 감지, 파일 경로면 그 파일만. 편집은 하지 않음",
        inputSchema: z.object({
          path: z.string().describe("프로젝트 폴더 / .claude 폴더 / 설정 파일의 절대경로"),
        }), annotations: WRITE,
      },
      run: async (a: { path: string }) => jsonResult(await runPy("cas.py", ["track", "--json", a.path])),
    },
    {
      name: "config_untrack",
      meta: {
        title: "Untrack Config Path",
        description: "추적 목록에서 파일을 제거(파일 자체는 그대로). 프로젝트 추적 행 제거용",
        inputSchema: z.object({
          path: z.string().describe("추적 중인 파일의 절대경로(행에 표시된 경로)"),
        }), annotations: WRITE,
      },
      run: async (a: { path: string }) => jsonResult(await runPy("cas.py", ["untrack", "--json", a.path])),
    },
    {
      name: "get_file_history",
      meta: {
        title: "Get File History",
        description: "특정 파일의 스냅샷 리비전 이력(git log 스타일). 내용이 바뀐 스냅샷만 추림",
        inputSchema: z.object({ path: z.string().describe("추적 중인 파일의 절대경로") }), annotations: READ,
      },
      run: async (a: { path: string }) => jsonResult(await runPy("cas.py", ["history", a.path, "--json"])),
    },
    {
      name: "get_file_content",
      meta: {
        title: "Get Current File Content",
        description: "추적 중인 설정 파일의 현재 내용을 그대로 반환(읽기 전용 뷰어). 추적 목록 밖 경로는 거부",
        inputSchema: z.object({ path: z.string().describe("추적 중인 파일의 절대경로") }), annotations: READ,
      },
      run: async (a: { path: string }) => text(await runPy("cas.py", ["cat", a.path])),
    },
    {
      name: "get_diff",
      meta: {
        title: "Get File Diff",
        description: "파일의 두 리비전(또는 스냅샷 vs 현재) 간 unified diff. from/to 미지정 시 최신 스냅샷 vs 작업본",
        inputSchema: z.object({
          path: z.string(),
          from: z.string().optional().describe("스냅샷 id (생략 시 최신 스냅샷)"),
          to: z.string().optional().describe("스냅샷 id 또는 'work'(기본=현재 파일)"),
        }), annotations: READ,
      },
      run: async (a: { path: string; from?: string; to?: string }) => {
        const args = ["diff", a.path];
        if (a.from) args.push("--from", a.from);
        if (a.to) args.push("--to", a.to);
        return text(await runPy("cas.py", args));
      },
    },
    {
      name: "snapshot_now",
      meta: {
        title: "Snapshot Now",
        description: "추적 파일 현재 상태로 스냅샷 1개 생성",
        inputSchema: z.object({ message: z.string().optional() }), annotations: WRITE,
      },
      run: async (a: { message?: string }) => text(await runPy("cas.py", ["snapshot", "-m", a.message || "manual"])),
    },
    {
      name: "config_restore",
      meta: {
        title: "Restore File From Snapshot",
        description: "추적 파일을 특정 스냅샷 버전으로 복원. 복원 전 현재 상태를 스냅샷(되돌림 지점) + .bak 백업",
        inputSchema: z.object({
          path: z.string().describe("복원할 추적 파일의 절대경로"),
          from: z.string().describe("복원할 스냅샷 id"),
        }), annotations: EDIT,
      },
      run: async (a: { path: string; from: string }) =>
        jsonResult(await runPy("cas.py", ["restore", a.path, "--from", a.from])),
    },

    // ----- watcher 제어 -----
    {
      name: "watcher_status",
      meta: {
        title: "Watcher Status",
        description: "상주 watcher(FileSystemWatcher 자동 스냅샷)의 실행 여부/heartbeat 조회",
        inputSchema: z.object({}), annotations: READ,
      },
      run: async () => jsonResult(await runPy("cas.py", ["watcher-status"])),
    },
    {
      name: "watcher_start",
      meta: {
        title: "Start Watcher",
        description: "watcher.ps1 을 백그라운드로 기동(추적 디렉토리 변경 시 자동 스냅샷). 이미 실행 중이면 no-op",
        inputSchema: z.object({}), annotations: WRITE,
      },
      run: async () => {
        const cur = JSON.parse((await runPy("cas.py", ["watcher-status"]).catch(() => "{}")) || "{}");
        if (cur.running) return jsonResult(JSON.stringify({ ok: true, message: "이미 실행 중", pid: cur.pid, changed: false }));
        // 좀비/중복 watcher 정리(상태 신뢰성과 무관하게 단일 인스턴스 보장) 후 새로 기동.
        await killWatchers();
        await fs.rm(path.join(STORE, "watcher.json"), { force: true }).catch(() => {});
        // Node 가 powershell 을 직접 detached spawn 하면 (1) 인자 백슬래시가 먹혀 -File 경로가 깨지고
        // (2) detached 자식이 즉사한다. 검증 패턴: 일회성 powershell 이 Start-Process 로 독립 기동.
        const ps1 = path.join(scriptDir, "watcher.ps1").replace(/\\/g, "/");
        const storeFwd = STORE.replace(/\\/g, "/");
        const cmd = "Start-Process powershell -WindowStyle Hidden -ArgumentList @(" +
          `'-NoProfile','-ExecutionPolicy','Bypass','-File','${ps1}','-Store','${storeFwd}','-Python','${PY}')`;
        spawn("powershell.exe", ["-NoProfile", "-Command", cmd], { stdio: "ignore", windowsHide: true, env: PY_ENV });
        // heartbeat 가 쓰일 때까지 폴링(최대 ~5.6s) 후 실제 상태를 반환(허위 ok 방지).
        let st: any = {};
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 700));
          st = JSON.parse((await runPy("cas.py", ["watcher-status"]).catch(() => "{}")) || "{}");
          if (st.running) break;
        }
        return jsonResult(JSON.stringify({
          ok: !!st.running,
          message: st.running ? "watcher 기동됨" : "watcher 기동 실패(watcher.json 미갱신 - 권한/PATH 확인)",
          pid: st.pid, changed: true,
        }));
      },
    },
    {
      name: "watcher_stop",
      meta: {
        title: "Stop Watcher",
        description: "실행 중인 watcher 프로세스를 종료(pid 기준)",
        inputSchema: z.object({}), annotations: WRITE,
      },
      run: async () => {
        // pid 하나가 아니라 모든 watcher.ps1 을 종료(좀비 누적 정리).
        await killWatchers();
        await fs.rm(path.join(STORE, "watcher.json"), { force: true }).catch(() => {});
        return jsonResult(JSON.stringify({ ok: true, message: "watcher 종료", changed: true }));
      },
    },

    // ----- 편집 (스냅샷-선행 + .bak) -----
    {
      name: "config_perm_add",
      meta: {
        title: "Add Permission Rule",
        description: "settings.json permissions.<allow|deny|ask> 에 규칙 추가 (편집 전 스냅샷+백업)",
        inputSchema: z.object({ kind: z.enum(["allow", "deny", "ask"]), rule: z.string() }), annotations: EDIT,
      },
      run: async (a: { kind: string; rule: string }) => jsonResult(await runPy("config_edit.py", ["perm-add", a.kind, a.rule])),
    },
    {
      name: "config_perm_remove",
      meta: {
        title: "Remove Permission Rule",
        description: "settings.json permissions 에서 규칙 제거",
        inputSchema: z.object({ kind: z.enum(["allow", "deny", "ask"]), rule: z.string() }), annotations: EDIT,
      },
      run: async (a: { kind: string; rule: string }) => jsonResult(await runPy("config_edit.py", ["perm-remove", a.kind, a.rule])),
    },
    {
      name: "config_hook_add",
      meta: {
        title: "Add Hook",
        description: "settings.json hooks.<event> 에 command hook 추가",
        inputSchema: z.object({ event: z.string(), command: z.string(), matcher: z.string().optional() }), annotations: EDIT,
      },
      run: async (a: { event: string; command: string; matcher?: string }) => {
        const args = ["hook-add", a.event, a.command];
        if (a.matcher) args.push("--matcher", a.matcher);
        return jsonResult(await runPy("config_edit.py", args));
      },
    },
    {
      name: "config_hook_remove",
      meta: {
        title: "Remove Hook",
        description: "settings.json hooks.<event> 에서 command substring 매칭 항목 제거",
        inputSchema: z.object({ event: z.string(), needle: z.string() }), annotations: EDIT,
      },
      run: async (a: { event: string; needle: string }) => jsonResult(await runPy("config_edit.py", ["hook-remove", a.event, a.needle])),
    },
    {
      name: "skill_scaffold",
      meta: {
        title: "Scaffold Code Skill",
        description: "~/.claude/skills/<name>/SKILL.md 스캐폴드 생성",
        inputSchema: z.object({
          name: z.string(), desc: z.string().optional(),
          content: z.string().optional().describe("SKILL.md 전체 내용(frontmatter 포함). 지정 시 스텁 대신 그대로 설치"),
        }), annotations: EDIT,
      },
      run: async (a: { name: string; desc?: string; content?: string }) => {
        const args = ["skill-scaffold", a.name];
        if (a.desc) args.push("--desc", a.desc);
        if (a.content) args.push("--content", a.content);
        return jsonResult(await runPy("config_edit.py", args));
      },
    },
    {
      name: "config_skill_remove",
      meta: {
        title: "Remove Code Skill",
        description: "~/.claude/skills/<name> 을 .trash 로 이동(복구 가능). 편집 전 스냅샷",
        inputSchema: z.object({ name: z.string() }), annotations: EDIT,
      },
      run: async (a: { name: string }) => jsonResult(await runPy("config_edit.py", ["skill-remove", a.name])),
    },
    {
      name: "config_agent_add",
      meta: {
        title: "Scaffold Agent",
        description: "~/.claude/agents/<name>.md 에이전트 생성. content 로 전체 정의(frontmatter 포함) 설치 가능, 없으면 desc/tools/model 스캐폴드",
        inputSchema: z.object({
          name: z.string(), desc: z.string().optional(),
          tools: z.string().optional(), model: z.string().optional(),
          content: z.string().optional().describe("에이전트 md 전체 내용(frontmatter 포함). 지정 시 desc/tools/model 무시"),
        }), annotations: EDIT,
      },
      run: async (a: { name: string; desc?: string; tools?: string; model?: string; content?: string }) => {
        const args = ["agent-scaffold", a.name];
        if (a.desc) args.push("--desc", a.desc);
        if (a.tools) args.push("--tools", a.tools);
        if (a.model) args.push("--model", a.model);
        if (a.content) args.push("--content", a.content);
        return jsonResult(await runPy("config_edit.py", args));
      },
    },
    {
      name: "config_agent_remove",
      meta: {
        title: "Remove Agent",
        description: "~/.claude/agents/<name>(.md) 을 .trash 로 이동(복구 가능). 편집 전 스냅샷",
        inputSchema: z.object({ name: z.string() }), annotations: EDIT,
      },
      run: async (a: { name: string }) => jsonResult(await runPy("config_edit.py", ["agent-remove", a.name])),
    },
    {
      name: "config_mcp_add",
      meta: {
        title: "Add/Update MCP Server",
        description: "mcpServers.<name> 추가/갱신. scope=user 는 ~/.claude.json, scope=desktop 은 claude_desktop_config.json(적용은 Desktop 재시작 필요). 스냅샷+.bak+atomic",
        inputSchema: z.object({
          name: z.string(),
          serverJson: z.string().describe('서버 설정 JSON 문자열, 예: {"command":"npx","args":["-y","some-mcp"]}'),
          scope: z.enum(["user", "desktop"]).optional(),
        }), annotations: EDIT,
      },
      run: async (a: { name: string; serverJson: string; scope?: string }) =>
        jsonResult(await runPy("config_edit.py",
          ["mcp-add", a.name, "--json", a.serverJson, "--scope", a.scope || "user"])),
    },
    {
      name: "config_mcp_remove",
      meta: {
        title: "Remove MCP Server",
        description: "mcpServers.<name> 제거. scope=user|desktop (desktop 적용은 재시작 필요). 스냅샷+.bak+atomic",
        inputSchema: z.object({ name: z.string(), scope: z.enum(["user", "desktop"]).optional() }), annotations: EDIT,
      },
      run: async (a: { name: string; scope?: string }) =>
        jsonResult(await runPy("config_edit.py", ["mcp-remove", a.name, "--scope", a.scope || "user"])),
    },

    // ----- 라이브러리 토글 (.claude 구조 라이브러리 디렉토리) -----
    {
      name: "library_scan",
      meta: {
        title: "Scan Personal Library",
        description: "라이브러리(.claude 구조, CLAUDE_CONFIG_LIBRARIES env 또는 등록분)의 agents/skills/commands 를 열거하고 라이브 설정과 해시 비교해 3상태(not_installed/installed/modified) 반환. lib 지정 시 신규 등록 후 스캔",
        inputSchema: z.object({ lib: z.string().optional().describe("라이브러리 루트 경로(.claude 구조 디렉토리). 최초 1회 등록용") }),
        annotations: READ,
      },
      run: async (a: { lib?: string }) => {
        const args = ["scan"];
        if (a.lib) args.push("--lib", a.lib);
        return jsonResult(await runPy("library.py", args));
      },
    },
    {
      name: "library_install",
      meta: {
        title: "Install Library Item",
        description: "라이브러리 항목을 ~/.claude 에 설치/동기화. skills 는 그룹 중첩(가변 깊이) 가능하며 leaf 이름으로 평탄 설치(예: path=2-stack/java-spring/error-handling -> ~/.claude/skills/error-handling). 기존 항목 존재 시 스냅샷+.bak(파일)/.trash(디렉토리) 후 덮어씀",
        inputSchema: z.object({
          category: z.enum(["agents", "skills", "commands"]),
          path: z.string().describe("카테고리 루트 기준 상대경로. skills 는 그룹 포함 가능, agents/commands 는 이름"),
          lib: z.string().optional(),
          target: z.string().optional().describe("설치 대상 루트(기본 ~/.claude). 프로젝트에 설치하려면 그 프로젝트의 .claude 경로"),
        }), annotations: EDIT,
      },
      run: async (a: { category: string; path: string; lib?: string; target?: string }) => {
        // --target 은 부모 파서 옵션이라 subcommand 앞에 와야 argparse 가 인식(--lib 는 install 서브파서 옵션).
        const args = a.target ? ["--target", a.target] : [];
        args.push("install", a.category, a.path);
        if (a.lib) args.push("--lib", a.lib);
        return jsonResult(await runPy("library.py", args));
      },
    },
    {
      name: "library_uninstall",
      meta: {
        title: "Uninstall Library Item",
        description: "~/.claude 의 해당 항목을 .trash 로 이동(복구 가능). 라이브러리 원본은 건드리지 않음",
        inputSchema: z.object({
          category: z.enum(["agents", "skills", "commands"]),
          name: z.string(),
        }), annotations: EDIT,
      },
      run: async (a: { category: string; name: string }) =>
        jsonResult(await runPy("library.py", ["uninstall", a.category, a.name])),
    },
    {
      name: "library_unregister",
      meta: {
        title: "Unregister Library Path",
        description: "등록된 라이브러리 경로를 store/config.json 의 libraries 에서 제거(추적 해제). 설치된 항목·라이브러리 원본 디렉토리는 건드리지 않음. env(CLAUDE_CONFIG_LIBRARIES) 지정 경로는 제거 불가",
        inputSchema: z.object({ lib: z.string().describe("등록 해제할 라이브러리 루트 경로") }),
        annotations: EDIT,
      },
      run: async (a: { lib: string }) =>
        jsonResult(await runPy("library.py", ["unregister", "--lib", a.lib])),
    },

    // ----- 브라우저 열기 -----
    {
      name: "open_report",
      meta: {
        title: "Open Static Report in Browser",
        description: "현재 설정 상태를 데이터 인라인 정적 HTML 로 생성해 기본 브라우저에서 연다(읽기 전용 스냅샷)",
        inputSchema: z.object({}), annotations: WRITE,
      },
      run: async () => {
        const out = path.join(scriptDir, "config-report.html");
        await runPy("claude_config.py", ["report", "-o", out]);
        openInBrowser(out.replace(/\\/g, "/"));
        return jsonResult(JSON.stringify({ ok: true, message: "정적 리포트 생성 + 브라우저 열기", path: out }));
      },
    },
    {
      name: "open_in_browser",
      meta: {
        title: "Open Live Dashboard in Browser",
        description: "라이브 대시보드 HTTP 서버(기본 3002)를 필요시 기동하고 기본 브라우저에서 연다(편집/복원/watcher 동작)",
        inputSchema: z.object({ port: z.number().optional() }), annotations: WRITE,
      },
      run: async (a: { port?: number }) => {
        const port = a.port || Number(process.env.PORT || 3002);
        const url = `http://localhost:${port}/`;
        const up = await fetch(url).then((r) => r.ok).catch(() => false);
        if (!up) {
          // server.ts(HTTP) 를 detached 로 기동. watcher 와 동일한 Start-Process 패턴.
          const srv = path.join(scriptDir, "server.ts").replace(/\\/g, "/");
          const dir = scriptDir.replace(/\\/g, "/");
          if (process.platform === "win32") {
            // 주의: `Start-Process npx` 는 npx.ps1 을 찾아 기본앱(메모장)으로 '편집' 연다.
            // cmd.exe /c 로 npx 를 '실행'해야 한다. -WorkingDirectory 로 local tsx 해결, PORT 는 env.
            const cmd = `Start-Process cmd -WindowStyle Hidden -WorkingDirectory '${dir}' -ArgumentList '/c','npx tsx ${srv}'`;
            spawn("powershell.exe", ["-NoProfile", "-Command", cmd],
              { stdio: "ignore", windowsHide: true, env: { ...process.env, PORT: String(port) } });
          } else {
            spawn("npx", ["tsx", srv], { cwd: scriptDir, stdio: "ignore", detached: true, env: { ...process.env, PORT: String(port) } }).unref();
          }
          // 기동 대기(최대 ~6s).
          for (let i = 0; i < 12; i++) {
            await new Promise((r) => setTimeout(r, 500));
            if (await fetch(url).then((r) => r.ok).catch(() => false)) break;
          }
        }
        openInBrowser(url);
        return jsonResult(JSON.stringify({ ok: true, message: "라이브 대시보드 브라우저 열기", url }));
      },
    },
  ];

  // 실행 중인 watcher.ps1 프로세스를 모두 종료(좀비/중복 정리).
  // '-File ...watcher.ps1' 로 기동된 실제 watcher 만 대상(이 명령 자신/-Command 류는 제외하고, $PID 도 제외해 자기 종료 방지).
  async function killWatchers(): Promise<void> {
    if (process.platform !== "win32") return;
    const cmd = "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | " +
      "Where-Object { $_.CommandLine -like '*-File*watcher.ps1*' -and $_.ProcessId -ne $PID } | " +
      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }";
    await pexec("powershell.exe", ["-NoProfile", "-Command", cmd]).catch(() => {});
  }

  // 기본 브라우저로 경로/URL 열기(로컬 서버이므로 사용자 머신에서 열림).
  function openInBrowser(target: string): void {
    if (process.platform === "win32") {
      spawn("powershell.exe", ["-NoProfile", "-Command", `Start-Process '${target}'`],
        { stdio: "ignore", windowsHide: true });
    } else {
      spawn("xdg-open", [target], { stdio: "ignore", detached: true }).unref();
    }
  }
}

export function registerAll(server: any, scriptDir: string): void {
  for (const d of buildTools(scriptDir)) {
    server.registerTool(d.name, d.meta, d.run);
  }

  // UI 전용 도구 + 리소스 (REST 에는 불필요)
  registerAppTool(server, "show_config_monitor", {
    title: "Show Config Monitor",
    description: "Claude 설정 모니터 대시보드(설정 카드 + 파일 클릭 시 diff history) 열기",
    inputSchema: {},
    _meta: { ui: { resourceUri: RESOURCE_URI } },
  }, async () => text("Config monitor dashboard opened."));

  registerAppResource(server, RESOURCE_URI, RESOURCE_URI, { mimeType: RESOURCE_MIME_TYPE }, async () => {
    const html = await fs.readFile(path.join(scriptDir, "dist", "dashboard.html"), "utf-8");
    return { contents: [{ uri: RESOURCE_URI, mimeType: RESOURCE_MIME_TYPE, text: html }] };
  });
}
