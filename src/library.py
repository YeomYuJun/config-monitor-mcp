#!/usr/bin/env python3
r"""
library.py - 라이브러리(.claude 구조 디렉토리) <-> 라이브 설정 토글 엔진.

원리 (/plugin 과 동일한 소스<->설치상태 모델):
  - 라이브러리 = 설치 가능한 항목의 소스(read-only 취급).
  - 라이브 설정(~/.claude) = 설치 상태.
  - scan 이 항목별 3상태를 판정: not_installed / installed(내용 동일) / modified(내용 다름).
    상태 판정은 이름이 아니라 **내용 해시** 비교 — 라이브러리가 업데이트되면 modified 가
    "동기화 가능" 신호가 된다.

카테고리: agents(파일), skills(디렉토리), commands(파일).
hooks 는 settings.json 조각 + 경로 재작성이 필요한 복합 유닛이라 미지원.

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

# 라이브러리 바깥을 가리키는 상대참조 탐지 휴리스틱: CLAUDE_PROJECT_DIR 또는 형제 디렉토리
# (conventions/playbooks/rules/modes/analysis) 참조가 있으면 다른 환경에 설치 시 깨질 수 있어 배지로 표시.
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
    """항목이 라이브러리 바깥의 형제 디렉토리를 참조하는지 휴리스틱 검사(이식성 배지용)."""
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


def _env_libs():
    """CLAUDE_CONFIG_LIBRARIES(os.pathsep 구분 복수 경로). env 지정분은 대시보드에서 제거 불가."""
    return [p.strip() for p in os.environ.get("CLAUDE_CONFIG_LIBRARIES", "").split(os.pathsep) if p.strip()]


def _norm(p):
    """경로 비교용 정규화(대소문자/구분자/./.. 흡수). 같은 물리 경로의 다른 표기를 한 키로.
    이게 없으면 'D:\\x' 와 'D:/x' 가 다른 경로로 취급돼 같은 라이브러리가 두 번 스캔됨."""
    return os.path.normcase(os.path.normpath(p))


def _load_libs(store):
    """라이브러리 목록 = env(선언적) + store 등록분(런타임 등록). 정규화 경로로 중복 제거(첫 표기 유지).
    CLAUDE_CONFIG_LIBRARIES 는 os.pathsep(;/:) 구분 복수 경로 - env 에서 빼면 목록에서도 빠진다."""
    libs, seen = [], set()

    def add(p):
        if not p:
            return
        k = _norm(p)
        if k not in seen:
            seen.add(k)
            libs.append(p)

    for p in _env_libs():
        add(p)
    cfg_path = _store_config_path(store)
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            for p in json.load(f).get("libraries", []):
                add(p)
    return libs


def _register_lib(store, lib):
    """라이브러리 경로를 store config.json 에 등록(멱등). store 미초기화면 등록 생략."""
    p = _store_config_path(store)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    libs = cfg.setdefault("libraries", [])
    if all(_norm(x) != _norm(lib) for x in libs):   # 정규화 기준 멱등(다른 표기의 중복 방지)
        libs.append(lib)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    return True


def _unregister_lib(store, lib):
    """store config.json 의 libraries 에서 경로 제거(멱등). env 지정 경로는 여기서 못 지운다."""
    p = _store_config_path(store)
    if not os.path.exists(p):
        return False
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    libs = cfg.get("libraries", [])
    keep = [x for x in libs if _norm(x) != _norm(lib)]   # 정규화 기준 제거(다른 표기도 함께)
    if len(keep) == len(libs):
        return False
    cfg["libraries"] = keep
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return True


def _iter_items(lib, category):
    """(leaf, full, kind, relpath) 산출.
    file 종류(agents/commands): base 직계 *.md.
    dir 종류(skills): 가변 깊이 재귀 - SKILL.md 를 가진 디렉토리를 스킬 leaf 로 간주.
      (그룹/서브그룹으로 감싸인 구조도 leaf 만 뽑음. leaf 내부 하위폴더로는 안 내려감.)
    relpath 는 base(카테고리 루트) 기준 상대경로 - 그룹 표시·설치 지정에 사용."""
    sub, kind = CATEGORIES[category]
    base = os.path.join(lib, sub)
    if not os.path.isdir(base):
        return
    if kind == "file":
        for name in sorted(os.listdir(base)):
            if name.startswith("."):
                continue
            full = os.path.join(base, name)
            if name.endswith(".md") and os.path.isfile(full):
                yield name[:-3], full, kind, name[:-3]
        return
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if "SKILL.md" in names:
            rel = os.path.relpath(root, base).replace("\\", "/")
            yield os.path.basename(root), root, kind, rel
            dirs[:] = []  # leaf 확정 - 스킬 내부는 하위 스킬이 아니므로 더 안 내려감


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
    env = set(_env_libs())
    result = []
    for lib in libs:
        # source: env(제거 불가) / registered(대시보드에서 제거 가능)
        src = "env" if lib in env else "registered"
        if not os.path.isdir(lib):
            result.append({"lib": lib, "source": src, "error": "경로 없음", "categories": {}})
            continue
        cats = {}
        for category in CATEGORIES:
            items = []
            for leaf, full, kind, rel in _iter_items(lib, category):
                # 설치는 leaf 이름으로 평탄화(그룹 접두 제거) -> ~/.claude/<sub>/<leaf>
                tgt = _target_path(a.target, category, leaf, kind)
                group = os.path.dirname(rel).replace("\\", "/")  # "" = 그룹 없음(평면)
                items.append({
                    "name": leaf,
                    "group": group,      # 표시용(가변 깊이 트리): "2-stack/java-spring" 등
                    "relpath": rel,      # 설치 지정용(소스 상대경로, leaf 와 다를 수 있음)
                    "status": _status(full, tgt, kind),
                    "kit_ref": _has_kit_ref(full, kind),
                    "lib_path": full,
                    "target": tgt,
                })
            cats[category] = items
        result.append({"lib": lib, "source": src, "categories": cats})
    print(json.dumps({"ok": True, "target": a.target, "libraries": result}, ensure_ascii=False))


def cmd_unregister(a):
    # scan 처럼 항상 exit 0 + JSON 으로 응답(runPy 가 nonzero exit 를 throw 하므로 out() 대신 print).
    if not a.lib:
        print(json.dumps({"ok": False, "message": "제거할 라이브러리 경로(--lib) 필요"}, ensure_ascii=False)); return
    if any(_norm(a.lib) == _norm(e) for e in _env_libs()):
        print(json.dumps({"ok": False, "message": "환경변수(CLAUDE_CONFIG_LIBRARIES)로 지정된 경로는 제거할 수 없습니다"}, ensure_ascii=False)); return
    removed = _unregister_lib(a.store, a.lib)
    print(json.dumps({"ok": True, "message": "라이브러리 경로 제거됨" if removed else "이미 없음 (no-op)", "removed": removed}, ensure_ascii=False))


def _resolve_item(a):
    """(src, kind, target) 해석. a.path = 카테고리 루트 기준 상대경로(가변 깊이 허용).
    타깃은 leaf 이름으로 평탄화.
    경로 주입 차단: 각 세그먼트는 순수 파일명이어야 함 - 빈/./.. 금지, 콜론 금지
    (드라이브상대 'C:foo' 는 isabs=False 로 새어들어 os.path.join 이 lib 밖으로 튐 + NTFS ADS 'a:b'),
    그리고 basename 과 동일(구분자·드라이브 접두 제거되면 다름)."""
    rel = (a.path or "").replace("\\", "/").strip("/")
    parts = rel.split("/") if rel else []
    seg_bad = any(p in ("", ".", "..") or ":" in p or p != os.path.basename(p) for p in parts)
    if not parts or os.path.isabs(a.path) or seg_bad:
        out(False, f"경로가 유효하지 않음: '{a.path}'")
    sub, kind = CATEGORIES[a.category]
    if kind == "file" and len(parts) != 1:
        out(False, f"경로는 단일 이름이어야 함: '{a.path}'")
    leaf = parts[-1]
    libs = [a.lib] if a.lib else _load_libs(a.store)
    for lib in libs:
        src = os.path.join(lib, sub, *parts)
        if kind == "file":
            src += ".md"
        if os.path.exists(src):
            return src, kind, _target_path(a.target, a.category, leaf, kind)
    out(False, f"라이브러리에 없음: {a.category}/{rel}")


def cmd_install(a):
    # phantom 방지: target(.claude 루트)의 부모(프로젝트 폴더 또는 HOME)가 실제로 존재해야 설치.
    # 전역 ~/.claude 는 부모 ~ 가 항상 존재하므로 통과. 없는 프로젝트 경로에 .claude 를 만들지 않는다.
    parent = os.path.dirname(os.path.normpath(a.target))
    if parent and not os.path.isdir(parent):
        out(False, f"설치 대상의 부모 디렉토리가 없음(phantom 방지): {parent}")
    src, kind, tgt = _resolve_item(a)
    existed = os.path.exists(tgt)
    if not a.no_snapshot:
        snapshot_before(a.store)
    bak = None
    if existed:
        # 이미 설치된 항목은 오류 대신 동기화: 라이브러리 버전으로 덮어쓰기 전 백업(파일 .bak / 디렉토리 .trash)
        bak = backup(tgt) if kind == "file" else trash(tgt)
    os.makedirs(os.path.dirname(tgt), exist_ok=True)
    if kind == "file":
        shutil.copy2(src, tgt)
    else:
        shutil.copytree(src, tgt)
    out(True, f"{'동기화' if existed else '설치'}됨: {a.category}/{a.path}",
        target=tgt, backup=bak, synced=existed)


def cmd_uninstall(a):
    if a.name != os.path.basename(a.name) or a.name in (".", "..") or ":" in a.name or any(c in a.name for c in "\\/"):
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
    p = sub.add_parser("unregister"); p.add_argument("--lib", default=None)
    p.set_defaults(func=cmd_unregister)
    p = sub.add_parser("install"); p.add_argument("category", choices=list(CATEGORIES))
    p.add_argument("path", help="카테고리 루트 기준 상대경로(예: 2-stack/java-spring/error-handling). agents/commands 는 이름")
    p.add_argument("--lib", default=None)
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("uninstall"); p.add_argument("category", choices=list(CATEGORIES))
    p.add_argument("name")
    p.set_defaults(func=cmd_uninstall)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
