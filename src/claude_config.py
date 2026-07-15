#!/usr/bin/env python3
r"""
claude_config.py - Claude 설정 introspection + 카드 HTML 생성기 (v2, 7 카테고리)

대상 경로:
  ~\.claude.json                                  Claude Code 전역 (관심 키만 추출 + 마스킹)
  ~\.claude\settings.json                         permission allow/deny + hooks
  ~\.claude\skills\*                              Code 스킬
  ~\.claude\agents\*                              Code 에이전트
  ~\Claude\Scheduled\*\SKILL.md                   스케줄러
  %APPDATA%\Claude\claude_desktop_config.json     Desktop MCP
  %APPDATA%\Claude\...\skills-plugin\**\manifest.json   Desktop 스킬(서버관리, 동기화 캐시)

CLI:
  python claude_config.py discover            # 어떤 경로가 잡히는지
  python claude_config.py dump                # 정규화 상태(JSON)  ← MCP get_config 가 사용
  python claude_config.py report -o out.html  # 카드 HTML 생성(데이터 인라인)
"""
from __future__ import annotations
import argparse, json, os, glob as globmod, html, re, sys

# Windows 콘솔 기본 인코딩(cp949)에서 한글/em-dash 출력 시 UnicodeEncodeError 방지.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
from datetime import datetime

import paths  # Win32/MSIX 겸용 Claude Desktop 디렉토리 해석(read↔write 동일 경로 보장)

HOME = os.path.expanduser("~")
# Desktop 데이터 디렉토리('...\Claude')는 설치 방식(Win32 vs MSIX/Store)에 따라
# 물리 경로가 다르므로 후보를 프로브해 해석한다. config·skills-plugin manifest 둘 다 하위.
DESKTOP_DIR = paths.resolve_desktop_dir()

CANDIDATES = {
    "claude_json":     [os.path.join(HOME, ".claude.json")],
    "code_settings":   [os.path.join(HOME, ".claude", "settings.json"),
                         os.path.join(HOME, ".claude", "settings.local.json")],
    "skills_dir":      [os.path.join(HOME, ".claude", "skills")],
    "agents_dir":      [os.path.join(HOME, ".claude", "agents")],
    "scheduled_dir":   [os.path.join(HOME, "Claude", "Scheduled")],
    "desktop_config":  [os.path.join(DESKTOP_DIR, "claude_desktop_config.json")],
    "desktop_skill_manifest_glob":
                       [os.path.join(DESKTOP_DIR, "local-agent-mode-sessions",
                                     "skills-plugin", "**", "manifest.json")],
}

SENSITIVE_RE = re.compile(r"(token|secret|password|apikey|api_key|key|oauth|account|email|userid|uuid)", re.I)

def first_existing(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def all_glob(patterns):
    out = []
    for pat in patterns:
        out += globmod.glob(pat, recursive=True)
    return sorted(set(out))

def safe_load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}

def discover(extra=None):
    found = {}
    for key, cands in CANDIDATES.items():
        if key.endswith("_glob"):
            found[key] = all_glob(cands)
        else:
            found[key] = first_existing(cands)
    if extra:
        for kv in extra:
            if "=" in kv:
                k, v = kv.split("=", 1)
                found[k] = v
    return found

