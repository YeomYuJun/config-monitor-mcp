#!/usr/bin/env python3
r"""
library.py - 라이브러리(.claude 구조 디렉토리) <-> 라이브 설정 토글 엔진.

원리 (/plugin 과 동일한 소스<->설치상태 모델):
  - 라이브러리 = 설치 가능한 항목의 소스(read-only 취급).
  - 라이브 설정(~/.claude) = 설치 상태.
  - scan 이 항목별 3상태를 판정: not_installed / installed(내용 동일) / modified(내용 다름).
    상태 판정은 이름이 아니라 **내용 해시** 비교 — 라이브러리가 업데이트되면 modified 가
    "동기화 가능" 신호가 된다.

카테고리 (v1): agents(파일), skills(디렉토리), commands(파일).
hooks 는 settings.json 조각 + 경로 재작성이 필요한 복합 유닛이라 v2.

안전 규율 (config_edit 와 동일):
  - install 덮어쓰기 전: cas 스냅샷 + .bak(파일) / .trash 이동(디렉토리)
  - uninstall: 삭제 대신 .trash 이동(복구 가능)

라이브러리 등록은 store/config.json 의 "libraries": [...] 에 영속화.
출력은 항상 JSON (MCP 서버가 그대로 파싱).
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from config_edit import backup, snapshot_before, trash, out  # 동일 안전 규율 재사용

HOME = os.path.expanduser("~")
DEFAULT_TARGET = os.path.join(HOME, ".claude")
DEFAULT_STORE = os.environ.get("CLAUDE_SNAPSHOT_STORE") or (
    "D:\\.claude-snapshot" if os.name == "nt" else os.path.join(HOME, ".claude-snapshot"))

# 카테고리 -> (라이브러리 하위경로, 항목 종류)
CATEGORIES = {
    "agents":   ("agents", "file"),      # *.md
    "skills":   ("skills", "dir"),       # <name>/ (SKILL.md 포함)
    "commands": ("commands", "file"),    # *.md
}

# kit 내부 상대참조 휴리스틱: 이식 시 깨질 수 있는 항목 표시용
KIT_REF_RE = re.compile(r"CLAUDE_PROJECT_DIR|(?:conventions|playbooks|rules|modes|analysis)/", re.A)


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_dir(path):
    """디렉토리 해시 = 정렬된 (상대경로, 파일해시) 목록의 해시. 파일 추가/삭제/수정 모두 감지."""
    rows = []
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in sorted(names):
            full = os.path.join(root, n)
            rel = os.path.relpath(full, path).replace("\\", "/")
            rows.append(f"{rel}:{_hash_file(full)}")
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def _has_kit_ref(path, kind):
    """항목이 kit 내부 디렉토리를 참조하는지 휴리스틱 검사(이식성 배지용)."""
    files = []
    if kind == "file":
        files = [path]
    else:
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            files += [os.path.join(root, n) for n in names if n.endswith((".md", ".js", ".ps1", ".sh", ".py"))]
    for f in files[:50]:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if KIT_REF_RE.search(fh.read()):
                    return True
        except OSError:
            continue
    return False


def _store_config_path(store):
    return os.path.join(store, "config.json")


def _load_libs(store):
    """라이브러리 목록 = env(선언적) + store 등록분(런타임 등록), 순서 유지 중복 제거.
    CLAUDE_CONFIG_LIBRARIES 는 os.pathsep(;/:) 구분 복수 경로 — env 에서 빼면 목록에서도 빠진다."""
    libs = []
    for p in os.environ.get("CLAUDE_CONFIG_LIBRARIES", "").split(os.pathsep):
        p = p.strip()
        if p and p not in libs:
            libs.append(p)
    cfg_path = _store_config_path(store)
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            for p in json.load(f).get("libraries", []):
                if p not in libs:
                    libs.append(p)
    return libs


def _register_lib(store, lib):
    """라이브러리 경로를 store config.json 에 등록(멱등). store 미초기화면 등록 생략."""
    p = _store_config_path(store)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    libs = cfg.setdefault("libraries", [])
    if lib not in libs:
        libs.append(lib)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    return True


def _iter_items(lib, category):
    sub, kind = CATEGORIES[category]
    base = os.path.join(lib, sub)
    if not os.path.isdir(base):
        return
    for name in sorted(os.listdir(base)):
        if name.startswith("."):
            continue
        full = os.path.join(base, name)
        if kind == "file" and name.endswith(".md") and os.path.isfile(full):
            yield name[:-3], full, kind
        elif kind == "dir" and os.path.isdir(full):
            yield name, full, kind


def _target_path(target_root, category, name, kind):
    sub, _ = CATEGORIES[category]
    return os.path.join(target_root, sub, name if kind == "dir" else f"{name}.md")


def _status(lib_path, tgt, kind):
    if not os.path.exists(tgt):
        return "not_installed"
    try:
        if kind == "file":
            same = _hash_file(lib_path) == _hash_file(tgt)
        else:
            same = _hash_dir(lib_path) == _hash_dir(tgt)
    except OSError:
        return "modified"
    return "installed" if same else "modified"


def cmd_scan(a):
    libs = [a.lib] if a.lib else _load_libs(a.store)
    if a.lib:
        _register_lib(a.store, a.lib)
    if not libs:
        # 미설정은 오류가 아니라 정상 상태(라이브러리 기능 미사용). 빈 결과로 응답.
        print(json.dumps({"ok": True, "target": a.target, "libraries": []}, ensure_ascii=False))
        return
    result = []
    for lib in libs:
        if not os.path.isdir(lib):
            result.append({"lib": lib, "error": "경로 없음", "categories": {}})
            continue
        cats = {}
        for category in CATEGORIES:
            items = []
            for name, full, kind in _iter_items(lib, category):
                tgt = _target_path(a.target, category, name, kind)
                items.append({
                    "name": name,
                    "status": _status(full, tgt, kind),
                    "kit_ref": _has_kit_ref(full, kind),
                    "lib_path": full,
                    "target": tgt,
                })
            cats[category] = items
        result.append({"lib": lib, "categories": cats})
    print(json.dumps({"ok": True, "target": a.target, "libraries": result}, ensure_ascii=False))


def _resolve_item(a):
    """(lib_path, kind, target) 해석. 이름은 단일 세그먼트만 허용(경로 주입 방지)."""
    if a.name != os.path.basename(a.name) or a.name in (".", "..") or any(c in a.name for c in "\\/"):
        out(False, f"이름이 유효하지 않음: '{a.name}'")
    libs = [a.lib] if a.lib else _load_libs(a.store)
    sub, kind = CATEGORIES[a.category]
    for lib in libs:
        src = os.path.join(lib, sub, a.name if kind == "dir" else f"{a.name}.md")
        if os.path.exists(src):
            return src, kind, _target_path(a.target, a.category, a.name, kind)
    out(False, f"라이브러리에 없음: {a.category}/{a.name}")


def cmd_install(a):
    src, kind, tgt = _resolve_item(a)
    existed = os.path.exists(tgt)
    if not a.no_snapshot:
        snapshot_before(a.store)
    bak = None
    if existed:
        # 사용자 결정: 덮어쓰기 전 백업 후 라이브러리 버전으로 동기화
        bak = backup(tgt) if kind == "file" else trash(tgt)
    os.makedirs(os.path.dirname(tgt), exist_ok=True)
    if kind == "file":
        shutil.copy2(src, tgt)
    else:
        shutil.copytree(src, tgt)
    out(True, f"{'동기화' if existed else '설치'}됨: {a.category}/{a.name}",
        target=tgt, backup=bak, synced=existed)


def cmd_uninstall(a):
    if a.name != os.path.basename(a.name) or a.name in (".", "..") or any(c in a.name for c in "\\/"):
        out(False, f"이름이 유효하지 않음: '{a.name}'")
    sub, kind = CATEGORIES[a.category]
    tgt = _target_path(a.target, a.category, a.name, kind)
    if not os.path.exists(tgt):
        out(True, f"이미 없음: {a.category}/{a.name} (no-op)", changed=False)
    if not a.no_snapshot:
        snapshot_before(a.store)
    dst = trash(tgt)
    out(True, f"제거됨(.trash 이동): {a.category}/{a.name}", trashed=dst)


def main():
    ap = argparse.ArgumentParser(prog="library")
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--target", default=DEFAULT_TARGET, help="설치 대상 루트(기본 ~/.claude)")
    ap.add_argument("--no-snapshot", action="store_true")
    sub = ap.add_subparsers(dest="op", required=True)

    p = sub.add_parser("scan"); p.add_argument("--lib", default=None)
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("install"); p.add_argument("category", choices=list(CATEGORIES))
    p.add_argument("name"); p.add_argument("--lib", default=None)
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("uninstall"); p.add_argument("category", choices=list(CATEGORIES))
    p.add_argument("name")
    p.set_defaults(func=cmd_uninstall)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
