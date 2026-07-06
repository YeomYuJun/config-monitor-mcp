#!/usr/bin/env python3
r"""
config_edit.py - Claude 설정 파일 안전 편집기 (스냅샷-선행 + atomic write + .bak).

안전장치:
  1) 편집 전 cas.py 스냅샷 시도(추적 중이면 롤백 지점 생성). 실패해도 편집은 진행.
  2) 편집 전 타임스탬프 .bak 백업.
  3) tmp 파일에 쓴 뒤 JSON 파싱 검증 통과해야 os.replace 로 원자적 교체.

대상: ~/.claude/settings.json (permissions / hooks), ~/.claude/skills (code 스킬 스캐폴드).
출력은 항상 JSON 한 줄 ({ok, message, ...}) — MCP 서버가 그대로 파싱.

ops:
  perm-add    <allow|deny|ask> <rule>
  perm-remove <allow|deny|ask> <rule>
  hook-add    <event> <command> [--matcher M]
  hook-remove <event> <command-substring>
  skill-scaffold <name> [--desc D]
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

def main():
    ap = argparse.ArgumentParser(prog="config_edit")
    ap.add_argument("--settings", default=DEFAULT_SETTINGS)
    ap.add_argument("--skills-dir", default=DEFAULT_SKILLS)
    ap.add_argument("--store", default=os.environ.get("CLAUDE_SNAPSHOT_STORE"))
    ap.add_argument("--no-snapshot", action="store_true", help="편집 전 cas 스냅샷 생략")
    sub = ap.add_subparsers(dest="op", required=True)

    p = sub.add_parser("perm-add");    p.add_argument("kind", choices=["allow", "deny", "ask"]); p.add_argument("rule")
    p = sub.add_parser("perm-remove"); p.add_argument("kind", choices=["allow", "deny", "ask"]); p.add_argument("rule")
    p = sub.add_parser("hook-add");    p.add_argument("event"); p.add_argument("command"); p.add_argument("--matcher")
    p = sub.add_parser("hook-remove"); p.add_argument("event"); p.add_argument("needle")
    p = sub.add_parser("skill-scaffold"); p.add_argument("name"); p.add_argument("--desc", default="TODO")

    a = ap.parse_args()

    # skill 스캐폴드는 settings 편집과 분리
    if a.op == "skill-scaffold":
        d = os.path.join(a.skills_dir, a.name)
        md = os.path.join(d, "SKILL.md")
        if os.path.exists(md):
            out(False, f"이미 존재: {md}")
        if not a.no_snapshot:
            snapshot_before(a.store)
        os.makedirs(d, exist_ok=True)
        with open(md, "w", encoding="utf-8") as f:
            f.write(f"---\nname: {a.name}\ndescription: {a.desc}\n---\n\n# {a.name}\n\n작성 중.\n")
        out(True, f"스킬 스캐폴드 생성: {md}", path=md)

    # 이하 settings.json 편집
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
