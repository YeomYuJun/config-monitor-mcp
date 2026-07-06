#!/usr/bin/env python3
r"""
config_edit.py - Claude 설정 파일 안전 편집기 (스냅샷-선행 + atomic write + .bak).

안전장치:
  1) 편집 전 cas.py 스냅샷 시도(추적 중이면 롤백 지점 생성). 실패해도 편집은 진행.
  2) 편집 전 타임스탬프 .bak 백업.
  3) tmp 파일에 쓴 뒤 JSON 파싱 검증 통과해야 os.replace 로 원자적 교체.

대상:
  ~/.claude/settings.json                          permissions / hooks
  ~/.claude.json                                   전역 mcpServers (scope=user)
  %APPDATA%/Claude/claude_desktop_config.json      Desktop mcpServers (scope=desktop)
  ~/.claude/skills/<name>/                         code 스킬 (scaffold/remove)
  ~/.claude/agents/<name>.md                       에이전트 (scaffold/remove)
출력은 항상 JSON 한 줄 ({ok, message, ...}) — MCP 서버가 그대로 파싱.

ops:
  perm-add    <allow|deny|ask> <rule>
  perm-remove <allow|deny|ask> <rule>
  hook-add    <event> <command> [--matcher M]
  hook-remove <event> <command-substring>
  skill-scaffold <name> [--desc D]
  skill-remove   <name>                            (.trash 로 이동 — 복구 가능)
  agent-scaffold <name> [--desc D] [--tools T] [--model M]
  agent-remove   <name>                            (.trash 로 이동)
  mcp-add        <name> --json '<serverConfig>' [--scope user|desktop]
  mcp-remove     <name> [--scope user|desktop]

파괴적 삭제는 없다: JSON 편집은 스냅샷+.bak+atomic, 파일/디렉토리 삭제는 .trash 이동.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys, time

# Windows 콘솔 기본 인코딩(cp949)에서 한글/em-dash 출력 시 UnicodeEncodeError 방지.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HOME = os.path.expanduser("~")
DEFAULT_SETTINGS = os.path.join(HOME, ".claude", "settings.json")
DEFAULT_SKILLS = os.path.join(HOME, ".claude", "skills")
DEFAULT_AGENTS = os.path.join(HOME, ".claude", "agents")
DEFAULT_CLAUDE_JSON = os.path.join(HOME, ".claude.json")
DEFAULT_DESKTOP_CONFIG = os.path.join(
    os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming")),
    "Claude", "claude_desktop_config.json")
HERE = os.path.dirname(os.path.abspath(__file__))

def out(ok, message, **extra):
    print(json.dumps({"ok": ok, "message": message, **extra}, ensure_ascii=False))
    sys.exit(0 if ok else 1)

def load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def backup(path):
    if os.path.exists(path):
        bak = f"{path}.{time.strftime('%Y%m%d%H%M%S')}.bak"
        shutil.copy2(path, bak)
        return bak
    return None

def save_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(tmp, encoding="utf-8") as f:  # 검증
        json.load(f)
    os.replace(tmp, path)

def snapshot_before(store):
    """cas.py 스냅샷 시도(있으면). 실패는 무시(편집 자체는 진행)."""
    try:
        env = dict(os.environ)
        if store:
            env["CLAUDE_SNAPSHOT_STORE"] = store
        subprocess.run([sys.executable, os.path.join(HERE, "cas.py"), "snapshot",
                        "-m", "before config edit"], cwd=HERE, env=env,
                       capture_output=True, timeout=30)
    except Exception:
        pass

def trash(path):
    """rmtree 대신 형제 .trash/<name>.<ts> 로 이동 — 디렉토리 삭제도 복구 가능하게."""
    parent = os.path.dirname(os.path.normpath(path))
    tdir = os.path.join(parent, ".trash")
    os.makedirs(tdir, exist_ok=True)
    dst = os.path.join(tdir, f"{os.path.basename(os.path.normpath(path))}.{time.strftime('%Y%m%d%H%M%S')}")
    shutil.move(path, dst)
    return dst

def edit_json_file(path, mutate, no_snapshot, store):
    """settings 외 임의 JSON 설정 파일에 동일한 안전 패턴 적용.
    mutate(data) -> (data, msg, changed)."""
    data = load(path)
    data, msg, changed = mutate(data)
    if not changed:
        out(True, msg + " (no-op)", changed=False)
    if not no_snapshot:
        snapshot_before(store)
    bak = backup(path)
    save_atomic(path, data)
    out(True, msg, changed=True, file=path, backup=bak)

# ── ops (settings 딕셔너리를 받아 (settings, msg) 반환) ──

def op_perm_add(s, kind, rule):
    lst = s.setdefault("permissions", {}).setdefault(kind, [])
    if rule in lst:
        return s, f"이미 존재: {kind} '{rule}'", False
    lst.append(rule)
    return s, f"추가됨: permissions.{kind} += '{rule}'", True

def op_perm_remove(s, kind, rule):
    lst = s.get("permissions", {}).get(kind, [])
    if rule not in lst:
        return s, f"없음(변경 안 함): {kind} '{rule}'", False
    lst.remove(rule)
    return s, f"제거됨: permissions.{kind} -= '{rule}'", True

def op_hook_add(s, event, command, matcher):
    arr = s.setdefault("hooks", {}).setdefault(event, [])
    arr.append({"matcher": matcher or "*", "hooks": [{"type": "command", "command": command}]})
    return s, f"hook 추가됨: {event} (matcher={matcher or '*'}) <- {command}", True

def op_hook_remove(s, event, needle):
    arr = s.get("hooks", {}).get(event, [])
    before = len(arr)
    arr[:] = [h for h in arr if needle not in json.dumps(h, ensure_ascii=False)]
    n = before - len(arr)
    return s, f"hook 제거됨 {n}건: {event} ~ '{needle}'", n > 0

def op_mcp_add(d, name, server):
    servers = d.setdefault("mcpServers", {})
    existed = name in servers
    servers[name] = server
    return d, f"{'갱신' if existed else '추가'}됨: mcpServers.{name}", True

def op_mcp_remove(d, name):
    servers = d.get("mcpServers", {})
    if name not in servers:
        return d, f"없음(변경 안 함): mcpServers.{name}", False
    del servers[name]
    return d, f"제거됨: mcpServers.{name}", True

def _safe_name(name):
    """디렉토리 탈출/경로 주입 방지: 이름은 경로 구분자·상대참조 없이 단일 세그먼트만."""
    if not name or name != os.path.basename(name) or name in (".", "..") or \
       any(c in name for c in "\\/"):
        out(False, f"이름이 유효하지 않음: '{name}'")
    return name

def main():
    ap = argparse.ArgumentParser(prog="config_edit")
    ap.add_argument("--settings", default=DEFAULT_SETTINGS)
    ap.add_argument("--skills-dir", default=DEFAULT_SKILLS)
    ap.add_argument("--agents-dir", default=DEFAULT_AGENTS)
    ap.add_argument("--claude-json", default=DEFAULT_CLAUDE_JSON)
    ap.add_argument("--desktop-config", default=DEFAULT_DESKTOP_CONFIG)
    ap.add_argument("--store", default=os.environ.get("CLAUDE_SNAPSHOT_STORE"))
    ap.add_argument("--no-snapshot", action="store_true", help="편집 전 cas 스냅샷 생략")
    sub = ap.add_subparsers(dest="op", required=True)

    p = sub.add_parser("perm-add");    p.add_argument("kind", choices=["allow", "deny", "ask"]); p.add_argument("rule")
    p = sub.add_parser("perm-remove"); p.add_argument("kind", choices=["allow", "deny", "ask"]); p.add_argument("rule")
    p = sub.add_parser("hook-add");    p.add_argument("event"); p.add_argument("command"); p.add_argument("--matcher")
    p = sub.add_parser("hook-remove"); p.add_argument("event"); p.add_argument("needle")
    p = sub.add_parser("skill-scaffold"); p.add_argument("name"); p.add_argument("--desc", default="TODO")
    p.add_argument("--content", default=None, help="SKILL.md 전체 내용(frontmatter 포함). 지정 시 스텁 대신 그대로 기록")
    p = sub.add_parser("skill-remove");   p.add_argument("name")
    p = sub.add_parser("agent-scaffold"); p.add_argument("name"); p.add_argument("--desc", default="TODO")
    p.add_argument("--tools", default=""); p.add_argument("--model", default="")
    p.add_argument("--content", default=None, help="에이전트 md 전체 내용(frontmatter 포함). 지정 시 desc/tools/model 무시")
    p = sub.add_parser("agent-remove");   p.add_argument("name")
    p = sub.add_parser("mcp-add");    p.add_argument("name"); p.add_argument("--json", dest="server_json", required=True)
    p.add_argument("--scope", choices=["user", "desktop"], default="user")
    p = sub.add_parser("mcp-remove"); p.add_argument("name")
    p.add_argument("--scope", choices=["user", "desktop"], default="user")

    a = ap.parse_args()

    # ── 파일/디렉토리 기반 ops (skills / agents) ──
    if a.op == "skill-scaffold":
        name = _safe_name(a.name)
        d = os.path.join(a.skills_dir, name)
        md = os.path.join(d, "SKILL.md")
        if os.path.exists(md):
            out(False, f"이미 존재: {md}")
        if not a.no_snapshot:
            snapshot_before(a.store)
        os.makedirs(d, exist_ok=True)
        body = a.content if a.content else \
            f"---\nname: {name}\ndescription: {a.desc}\n---\n\n# {name}\n\n작성 중.\n"
        with open(md, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        out(True, f"스킬 {'설치' if a.content else '스캐폴드 생성'}: {md}", path=md)

    if a.op == "skill-remove":
        name = _safe_name(a.name)
        d = os.path.join(a.skills_dir, name)
        if not os.path.isdir(d):
            out(False, f"스킬 없음: {d}")
        if not a.no_snapshot:
            snapshot_before(a.store)
        dst = trash(d)
        out(True, f"스킬 제거됨(.trash 이동): {name}", trashed=dst)

    if a.op == "agent-scaffold":
        name = _safe_name(a.name)
        md = os.path.join(a.agents_dir, f"{name}.md")
        if os.path.exists(md):
            out(False, f"이미 존재: {md}")
        if not a.no_snapshot:
            snapshot_before(a.store)
        os.makedirs(a.agents_dir, exist_ok=True)
        if a.content:
            body = a.content
        else:
            fm = [f"name: {name}", f"description: {a.desc}"]
            if a.tools:
                fm.append(f"tools: {a.tools}")
            if a.model:
                fm.append(f"model: {a.model}")
            body = "---\n" + "\n".join(fm) + f"\n---\n\n# {name}\n\n작성 중.\n"
        with open(md, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        out(True, f"에이전트 {'설치' if a.content else '스캐폴드 생성'}: {md}", path=md)

    if a.op == "agent-remove":
        name = _safe_name(a.name)
        # <name>.md 파일 또는 <name>/ 디렉토리(AGENT.md 형) 둘 다 지원
        cand = [os.path.join(a.agents_dir, f"{name}.md"), os.path.join(a.agents_dir, name)]
        target = next((c for c in cand if os.path.exists(c)), None)
        if target is None:
            out(False, f"에이전트 없음: {name} ({a.agents_dir})")
        if not a.no_snapshot:
            snapshot_before(a.store)
        dst = trash(target)
        out(True, f"에이전트 제거됨(.trash 이동): {name}", trashed=dst)

    # ── mcpServers ops: scope 에 따라 대상 파일 선택 ──
    if a.op in ("mcp-add", "mcp-remove"):
        target = a.desktop_config if a.scope == "desktop" else a.claude_json
        if a.op == "mcp-add":
            try:
                server = json.loads(a.server_json)
            except json.JSONDecodeError as e:
                out(False, f"--json 파싱 실패: {e}")
            if not isinstance(server, dict):
                out(False, "--json 은 서버 설정 객체여야 함 (예: {\"command\":\"npx\",\"args\":[...]})")
            edit_json_file(target, lambda d: op_mcp_add(d, a.name, server), a.no_snapshot, a.store)
        else:
            edit_json_file(target, lambda d: op_mcp_remove(d, a.name), a.no_snapshot, a.store)

    # ── 이하 settings.json 편집 ──
    s = load(a.settings)
    if a.op == "perm-add":      s, msg, changed = op_perm_add(s, a.kind, a.rule)
    elif a.op == "perm-remove": s, msg, changed = op_perm_remove(s, a.kind, a.rule)
    elif a.op == "hook-add":    s, msg, changed = op_hook_add(s, a.event, a.command, a.matcher)
    elif a.op == "hook-remove": s, msg, changed = op_hook_remove(s, a.event, a.needle)
    else:
        out(False, f"알 수 없는 op: {a.op}")

    if not changed:
        out(True, msg + " (no-op)", changed=False)

    if not a.no_snapshot:
        snapshot_before(a.store)
    bak = backup(a.settings)
    save_atomic(a.settings, s)
    out(True, msg, changed=True, settings=a.settings, backup=bak)

if __name__ == "__main__":
    main()