def read_frontmatter(path):
    """SKILL.md / agent md 의 --- ... --- frontmatter 를 얕게 파싱."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return {}
    m = re.match(r"^\s*---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"\s*([A-Za-z0-9_\-]+)\s*:\s*(.*)\s*$", line)
        if mm:
            meta[mm.group(1)] = mm.group(2).strip().strip('"\'')
    return meta

def _short(v, n=160):
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"

DESC_KEYS = {"desc", "description", "설명", "summary"}

def card(name, kv, badge=None, ok=False, edit=None, scope=None, project=None):
    # 서술형 값은 넉넉히 담고(줄 수 표시는 UI 의 -webkit-line-clamp 가 담당),
    # 코드형/경로 값만 160자 선절단 — 슬라이더(2~10줄) 전 구간이 실제 텍스트로 채워지게.
    c = {"name": name, "badge": badge, "ok": ok,
         "kv": [[k, _short(v, 600 if str(k).lower() in DESC_KEYS else 160)] for k, v in kv]}
    if edit:  # UI 가 인라인 add/remove 버튼을 그릴 때 쓰는 구조화 메타
        c["edit"] = edit
    # scope/project 는 프로젝트 전용 항목에만 붙는다(전역 항목은 미부여 -> 출력 불변).
    if scope:
        c["scope"] = scope
    if project:
        c["project"] = project
    return c

# --- 섹션별 카드 빌더(전역/프로젝트 공용). scope=None 이면 전역(기존 동작 그대로),
#     scope="project" 이면 프로젝트 항목으로 태깅. 프로젝트 skills/agents 는 편집 대상이
#     전역 디렉토리라 오설치/오삭제 위험 -> 뷰 전용(edit 미부여). permissions/hooks 만 편집 가능(대상 settings 파일 지정).
def _perm_cards(cs, scope=None, project=None):
    cards = []
    if cs and os.path.exists(cs):
        perms = (safe_load(cs) or {}).get("permissions", {})
        for kind in ("allow", "deny", "ask"):
            lst = perms.get(kind, []) or []
            edit = {"kind": "perm", "permKind": kind, "items": list(lst)}
            if scope == "project":
                edit["settings"] = cs
            cards.append(card(kind, [], badge=str(len(lst)), ok=(kind == "allow"),
                              edit=edit, scope=scope, project=project))
    return cards

def _hook_cards(cs, scope=None, project=None):
    cards = []
    if cs and os.path.exists(cs):
        hooks = (safe_load(cs) or {}).get("hooks", {})
        for event, entries in hooks.items():
            cmds = [hk.get("command", "") for ent in (entries or [])
                    for hk in (ent.get("hooks", []) or [])]
            edit = {"kind": "hook", "event": event, "items": cmds}
            if scope == "project":
                edit["settings"] = cs
            cards.append(card(event, [("matchers", len(entries or []))], badge="hook",
                              edit=edit, scope=scope, project=project))
    return cards

def _skill_cards(sd, scope=None, project=None):
    cards = []
    if sd and os.path.isdir(sd):
        for name in sorted(os.listdir(sd)):
            if name.startswith("."):
                continue
            full = os.path.join(sd, name)
            if os.path.isdir(full):
                has_md = os.path.exists(os.path.join(full, "SKILL.md"))
                meta = read_frontmatter(os.path.join(full, "SKILL.md")) if has_md else {}
                edit = None if scope == "project" else {"kind": "skill", "name": name}
                cards.append(card(name, [("desc", meta.get("description", "-")), ("path", full)],
                                  badge="SKILL.md" if has_md else "no md", ok=has_md,
                                  edit=edit, scope=scope, project=project))
        if scope != "project":
            cards.append(card("＋ 새 스킬", [("형식", "name 설명…")],
                              badge="add", edit={"kind": "skill-add"}))
    return cards

def _agent_cards(ad, scope=None, project=None):
    cards = []
    if ad and os.path.isdir(ad):
        for name in sorted(os.listdir(ad)):
            if name.startswith("."):
                continue
            full = os.path.join(ad, name)
            md = full if name.endswith(".md") else os.path.join(full, "AGENT.md")
            meta = read_frontmatter(md) if os.path.exists(md) else {}
            # 제거 op 는 파일시스템 이름 기준(.md 제거) — frontmatter name 과 다를 수 있음
            fs_name = name[:-3] if name.endswith(".md") else name
            edit = None if scope == "project" else {"kind": "agent", "name": fs_name}
            cards.append(card(meta.get("name", name), [
                ("desc", meta.get("description", "-")),
                ("tools", meta.get("tools", "-")),
                ("path", full),
            ], badge="agent", edit=edit, scope=scope, project=project))
        if scope != "project":
            cards.append(card("＋ 새 에이전트", [("형식", "name 설명…")],
                              badge="add", edit={"kind": "agent-add"}))
    return cards

def _append_project_cards(sections, projects):
    """추적 중인 프로젝트 .claude 디렉토리들의 permissions/hooks/skills/agents 를 스캔해
    해당 전역 섹션 뒤에 프로젝트 항목으로 append(전역 카드는 불변). title 개수도 재계산."""
    by_prefix = {}
    for sec in sections:
        for pfx in ("Permissions", "Hooks", "Skills (code)", "Agents"):
            if sec["title"].startswith(pfx):
                by_prefix[pfx] = sec
    def add_to(pfx, cards):
        sec = by_prefix.get(pfx)
        if sec is not None and cards:
            sec["cards"].extend(cards)
    for cdir in projects:
        if not cdir or not os.path.isdir(cdir):
            continue
        root = os.path.dirname(cdir.rstrip("/\\"))   # <root>/.claude -> <root> (칩 라벨 = 마지막 세그먼트)
        cs = first_existing([os.path.join(cdir, "settings.json"),
                             os.path.join(cdir, "settings.local.json")])
        add_to("Permissions", _perm_cards(cs, "project", root))
        add_to("Hooks", _hook_cards(cs, "project", root))
        add_to("Skills (code)", _skill_cards(os.path.join(cdir, "skills"), "project", root))
        add_to("Agents", _agent_cards(os.path.join(cdir, "agents"), "project", root))
    for pfx, sec in by_prefix.items():
        base = sec["title"].split(" · ")[0]
        sec["title"] = f"{base} · {len(sec['cards'])}"


def parse(found, project_dirs=None):
    # 주의: 아래 섹션2 에서 지역변수 projects(=.claude.json 의 projects 맵)를 쓰므로
    # 파라미터명은 project_dirs 로 구분(같은 이름이면 섀도잉으로 프로젝트 append 가 오작동).
    state = {"generated": datetime.now().isoformat(), "sources": found, "sections": []}
    add = state["sections"].append

    # 1) Desktop MCP servers (카드 단위 제거 + 섹션 add 카드)
    cards = []
    dc = found.get("desktop_config")
    if dc and os.path.exists(dc):
        servers = (safe_load(dc) or {}).get("mcpServers", {})
        for name, cfg in servers.items():
            cards.append(card(name, [
                ("command", cfg.get("command", "-")),
                ("args", " ".join(cfg.get("args", [])) or "-"),
                ("env", ", ".join((cfg.get("env") or {}).keys()) or "-"),
            ], badge="stdio" if cfg.get("command") else cfg.get("type", "?"), ok=True,
               edit={"kind": "mcp", "scope": "desktop", "name": name}))
        cards.append(card("＋ 새 MCP 서버", [("형식", 'name {"command":"npx","args":[...]}')],
                          badge="add", edit={"kind": "mcp-add", "scope": "desktop"}))
    add({"title": f"MCP Servers (desktop) · {len(cards)}", "source": dc, "cards": cards})

    # 2) Claude Code 전역 (.claude.json, 관심 키만)
    cards = []
    cj = found.get("claude_json")
    if cj and os.path.exists(cj):
        data = safe_load(cj)
        if "__error__" in data:
            cards.append(card("(파싱 오류)", [("error", data["__error__"])]))
        else:
            g_mcp = list((data.get("mcpServers") or {}).keys())
            projects = data.get("projects") or {}
            cards.append(card("전역 요약", [
                ("global MCP", ", ".join(g_mcp) or "-"),
                ("projects", len(projects)),
                ("account", "set" if data.get("oauthAccount") else "-"),
                ("dropped", "history(노이즈) 제외 · 민감키 마스킹"),
            ], badge="claude.json", ok=True))
            # 전역 mcpServers 를 카드 단위로 노출(제거 가능) + add 카드
            for name, cfg in (data.get("mcpServers") or {}).items():
                cfg = cfg or {}
                cards.append(card(name, [
                    ("command", cfg.get("command", "-")),
                    ("args", " ".join(cfg.get("args", [])) or "-"),
                ], badge="global mcp", ok=True,
                   edit={"kind": "mcp", "scope": "user", "name": name}))
            cards.append(card("＋ 새 전역 MCP 서버", [("형식", 'name {"command":"npx","args":[...]}')],
                              badge="add", edit={"kind": "mcp-add", "scope": "user"}))
            for path, pj in list(projects.items())[:20]:
                pj = pj or {}
                cards.append(card(os.path.basename(path.rstrip("/\\")) or path, [
                    ("path", path),
                    ("allowedTools", len(pj.get("allowedTools", []) or [])),
                    ("mcpServers", ", ".join((pj.get("mcpServers") or {}).keys()) or "-"),
                    ("trust", pj.get("hasTrustDialogAccepted", "-")),
                ], badge="project"))
    add({"title": f"Claude Code (.claude.json) · {len(cards)}", "source": cj, "cards": cards})

    # 3) Permissions + 4) Hooks (settings.json)
    cs = found.get("code_settings")
    perm_cards = _perm_cards(cs)
    hook_cards = _hook_cards(cs)
    add({"title": f"Permissions · {len(perm_cards)}", "source": cs, "cards": perm_cards})
    add({"title": f"Hooks · {len(hook_cards)}", "source": cs, "cards": hook_cards})

    # 5) Code Skills
    sd = found.get("skills_dir")
    skill_cards = _skill_cards(sd)
    add({"title": f"Skills (code) · {len(skill_cards)}", "source": sd, "cards": skill_cards})

    # 6) Agents
    ad = found.get("agents_dir")
    agent_cards = _agent_cards(ad)
    add({"title": f"Agents · {len(agent_cards)}", "source": ad, "cards": agent_cards})

    # 7) Scheduled tasks
    cards = []
    schd = found.get("scheduled_dir")
    if schd and os.path.isdir(schd):
        for name in sorted(os.listdir(schd)):
            full = os.path.join(schd, name)
            skill_md = os.path.join(full, "SKILL.md")
            if os.path.isdir(full) and os.path.exists(skill_md):
                meta = read_frontmatter(skill_md)
                cards.append(card(name, [
                    ("desc", meta.get("description", "-")),
                    ("schedule", meta.get("cron") or meta.get("schedule") or meta.get("fireAt", "-")),
                    ("path", full),
                ], badge="scheduled", ok=True))
    add({"title": f"Scheduled Tasks · {len(cards)}", "source": schd, "cards": cards})

    # 8) Desktop Skills (서버관리: 모든 manifest 머지 + creatorType 구분)
    cards = []
    mans = found.get("desktop_skill_manifest_glob")
    if isinstance(mans, str):
        mans = [mans]
    merged = {}
    for mp in (mans or []):
        if not (mp and os.path.exists(mp)):
            continue
        data = safe_load(mp)
        for s in (data.get("skills") or []) if isinstance(data, dict) else []:
            if isinstance(s, dict):
                sid = s.get("skillId") or s.get("name")
                prev = merged.get(sid)
                if prev is None or str(s.get("updatedAt") or "") >= str(prev.get("updatedAt") or ""):
                    merged[sid] = s
    items = sorted(merged.values(), key=lambda s: (s.get("creatorType") != "user", s.get("name", "")))
    n_user = sum(1 for s in items if s.get("creatorType") == "user")
    for s in items:
        ct = s.get("creatorType", "?")
        cards.append(card(s.get("name", "?"), [
            ("desc", s.get("description", "-")),
            ("creator", ct),
            ("enabled", s.get("enabled", "-")),
            ("updated", s.get("updatedAt") or "-"),
        ], badge=("user" if ct == "user" else "anthropic"), ok=(ct == "user")))
    src = (f"{mans[0]}  (+{len(mans)-1} more)" if mans and len(mans) > 1 else (mans[0] if mans else None))
    add({"title": f"Desktop Skills · user {n_user} / anthropic {len(items)-n_user}", "source": src, "cards": cards})

    if project_dirs:
        _append_project_cards(state["sections"], project_dirs)
    return state

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude 설정 상태</title>
<style>
  :root{ --bg:#0f1115; --card:#1a1d24; --line:#2a2f3a; --fg:#e6e8ec; --mut:#8b93a1;
         --accent:#7aa2f7; --ok:#9ece6a; }
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);
     font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:28px}
  h1{font-size:20px;margin:0 0 4px} .sub{color:var(--mut);font-size:12px;margin-bottom:22px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);
     margin:26px 0 4px;border-bottom:1px solid var(--line);padding-bottom:6px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-top:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
  .card .name{font-weight:600;font-size:14px;margin-bottom:8px;display:flex;align-items:center;gap:8px;
     justify-content:space-between}
  .badge{font-size:10px;padding:2px 7px;border-radius:20px;background:#222733;color:var(--mut);white-space:nowrap}
  .badge.ok{background:rgba(158,206,106,.15);color:var(--ok)}
  .kv{display:flex;gap:6px;font-size:12px;margin-top:4px}
  .kv .k{color:var(--mut);min-width:84px;flex:0 0 auto}
  .kv .v{color:var(--fg);word-break:break-all;font-family:ui-monospace,monospace;white-space:pre-wrap}
  .empty{color:var(--mut);font-style:italic;padding:8px 0}
  .src{font-size:11px;color:var(--mut)} code{background:#222733;padding:1px 5px;border-radius:4px}
</style></head><body>
<h1>Claude 설정 상태 카드</h1>
<div class="sub">생성: __GENERATED__ · 데이터 인라인(오프라인 열람) · 민감정보 마스킹됨</div>
<div id="app"></div>
<script>
const STATE = __DATA__;
const el = h=>{const t=document.createElement('template');t.innerHTML=h.trim();return t.content.firstChild;};
const esc = s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const app = document.getElementById('app');
STATE.sections.forEach(sec=>{
  app.appendChild(el(`<h2>${esc(sec.title)}</h2>`));
  app.appendChild(el(`<div class="src">출처: <code>${esc(sec.source||'미발견')}</code></div>`));
  const grid = el('<div class="grid"></div>');
  if(!sec.cards.length) grid.appendChild(el('<div class="empty">항목 없음 / 파일 미발견</div>'));
  sec.cards.forEach(c=>{
    const cd = el('<div class="card"></div>');
    cd.appendChild(el(`<div class="name"><span>${esc(c.name)}</span>`+
      (c.badge?`<span class="badge ${c.ok?'ok':''}">${esc(c.badge)}</span>`:'')+`</div>`));
    c.kv.forEach(([k,v])=>cd.appendChild(
      el(`<div class="kv"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`)));
    grid.appendChild(cd);
  });
  app.appendChild(grid);
});
</script></body></html>
"""

