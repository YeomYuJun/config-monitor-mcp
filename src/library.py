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
import argparse, datetime, hashlib, json, os, re, shutil, sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from config_edit import backup, snapshot_before, trash, out  # 동일 안전 규율 재사용
import lib_store
import remote_fetch
from lib_store import norm as _norm   # 정규화 규칙을 한 곳에서만 정의

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


def _env_libs():
    """CLAUDE_CONFIG_LIBRARIES(os.pathsep 구분 복수 경로). env 지정분은 대시보드에서 제거 불가."""
    return [p.strip() for p in os.environ.get("CLAUDE_CONFIG_LIBRARIES", "").split(os.pathsep) if p.strip()]


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _origin_local(p):
    return "local:" + _norm(p)


def _load_libs(store):
    """라이브러리 레코드 목록 = env + store(libraries/remotes/marketplaces).
    정규화 경로로 중복 제거(첫 표기 유지).

    반환은 dict 레코드다: {"lib", "source", "origin", "map"}.
    libraries 키 자체는 문자열 배열 그대로 둔다 - _norm(os.path.normpath) 이 dict 에서 TypeError.
    """
    recs, seen = [], set()

    def add(p, source, origin, cmap=None):
        if not p:
            return
        k = _norm(p)
        if k in seen:
            return
        seen.add(k)
        recs.append({"lib": p, "source": source, "origin": origin, "map": cmap})

    for p in _env_libs():
        add(p, "env", _origin_local(p))
    cfg = lib_store.load_cfg(store)
    for p in cfg.get("libraries", []):
        add(p, "registered", _origin_local(p))
    for r in cfg.get("remotes", []):
        add(r.get("cache"), "remote", f"remote:{r.get('id')}", r.get("map"))
    for m in cfg.get("marketplaces", []):
        for pl in m.get("plugins", []):
            add(pl.get("cache"), "market", f"market:{m.get('id')}/{pl.get('name')}", pl.get("map"))
    return recs


def _register_lib(store, lib):
    """라이브러리 경로를 store config.json 에 등록(멱등).
    store 미초기화면 lib_store.StoreNotInitialized 가 올라간다 - 호출부가 ok:false 로 보고한다."""
    cfg = lib_store.load_cfg(store)
    libs = cfg.setdefault("libraries", [])
    if all(_norm(x) != _norm(lib) for x in libs):
        libs.append(lib)
        lib_store.save_cfg(store, cfg)
    elif not os.path.exists(lib_store.store_config_path(store)):
        raise lib_store.StoreNotInitialized(f"스토어가 초기화되지 않음: {store}")
    return True


def _unregister_lib(store, lib):
    """store config.json 의 libraries 에서 경로 제거(멱등). env 지정 경로는 여기서 못 지운다."""
    cfg = lib_store.load_cfg(store)
    libs = cfg.get("libraries", [])
    keep = [x for x in libs if _norm(x) != _norm(lib)]
    if len(keep) == len(libs):
        return False
    cfg["libraries"] = keep
    lib_store.save_cfg(store, cfg)
    return True


def _iter_items(lib, category, cmap=None):
    """(leaf, full, kind, relpath) 산출.
    cmap: 카테고리 -> lib 기준 상대경로(원격 레포가 Agents/Skills 처럼 대문자일 때).
    스킬 마커는 대소문자 무시 - skill.md 를 쓴 레포가 대소문자 구분 FS 에서 누락되지 않게.
    file 종류(agents/commands): base 직계 *.md.
    dir 종류(skills): 가변 깊이 재귀 - SKILL.md 를 가진 디렉토리를 스킬 leaf 로 간주.
      (그룹/서브그룹으로 감싸인 구조도 leaf 만 뽑음. leaf 내부 하위폴더로는 안 내려감.)
    relpath 는 base(카테고리 루트) 기준 상대경로 - 그룹 표시·설치 지정에 사용."""
    sub, kind = CATEGORIES[category]
    if cmap and cmap.get(category):
        sub = cmap[category]
    base = os.path.join(lib, sub)
    if not os.path.isdir(base):
        return
    if kind == "file":
        for name in sorted(os.listdir(base)):
            if name.startswith("."):
                continue
            full = os.path.join(base, name)
            if name.lower().endswith(".md") and os.path.isfile(full):
                yield name[:-3], full, kind, name[:-3]
        return
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        if any(n.casefold() == "skill.md" for n in names):
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


