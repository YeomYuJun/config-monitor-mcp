#!/usr/bin/env python3
r"""remote_fetch.py - 원격 레포 물질화(git) + 라이브러리 레이아웃 탐지.

store 를 모른다(캐시 경로는 호출부가 준다). 네트워크를 타는 유일한 모듈이므로
scan 경로에서는 import 만 하고 호출하지 않는다.

레이아웃 탐지가 별도인 이유: fetch 된 플러그인 루트는 이미 소문자 agents/skills/commands 라
매핑이 0 이다. 매핑이 필요한 건 my-tools 처럼 Agents/Skills 인 임의 레포뿐이다.
"""
from __future__ import annotations
import os, shutil, subprocess

CATEGORY_NAMES = ("agents", "skills", "commands")
SKILL_MARKER = "skill.md"          # 비교는 항상 casefold 후


def _dirs(path):
    try:
        return [e for e in os.listdir(path) if os.path.isdir(os.path.join(path, e)) and not e.startswith(".")]
    except OSError:
        return []


def _has_skill_marker(path) -> bool:
    """path 하위 어딘가에 SKILL.md(대소문자 무시)를 품은 디렉토리가 있는가."""
    for root, dirs, names in os.walk(path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if any(n.casefold() == SKILL_MARKER for n in names):
            return True
    return False


def detect_layout(root: str) -> dict:
    """1) 루트 직계에서 agents/skills/commands 를 대소문자 무시로 찾아 채택.
    2) 실패한 카테고리는 깊이 2 + SKILL.md 마커로 후보만 제안(자동 채택 안 함).

    반환 map 의 값은 **root 기준 상대경로**(실제 디스크 표기 그대로).
    자동 채택은 폴백이 아니라 기본 경로이고, 매핑은 사용자가 확정한다."""
    top = _dirs(root)
    by_fold = {d.casefold(): d for d in top}
    cmap, found = {}, []
    for cat in CATEGORY_NAMES:
        actual = by_fold.get(cat)
        if actual:
            cmap[cat] = actual
            found.append(cat)

    candidates = []
    seen = set()

    def add(cat, rel):
        k = (cat, rel)
        if cat not in found and k not in seen:
            seen.add(k)
            candidates.append({"category": cat, "path": rel})

    for d in top:
        sub = os.path.join(root, d)
        for d2 in _dirs(sub):
            if d2.casefold() in CATEGORY_NAMES:
                add(d2.casefold(), os.path.join(d, d2))
        # SKILL.md 를 품은 디렉토리는 이름이 skills 가 아니어도 스킬 루트 후보다.
        if d.casefold() not in CATEGORY_NAMES and _has_skill_marker(sub):
            add("skills", d)

    return {"map": cmap, "found": found, "candidates": candidates}


# ── git ────────────────────────────────────────────────────────────────────
# 모든 명령에 -c core.autocrlf=false 고정 + 로컬 config 에도 박는다(이후 checkout 까지 커버).
# tarball 폴백은 만들지 않는다 - 호스트 하나에서만 되고 얻는 게 없다.

GIT_BASE = ["git", "-c", "core.autocrlf=false"]


class GitError(Exception):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


def _run(cwd, *args, check=True):
    if not git_available():
        raise GitError("git 을 PATH 에서 찾을 수 없습니다")
    p = subprocess.run([*GIT_BASE, *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    if check and p.returncode != 0:
        raise GitError(f"git {' '.join(args)} 실패(rc={p.returncode}): {(p.stderr or p.stdout).strip()[:500]}")
    return p.stdout.strip()


def materialize(dest: str, url: str, ref=None, sha=None, sparse=None) -> str:
    """dest 에 레포를 물질화하고 해석된 sha 를 반환. 이미 있으면 재사용(sparse 확장 포함).

    sha 가 있으면 sha 로 직접 fetch 한다(GitHub 은 비-tip SHA 도 허용).
    실패하면 --branch ref 로 받고 결과 sha 를 기록하는 폴백을 탄다.

    git fetch 는 실패해도 .git/FETCH_HEAD 를 빈 파일로 만들어 둔다(전송 시도 시점에
    열어서 truncate 하기 때문). "실패하면 부분 캐시를 남기지 않는다"는 계약은 git 이
    보장해주지 않으므로, 이번 호출이 새로 만든 dest 라면 실패 시 통째로 지운다."""
    os.makedirs(dest, exist_ok=True)
    fresh = not os.path.isdir(os.path.join(dest, ".git"))
    try:
        if fresh:
            _run(dest, "init", "-q")
            _run(dest, "config", "--local", "core.autocrlf", "false")
            _run(dest, "remote", "add", "origin", url)
        else:
            _run(dest, "remote", "set-url", "origin", url)

        if sparse:
            _run(dest, "sparse-checkout", "init", "--cone")
            _run(dest, "sparse-checkout", "set", *sparse)

        target = sha or ref or "HEAD"
        try:
            _run(dest, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", target)
        except GitError:
            if not sha:
                raise
            # 서버가 비-tip SHA want 를 거부하는 경우: ref 로 받고 sha 를 검증한다.
            _run(dest, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref or "HEAD")
        _run(dest, "checkout", "-q", "FETCH_HEAD")
        got = _run(dest, "rev-parse", "HEAD")
        if sha and got != sha:
            raise GitError(f"sha 불일치: 요청 {sha[:12]} != 실제 {got[:12]}")
        return got
    except GitError:
        if fresh:
            shutil.rmtree(dest, ignore_errors=True)
        raise