def make_html(state):
    return (HTML_TEMPLATE
            .replace("__GENERATED__", html.escape(state["generated"]))
            .replace("__DATA__", json.dumps(state, ensure_ascii=False)))

def main():
    ap = argparse.ArgumentParser(prog="claude_config", description="Claude 설정 introspection + 카드 HTML")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("discover", "dump", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--paths", nargs="*", help="key=path 로 후보 직접 지정")
        if name in ("dump", "report"):
            sp.add_argument("--projects", nargs="*", default=None,
                            help="프로젝트 .claude 디렉토리들 - 각각의 permissions/hooks/skills/agents 를 프로젝트 항목으로 추가")
        if name == "report":
            sp.add_argument("-o", "--out", default="claude-status.html")
    args = ap.parse_args()
    found = discover(getattr(args, "paths", None))
    projects = getattr(args, "projects", None)

    if args.cmd == "discover":
        print(json.dumps(found, ensure_ascii=False, indent=2))
    elif args.cmd == "dump":
        print(json.dumps(parse(found, projects), ensure_ascii=False, indent=2))
    elif args.cmd == "report":
        state = parse(found, projects)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(make_html(state))
        print(f"리포트 생성: {os.path.abspath(args.out)}")
        for s in state["sections"]:
            print(f"  - {s['title']}")

if __name__ == "__main__":
    main()