def _recs_for(a, register_new=False):
    """레코드 해석: a.lib 지정 시 이미 알려진 레코드(env/registered/remote/market)와
    norm() 매칭을 시도해, 있으면 그 레코드(정확한 source/origin/map)를 그대로 쓴다.
    없으면(=새 로컬 경로) local: 레코드를 합성한다 - register_new=True(scan 전용)면
    합성 전에 store 에도 등록한다. a.lib 미지정이면 전체 레코드 목록.

    무조건 local: 로 재합성하면(구버전 버그) UI 가 remote 캐시 경로를 --lib 로 보내면서
    --origin 도 같이 보내는 설치 조합(Task 9)에서 origin 필터가 항상 빈 결과가 되어
    원격 레포 설치가 전부 실패한다. 또 이미 remotes[] 에 있는 캐시 경로를 매번 재등록하면
    libraries[] 에도 중복으로 들어가 같은 캐시가 source="registered"/"remote" 두 줄로 쪼개진다.

    StoreNotInitialized 는 register_new 경로에서만 올라올 수 있다 - 호출부(cmd_scan)가 처리."""
    all_recs = _load_libs(a.store)
    if not a.lib:
        return all_recs
    hit = next((r for r in all_recs if _norm(r["lib"]) == _norm(a.lib)), None)
    if hit:
        return [hit]
    if register_new:
        _register_lib(a.store, a.lib)   # StoreNotInitialized 는 호출부로 전파
    return [{"lib": a.lib, "source": "registered", "origin": _origin_local(a.lib), "map": None}]


def _status_ex(cfg, target_root, category, leaf, src, tgt, kind, origin):
    """4상태 판정. (status, owner_origin) 반환.

    원장에 없는 타깃은 기존 해시 비교로 흘린다 - 기능 도입 전 설치분과
    대시보드 밖에서 생긴 항목이 오늘과 완전히 동일하게 동작해야 하므로."""
    if not os.path.exists(tgt):
        return "not_installed", None
    rec = lib_store.ledger_get(cfg, target_root, category, leaf)
    if not rec:
        return _status(src, tgt, kind), None
    owner = rec.get("origin")
    if owner and owner != origin:
        return "conflict", owner
    return _status(src, tgt, kind), owner


def cmd_scan(a):
    try:
        recs = _recs_for(a, register_new=True)
    except lib_store.StoreNotInitialized as e:
        print(json.dumps({"ok": False, "message": str(e), "libraries": []}, ensure_ascii=False)); return
    if not recs:
        # 미설정은 오류가 아니라 정상 상태(라이브러리 기능 미사용). 빈 결과로 응답.
        print(json.dumps({"ok": True, "target": a.target, "libraries": []}, ensure_ascii=False)); return
    cfg = lib_store.load_cfg(a.store)
    meta = _source_meta(cfg)          # origin -> {sha, fetched_at, url}
    result = []
    for rec in recs:
        lib, src, origin, cmap = rec["lib"], rec["source"], rec["origin"], rec["map"]
        row = {"lib": lib, "source": src, "origin": origin, **meta.get(origin, {})}
        if not os.path.isdir(lib):
            result.append({**row, "error": "경로 없음", "categories": {}})
            continue
        cats = {}
        for category in CATEGORIES:
            items = []
            for leaf, full, kind, rel in _iter_items(lib, category, cmap):
                # 설치는 leaf 이름으로 평탄화(그룹 접두 제거) -> ~/.claude/<sub>/<leaf>
                tgt = _target_path(a.target, category, leaf, kind)
                st, owner = _status_ex(cfg, a.target, category, leaf, full, tgt, kind, origin)
                group = os.path.dirname(rel).replace("\\", "/")  # "" = 그룹 없음(평면)
                items.append({
                    "name": leaf,
                    "group": group,      # 표시용(가변 깊이 트리): "2-stack/java-spring" 등
                    "relpath": rel,      # 설치 지정용(소스 상대경로, leaf 와 다를 수 있음)
                    "status": st,
                    "owner": owner,       # conflict 일 때 현재 소유자 origin
                    "origin": origin,
                    "kit_ref": _has_kit_ref(full, kind),
                    "lib_path": full,
                    "target": tgt,
                })
            cats[category] = items
        result.append({**row, "categories": cats})
    print(json.dumps({"ok": True, "target": a.target, "libraries": result}, ensure_ascii=False))


def _source_meta(cfg):
    """origin -> 표시용 메타(고정 sha / fetched_at / url). staleness 배지의 근거."""
    m = {}
    for r in cfg.get("remotes", []):
        m[f"remote:{r.get('id')}"] = {"sha": r.get("sha"), "fetched_at": r.get("fetched_at"), "url": r.get("url")}
    for mk in cfg.get("marketplaces", []):
        for pl in mk.get("plugins", []):
            m[f"market:{mk.get('id')}/{pl.get('name')}"] = {
                "sha": pl.get("sha"), "fetched_at": pl.get("fetched_at"), "url": mk.get("url")}
    return m


def cmd_unregister(a):
    # scan 처럼 항상 exit 0 + JSON 으로 응답(runPy 가 nonzero exit 를 throw 하므로 out() 대신 print).
    if not a.lib:
        print(json.dumps({"ok": False, "message": "제거할 라이브러리 경로(--lib) 필요"}, ensure_ascii=False)); return
    if any(_norm(a.lib) == _norm(e) for e in _env_libs()):
        print(json.dumps({"ok": False, "message": "환경변수(CLAUDE_CONFIG_LIBRARIES)로 지정된 경로는 제거할 수 없습니다"}, ensure_ascii=False)); return
    removed = _unregister_lib(a.store, a.lib)
    print(json.dumps({"ok": True, "message": "라이브러리 경로 제거됨" if removed else "이미 없음 (no-op)", "removed": removed}, ensure_ascii=False))


def _resolve_item(a):
    """(src, kind, target, origin) 해석. a.path = 카테고리 루트 기준 상대경로(가변 깊이 허용).
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
    recs = _recs_for(a)
    if getattr(a, "origin", None):
        recs = [r for r in recs if r["origin"] == a.origin]
        if not recs:
            out(False, f"출처를 찾을 수 없음: {a.origin}")
    hits = []
    for r in recs:
        cmap = r.get("map") or {}
        src = os.path.join(r["lib"], cmap.get(a.category) or sub, *parts)
        if kind == "file":
            src += ".md"
        if os.path.exists(src):
            hits.append((src, kind, _target_path(a.target, a.category, leaf, kind), r["origin"]))
    if not hits:
        out(False, f"라이브러리에 없음: {a.category}/{rel}")
    if len(hits) > 1:
        # 캐시가 여러 개면 첫 매치를 조용히 고르는 건 conflict 판정을 우회한다.
        out(False, f"출처가 모호함({len(hits)}개) - --origin 으로 지정하세요",
            candidates=[h[3] for h in hits])
    return hits[0]


def cmd_install(a):
    # phantom 방지: target(.claude 루트)의 부모(프로젝트 폴더 또는 HOME)가 실제로 존재해야 설치.
    # 전역 ~/.claude 는 부모 ~ 가 항상 존재하므로 통과. 없는 프로젝트 경로에 .claude 를 만들지 않는다.
    parent = os.path.dirname(os.path.normpath(a.target))
    if parent and not os.path.isdir(parent):
        out(False, f"설치 대상의 부모 디렉토리가 없음(phantom 방지): {parent}")
    src, kind, tgt, origin = _resolve_item(a)
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
    leaf = os.path.basename(a.path.replace("\\", "/").strip("/"))
    warn = None
    try:
        cfg = lib_store.load_cfg(a.store)
        lib_store.ledger_put(cfg, a.target, a.category, leaf, {
            "origin": origin,
            "relpath": a.path,
            "src_hash": _hash_file(src) if kind == "file" else _hash_dir(src),
            "at": _now(),
        })
        lib_store.save_cfg(a.store, cfg)
    except lib_store.StoreNotInitialized:
        # 파일은 이미 설치됐다. 원장만 못 남긴 상태를 숨기지 않는다 -
        # 이 항목은 다음 scan 에서 하위호환(해시 비교) 경로로 흐른다.
        warn = "스토어 미초기화로 출처를 기록하지 못했습니다(설치 자체는 완료)"
    out(True, f"{'동기화' if existed else '설치'}됨: {a.category}/{a.path}",
        target=tgt, backup=bak, synced=existed, origin=origin, warning=warn)


def cmd_uninstall(a):
    if a.name != os.path.basename(a.name) or a.name in (".", "..") or ":" in a.name or any(c in a.name for c in "\\/"):
        out(False, f"이름이 유효하지 않음: '{a.name}'")
    sub, kind = CATEGORIES[a.category]
    tgt = _target_path(a.target, a.category, a.name, kind)
    if not os.path.exists(tgt):
        out(True, f"이미 없음: {a.category}/{a.name} (no-op)", changed=False)
    cfg = lib_store.load_cfg(a.store)
    rec = lib_store.ledger_get(cfg, a.target, a.category, a.name)
    owner = (rec or {}).get("origin")
    if a.origin and owner and owner != a.origin:
        # 요청한 출처가 소유자가 아니다. 남의 설치를 지우지 않는다.
        out(False, f"출처가 다릅니다 - 이 항목의 소유자는 '{owner}' 입니다", owner=owner)
    if not a.no_snapshot:
        snapshot_before(a.store)
    dst = trash(tgt)
    try:
        lib_store.ledger_del(cfg, a.target, a.category, a.name)
        lib_store.save_cfg(a.store, cfg)
    except lib_store.StoreNotInitialized:
        pass          # 원장이 애초에 없었다는 뜻 - 지울 것도 없다
    out(True, f"제거됨(.trash 이동): {a.category}/{a.name}", trashed=dst, owner=owner)


def _lib_cache(store, *parts):
    return os.path.join(store, "lib-cache", *parts)


def _id_from_url(url):
    """URL 에서 레포명을 파생. 신뢰할 수 없는 입력이므로 세그먼트 검증을 통과해야 한다."""
    base = url.rstrip("/").split("/")[-1]
    if base.endswith(".git"):
        base = base[:-4]
    if not base or base != os.path.basename(base) or base in (".", "..") or \
       ":" in base or any(c in base for c in "\\/"):
        out(False, f"URL 에서 유효한 id 를 만들 수 없습니다: '{url}' - --id 로 지정하세요")
    return base


def cmd_remote_add(a):
    """임의 git 레포를 라이브러리로 등록. 네트워크를 타는 명시 호출 도구다(scan 아님)."""
    if not remote_fetch.git_available():
        print(json.dumps({"ok": False, "message": "git 을 PATH 에서 찾을 수 없습니다"}, ensure_ascii=False)); return
    rid = a.id or _id_from_url(a.url)
    if rid != os.path.basename(rid) or ":" in rid or any(c in rid for c in "\\/"):
        out(False, f"id 가 유효하지 않음: '{rid}'")
    cache = _lib_cache(a.store, "remotes", rid)
    cmap = None
    if a.map:
        try:
            cmap = json.loads(a.map)
        except ValueError as e:
            out(False, f"--map 이 JSON 이 아님: {e}")
        if not isinstance(cmap, dict) or any(k not in CATEGORIES for k in cmap):
            out(False, f"--map 키는 {list(CATEGORIES)} 중 하나여야 합니다")
    try:
        sha = remote_fetch.materialize(cache, a.url, ref=a.ref or None)
    except remote_fetch.GitError as e:
        print(json.dumps({"ok": False, "message": str(e)}, ensure_ascii=False)); return
    layout = remote_fetch.detect_layout(cache)
    if cmap:
        layout["map"] = {**layout["map"], **cmap}
    try:
        cfg = lib_store.load_cfg(a.store)
        remotes = cfg.setdefault("remotes", [])
        rec = {"id": rid, "url": a.url, "ref": a.ref or None, "sha": sha,
               "fetched_at": _now(), "cache": cache, "map": layout["map"] or None}
        for i, r in enumerate(remotes):
            if r.get("id") == rid:
                remotes[i] = rec
                break
        else:
            remotes.append(rec)
        lib_store.save_cfg(a.store, cfg)
    except lib_store.StoreNotInitialized as e:
        print(json.dumps({"ok": False, "message": str(e), "cache": cache}, ensure_ascii=False)); return
    print(json.dumps({"ok": True, "id": rid, "origin": f"remote:{rid}", "cache": cache,
                      "sha": sha, "layout": layout,
                      "message": f"원격 라이브러리 등록됨: {rid}"}, ensure_ascii=False))


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
    p.add_argument("--origin", default=None, help="출처 식별자(local:/remote:/market:). 캐시 다수일 때 필수")
    p.set_defaults(func=cmd_install)
    p = sub.add_parser("uninstall"); p.add_argument("category", choices=list(CATEGORIES))
    p.add_argument("name")
    p.add_argument("--origin", default=None, help="요청 출처. 원장의 소유자와 다르면 거부")
    p.set_defaults(func=cmd_uninstall)
    p = sub.add_parser("remote-add")
    p.add_argument("--url", required=True); p.add_argument("--ref", default=None)
    p.add_argument("--id", default=None); p.add_argument("--map", default=None,
                   help='카테고리 매핑 JSON, 예: {"agents":"Agents","skills":"Skills"}')
    p.set_defaults(func=cmd_remote_add)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
