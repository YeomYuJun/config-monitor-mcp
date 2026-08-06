#!/usr/bin/env python3
r"""test_remote_library.py - 원격 라이브러리 & 마켓플레이스 서브시스템 테스트.

의존성 0: 표준 unittest(pytest 로도 실행됨).
  python -m unittest tests.test_remote_library -v
  python -m pytest tests/test_remote_library.py

fetch 계열은 네트워크 대신 로컬 `git init` 픽스처 레포로 검증한다.
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
LIB = os.path.join(SRC, "library.py")
CAS = os.path.join(SRC, "cas.py")
sys.path.insert(0, SRC)          # 모듈 직접 import(순수 함수 단위 테스트용)
sys.path.insert(0, HERE)         # test_smoke 의 run() 재사용
from test_smoke import run       # noqa: E402


class LibStore(unittest.TestCase):
    """lib_store.py: store config 원자적 입출력 + 미초기화 시 명시적 실패."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="libstore_test_")
        self.store = os.path.join(self.tmp, "store")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_raises_when_store_uninitialized(self):
        # 조용한 등록 실패 회귀 가드: store 가 없으면 False 를 삼키지 말고 예외를 던진다.
        import lib_store
        with self.assertRaises(lib_store.StoreNotInitialized):
            lib_store.save_cfg(self.store, {"libraries": []})

    def test_roundtrip_preserves_unknown_keys(self):
        # 원장/remotes 를 쓰는 코드가 기존 키를 날리지 않아야 한다.
        import lib_store
        run(CAS, "--store", self.store, "init")
        cfg = lib_store.load_cfg(self.store)
        cfg["libraries"] = []                       # library.py 소유 키(cas init 은 안 만듦)
        cfg.setdefault("remotes", []).append({"id": "x", "url": "u"})
        lib_store.save_cfg(self.store, cfg)
        again = lib_store.load_cfg(self.store)
        self.assertEqual(again["remotes"], [{"id": "x", "url": "u"}])
        self.assertIn("libraries", again)
        self.assertIn("tracked", again)             # cas init 이 만든 키 보존


class Ledger(unittest.TestCase):
    """출처 원장: 키는 타깃 경로, origin 은 필드. 방향이 뒤집히면 conflict 가 죽는다."""

    def setUp(self):
        import lib_store
        self.ls = lib_store
        self.cfg = {}
        self.tgt = os.path.join("C:\\Users\\u", ".claude") if os.name == "nt" else "/home/u/.claude"

    def test_two_origins_same_name_collide_on_one_key(self):
        # 이 테스트가 원장 방향의 존재 이유다: 서로 다른 출처의 같은 이름이
        # **같은 키**에 앉아야 두 번째 설치가 첫 번째의 소유권을 본다.
        self.ls.ledger_put(self.cfg, self.tgt, "skills", "code-review",
                           {"origin": "market:official/plugin-a", "src_hash": "h1"})
        got = self.ls.ledger_get(self.cfg, self.tgt, "skills", "code-review")
        self.assertEqual(got["origin"], "market:official/plugin-a")

        rows = self.cfg["installed"][self.ls.ledger_root_key(self.tgt)]
        self.assertEqual(list(rows), ["skills/code-review"])   # 항목 1개 - origin 별로 갈라지지 않음

    def test_key_is_case_and_separator_normalized(self):
        self.ls.ledger_put(self.cfg, self.tgt, "agents", "a1", {"origin": "local:d:\\x"})
        alt = self.tgt.replace("\\", "/") if os.name == "nt" else self.tgt + "/"
        self.assertIsNotNone(self.ls.ledger_get(self.cfg, alt, "agents", "a1"))

    def test_del_is_idempotent(self):
        self.ls.ledger_put(self.cfg, self.tgt, "agents", "a1", {"origin": "local:x"})
        self.ls.ledger_del(self.cfg, self.tgt, "agents", "a1")
        self.ls.ledger_del(self.cfg, self.tgt, "agents", "a1")   # 두 번째도 조용히 no-op
        self.assertIsNone(self.ls.ledger_get(self.cfg, self.tgt, "agents", "a1"))

    def test_refs_root_finds_hooks_holding_a_cache_dir(self):
        # unregister 가드의 근거: 이 캐시를 붙잡고 있는 원장 항목을 찾아낸다.
        root = os.path.join("D:\\cache", "plugins", "sg", "abc123")
        self.ls.ledger_put(self.cfg, self.tgt, "hooks", "security-guidance",
                           {"kind": "hooks", "origin": "market:official/security-guidance", "root": root})
        self.ls.ledger_put(self.cfg, self.tgt, "agents", "a1", {"origin": "local:x"})
        hits = self.ls.ledger_refs_root(self.cfg, root)
        self.assertEqual([h["key"] for h in hits], ["hooks/security-guidance"])
        self.assertEqual(self.ls.ledger_refs_root(self.cfg, os.path.join("D:\\cache", "other")), [])

    def test_refs_root_matches_descendant_but_not_sibling_prefix(self):
        # 번들(str-path) 플러그인의 root 는 <market repo cache>/plugins/<name> 로
        # 마켓 레포 캐시의 하위다. exact-equality 만 보면 unregister --origin market:<id> 가
        # 그 하위 root 를 붙잡은 hooks/MCP 원장을 못 찾아 캐시를 지워버린다(원장은 죽은 경로 참조).
        cache_root = os.path.join("D:\\cache", "markets", "mk", "repo")
        descendant = os.path.join(cache_root, "plugins", "bundled")
        self.ls.ledger_put(self.cfg, self.tgt, "hooks", "bundled-hook",
                           {"kind": "hooks", "origin": "market:mk/bundled", "root": descendant})
        hits = self.ls.ledger_refs_root(self.cfg, cache_root)
        self.assertEqual([h["key"] for h in hits], ["hooks/bundled-hook"])

        # 같은 이름 접두를 공유하는 형제("...\repo-extra")는 매치되면 안 된다 -
        # 문자열 startswith(구분자 없이) 만 쓰면 "...\repo" 가 "...\repo-extra" 에 거짓 매치된다.
        self.cfg = {}
        sibling = os.path.join(os.path.dirname(cache_root), "repo-extra")
        self.ls.ledger_put(self.cfg, self.tgt, "hooks", "sibling-hook",
                           {"kind": "hooks", "origin": "market:mk/other", "root": sibling})
        self.assertEqual(self.ls.ledger_refs_root(self.cfg, cache_root), [])


class LayoutDetect(unittest.TestCase):
    """레이아웃 탐지: 소문자/대문자/깊이2/SKILL.md 마커. 대소문자 구분 FS 를 가정한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="layout_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mk(self, *parts, file=None, body="x"):
        d = os.path.join(self.tmp, *parts)
        os.makedirs(d, exist_ok=True)
        if file:
            with open(os.path.join(d, file), "w", encoding="utf-8") as f:
                f.write(body)
        return d

    def test_lowercase_root_is_adopted_with_identity_map(self):
        import remote_fetch
        self.mk("agents"); self.mk("skills"); self.mk("commands")
        r = remote_fetch.detect_layout(self.tmp)
        self.assertEqual(sorted(r["found"]), ["agents", "commands", "skills"])
        self.assertEqual(r["map"]["skills"], "skills")

    def test_uppercase_root_is_matched_case_insensitively(self):
        # my-tools 실물 레이아웃: Agents/ Skills/ - Windows 에선 지금도 잡히지만
        # 대소문자 구분 FS 사용자에게도 동작해야 한다.
        import remote_fetch
        self.mk("Agents"); self.mk("Skills")
        r = remote_fetch.detect_layout(self.tmp)
        self.assertEqual(sorted(r["found"]), ["agents", "skills"])
        self.assertEqual(r["map"]["agents"], "Agents")     # 실제 디스크 표기를 돌려준다
        self.assertEqual(r["map"]["skills"], "Skills")

    def test_depth_two_becomes_candidate_not_auto_adopted(self):
        import remote_fetch
        self.mk("packages", "skills")
        r = remote_fetch.detect_layout(self.tmp)
        self.assertEqual(r["found"], [])                   # 루트에 없으므로 자동 채택 안 함
        self.assertIn({"category": "skills", "path": os.path.join("packages", "skills")},
                      r["candidates"])

    def test_lowercase_skill_md_marker_is_detected(self):
        # _iter_items 의 `if "SKILL.md" in names` 는 대소문자를 구분한다.
        # 카테고리 디렉토리만 고치면 skill.md 를 쓴 레포는 여전히 안 잡힌다.
        import remote_fetch
        self.mk("kit", "my-skill", file="skill.md")
        r = remote_fetch.detect_layout(self.tmp)
        self.assertIn({"category": "skills", "path": "kit"}, r["candidates"])

    def test_empty_repo_yields_nothing_but_does_not_raise(self):
        import remote_fetch
        r = remote_fetch.detect_layout(self.tmp)
        self.assertEqual(r["found"], [])
        self.assertEqual(r["candidates"], [])
        self.assertEqual(r["map"], {})


def _git(cwd, *args):
    subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class Materialize(unittest.TestCase):
    """fetch 는 네트워크 대신 로컬 git 픽스처 레포로 검증한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mat_test_")
        self.origin = os.path.join(self.tmp, "origin")
        os.makedirs(os.path.join(self.origin, "skills", "s1"))
        os.makedirs(os.path.join(self.origin, "plugins", "p1"))
        for p, body in ((os.path.join("skills", "s1", "SKILL.md"), "---\nname: s1\n---\nbody\n"),
                        (os.path.join("plugins", "p1", "note.md"), "plugin file\n"),
                        ("README.md", "readme\n")):
            with open(os.path.join(self.origin, p), "w", encoding="utf-8") as f:
                f.write(body)
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.origin,
                                   capture_output=True, text=True).stdout.strip()
        self.url = "file:///" + self.origin.replace("\\", "/").lstrip("/")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_materialize_by_ref_returns_resolved_sha(self):
        import remote_fetch
        dest = os.path.join(self.tmp, "cache1")
        sha = remote_fetch.materialize(dest, self.url, ref="main")
        self.assertEqual(sha, self.head)
        self.assertTrue(os.path.exists(os.path.join(dest, "skills", "s1", "SKILL.md")))

    def test_materialize_by_sha_is_reproducible(self):
        import remote_fetch
        dest = os.path.join(self.tmp, "cache2")
        sha = remote_fetch.materialize(dest, self.url, sha=self.head)
        self.assertEqual(sha, self.head)

    def test_sparse_checkout_limits_worktree(self):
        import remote_fetch
        dest = os.path.join(self.tmp, "cache3")
        remote_fetch.materialize(dest, self.url, ref="main", sparse=["plugins/p1"])
        self.assertTrue(os.path.exists(os.path.join(dest, "plugins", "p1", "note.md")))
        self.assertFalse(os.path.exists(os.path.join(dest, "skills", "s1", "SKILL.md")))

    def test_sparse_can_be_expanded_in_place(self):
        # 마켓 지연 확장의 핵심 동작: 매니페스트만 받아둔 클론에 경로를 덧붙인다.
        import remote_fetch
        dest = os.path.join(self.tmp, "cache4")
        remote_fetch.materialize(dest, self.url, ref="main", sparse=["plugins/p1"])
        remote_fetch.materialize(dest, self.url, ref="main", sparse=["plugins/p1", "skills"])
        self.assertTrue(os.path.exists(os.path.join(dest, "skills", "s1", "SKILL.md")))

    def test_bad_url_raises_giterror_without_leaving_partial_cache(self):
        import remote_fetch
        dest = os.path.join(self.tmp, "cache5")
        with self.assertRaises(remote_fetch.GitError):
            remote_fetch.materialize(dest, "file:///definitely/not/a/repo", ref="main")
        self.assertFalse(os.path.exists(os.path.join(dest, ".git", "FETCH_HEAD")))

    def test_autocrlf_is_disabled_in_materialized_clone(self):
        # 줄바꿈 정규화는 내용 해시를 바꿔 허위 modified 를 만든다.
        import remote_fetch
        dest = os.path.join(self.tmp, "cache6")
        remote_fetch.materialize(dest, self.url, ref="main")
        got = subprocess.run(["git", "config", "--local", "core.autocrlf"], cwd=dest,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(got, "false")

    def test_longpaths_is_enabled_in_materialized_clone(self):
        # Windows MAX_PATH(260자) 를 넘는 캐시 경로에서 git 이 자기 내부 파일을
        # "Filename too long" 으로 못 쓰는 걸 막는다 - 외부 플러그인은 캐시 아래
        # <market-id>/plugins/<name>/<40자 sha>/ 로 깊이 파고들어 여유가 금방 잠식된다.
        import remote_fetch
        dest = os.path.join(self.tmp, "cache_longpaths")
        remote_fetch.materialize(dest, self.url, ref="main")
        got = subprocess.run(["git", "config", "--local", "core.longpaths"], cwd=dest,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(got, "true")

    def test_materialize_rejects_malicious_urls_independently_of_marketplace(self):
        # 리뷰 Finding 2: marketplace.py 를 거치지 않는 호출(사용자가 직접 --url 로 넘긴
        # remote-add/market-add)도 같은 방어가 필요하다 - remote_fetch 는 marketplace 를
        # import 하지 않으므로 독립적으로 재검증한다. 이 머신에서 실제로 전송 헬퍼(ext::)를
        # 실행시키지 않기 위해 비실행형 케이스(--옵션 주입, 허용 목록 밖 스킴)로 확인한다.
        # git 이 실제로 불리기 전에 거부돼야 하므로 실패 사유가 검증 메시지인지도 확인한다
        # (pre-fix 에서는 git 자신이 실패해도 같은 GitError 타입이 나와 구분이 안 됐다).
        import remote_fetch
        dest = os.path.join(self.tmp, "cache7")
        with self.assertRaises(remote_fetch.GitError) as cm:
            remote_fetch.materialize(dest, "--upload-pack=evil", ref="main")
        self.assertIn("옵션처럼", str(cm.exception))
        self.assertFalse(os.path.exists(dest))

        dest2 = os.path.join(self.tmp, "cache8")
        with self.assertRaises(remote_fetch.GitError) as cm:
            remote_fetch.materialize(dest2, "ftp://example.invalid/x.git", ref="main")
        self.assertIn("허용 목록", str(cm.exception))
        self.assertFalse(os.path.exists(dest2))

    def test_materialize_rejects_option_like_ref_and_non_hex_sha(self):
        # 메시지로 판별한다: git 자신도 이런 값에 결국 실패하지만(예: sha 불일치로 뒤늦게),
        # 검증이 있으면 git 을 부르기도 전에 우리 메시지로 즉시 죽는다 - 디렉토리 생성 전이므로
        # dest 도 안 남는다(pre-fix 에서는 git 이 일부 파일을 쓴 뒤 실패해 정리가 완벽하지
        # 않을 수 있다 - 그건 이 수정과 무관한 별개의 Windows rmtree 이슈다).
        import remote_fetch
        dest = os.path.join(self.tmp, "cache9")
        with self.assertRaises(remote_fetch.GitError) as cm:
            remote_fetch.materialize(dest, self.url, ref="--upload-pack=evil")
        self.assertIn("유효하지 않음", str(cm.exception))
        self.assertFalse(os.path.exists(dest))

        dest2 = os.path.join(self.tmp, "cache10")
        with self.assertRaises(remote_fetch.GitError) as cm:
            remote_fetch.materialize(dest2, self.url, sha="not-hex!")
        self.assertIn("hex 아님", str(cm.exception))
        self.assertFalse(os.path.exists(dest2))


class ScanRecords(unittest.TestCase):
    """_load_libs 가 레코드를 돌려주고 cmd_scan 이 source/origin 을 방출한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scanrec_test_")
        self.store = os.path.join(self.tmp, "store")
        self.lib = os.path.join(self.tmp, "kit", ".claude")
        self.target = os.path.join(self.tmp, "live")
        os.makedirs(os.path.join(self.lib, "agents"))
        with open(os.path.join(self.lib, "agents", "a1.md"), "w", encoding="utf-8") as f:
            f.write("agent body\n")
        run(CAS, "--store", self.store, "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def libcmd(self, *args):
        return run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot", *args)

    def test_scan_emits_origin_for_local_library(self):
        rc, out, err = self.libcmd("scan", "--lib", self.lib)
        self.assertEqual(rc, 0, err)
        l = json.loads(out)["libraries"][0]
        self.assertEqual(l["source"], "registered")
        self.assertEqual(l["origin"], "local:" + os.path.normcase(os.path.normpath(self.lib)))
        self.assertEqual(l["categories"]["agents"][0]["origin"], l["origin"])

    def test_uppercase_category_dirs_are_scanned_via_map(self):
        # my-tools 레이아웃: Agents/ Skills/. 대소문자 구분 FS 에서도 잡혀야 한다.
        up = os.path.join(self.tmp, "upkit")
        os.makedirs(os.path.join(up, "Skills", "s1"))
        with open(os.path.join(up, "Skills", "s1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: s1\n---\n")
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        cfg.setdefault("remotes", []).append(
            {"id": "upkit", "url": "u", "cache": up, "map": {"skills": "Skills"}})
        lib_store.save_cfg(self.store, cfg)
        rc, out, err = self.libcmd("scan")
        self.assertEqual(rc, 0, err)
        rec = [l for l in json.loads(out)["libraries"] if l["lib"] == up][0]
        self.assertEqual(rec["source"], "remote")
        self.assertEqual(rec["origin"], "remote:upkit")
        self.assertEqual([i["name"] for i in rec["categories"]["skills"]], ["s1"])

    def test_missing_remote_cache_reports_error_row_not_silence(self):
        # 캐시가 사라져도 '행 자체'는 남아야 사용자가 다시 받아올 수 있다.
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        gone = os.path.join(self.tmp, "gone")
        cfg.setdefault("remotes", []).append({"id": "gone", "url": "u", "cache": gone})
        lib_store.save_cfg(self.store, cfg)
        rc, out, err = self.libcmd("scan")
        self.assertEqual(rc, 0, err)
        rec = [l for l in json.loads(out)["libraries"] if l["lib"] == gone][0]
        self.assertEqual(rec["error"], "경로 없음")
        self.assertEqual(rec["source"], "remote")

    def test_scan_stays_offline(self):
        # scan 이 git 을 부르면 refreshLibrary 가 매 새로고침마다 멈춘다.
        # --lib 를 쓰면 fast-path 라 _load_libs/remotes[] 자체가 안 돈다 - 이 테스트는
        # 아무것도 검증 못 하고 항상 통과한다. remotes[] 를 심고 --lib 없이 돌려서
        # 실제로 remotes[] 를 순회하는 경로(나중에 누가 fetch 를 여기 넣어도 잡히는 경로)를 태운다.
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        cfg.setdefault("remotes", []).append(
            {"id": "offlinekit", "url": "u", "cache": self.lib})
        lib_store.save_cfg(self.store, cfg)
        rc, out, err = run(LIB, "--store", self.store, "--target", self.target,
                           "--no-snapshot", "scan", env={"PATH": ""})
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])

    def test_install_with_lib_and_origin_resolves_remote_record_not_local(self):
        # UI 조합(Task 9): install 이 --lib 에 캐시 경로, --origin 에 remote:<id> 를 함께 보낸다.
        # --lib 를 무조건 local: 로 재합성하면 --origin 필터가 항상 빈 결과가 되어
        # 원격 레포 설치가 전부 실패한다(리뷰에서 재현된 회귀). 대문자 카테고리 디렉토리(map)도
        # --lib 재합성 시 map 이 None 으로 날아가면 마찬가지로 못 찾는다 - 함께 검증한다.
        up = os.path.join(self.tmp, "upkit2")
        os.makedirs(os.path.join(up, "Agents"))
        with open(os.path.join(up, "Agents", "a1.md"), "w", encoding="utf-8") as f:
            f.write("agent body\n")
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        cfg.setdefault("remotes", []).append(
            {"id": "upkit2", "url": "u", "cache": up, "map": {"agents": "Agents"}})
        lib_store.save_cfg(self.store, cfg)
        rc, out, err = self.libcmd("install", "agents", "a1", "--lib", up, "--origin", "remote:upkit2")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual(res["origin"], "remote:upkit2")
        self.assertTrue(os.path.exists(os.path.join(self.target, "agents", "a1.md")))
        cfg2 = lib_store.load_cfg(self.store)
        rec = lib_store.ledger_get(cfg2, self.target, "agents", "a1")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["origin"], "remote:upkit2")   # local:... 이면 회귀


class ScanEnumerationErrors(unittest.TestCase):
    """fix round 2: os.walk 나열 실패(onerror=None 기본값)가 조용히 '항목 0개' 로 둔갑하던
    버그. 진짜 빈 라이브러리(예외 없음)와 나열 실패(예외 있음)는 구분돼야 한다.

    프로세스 안에서 cmd_scan 을 직접 호출하고 os.walk 를 부분적으로 몽키패치한다 - 실제
    260+ 문자 경로를 만들지 않고도 같은 실패 모양(OSError)을 재현하기 위해서다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="scanerr_test_")
        self.store = os.path.join(self.tmp, "store")
        self.target = os.path.join(self.tmp, "live")
        self.lib = os.path.join(self.tmp, "kit", ".claude")
        os.makedirs(os.path.join(self.lib, "skills", "s1"))
        with open(os.path.join(self.lib, "skills", "s1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: s1\n---\n")
        os.makedirs(os.path.join(self.lib, "agents"))
        with open(os.path.join(self.lib, "agents", "a1.md"), "w", encoding="utf-8") as f:
            f.write("agent\n")
        run(CAS, "--store", self.store, "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scan_inprocess(self, lib):
        import argparse, contextlib, io, library
        a = argparse.Namespace(store=self.store, target=self.target, lib=lib)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            library.cmd_scan(a)
        return json.loads(buf.getvalue())

    def test_enumeration_failure_surfaces_error_not_zero_items(self):
        import os as _os
        from unittest import mock
        broken = _os.path.normcase(_os.path.normpath(_os.path.join(self.lib, "skills")))
        real_walk = _os.walk

        def flaky_walk(path, *args, **kwargs):
            if _os.path.normcase(_os.path.normpath(path)) == broken:
                def _gen():
                    raise OSError("simulated: enumeration failed (path too long)")
                    yield  # pragma: no cover - never reached, keeps this a generator
                return _gen()
            return real_walk(path, *args, **kwargs)

        with mock.patch("os.walk", side_effect=flaky_walk):
            res = self._scan_inprocess(self.lib)
        self.assertTrue(res["ok"])
        row = res["libraries"][0]
        self.assertIn("error", row,
                      "나열 실패는 error 로 드러나야 한다 - 항목 0개로 조용히 넘기면 안 됨")
        self.assertEqual(row["categories"]["skills"], [])
        # 한 카테고리의 나열 실패가 다른 카테고리·다른 라이브러리까지 끌고 내려가면 안 된다.
        self.assertEqual([i["name"] for i in row["categories"]["agents"]], ["a1"])

    def test_genuinely_empty_library_reports_zero_items_without_error(self):
        empty_lib = os.path.join(self.tmp, "empty", ".claude")
        os.makedirs(empty_lib)   # agents/skills/commands 서브디렉토리 자체가 없다
        res = self._scan_inprocess(empty_lib)
        self.assertTrue(res["ok"])
        row = res["libraries"][0]
        self.assertNotIn("error", row,
                         "진짜 빈 라이브러리는 error 를 달면 안 된다(나열 실패와 혼동 금지)")
        self.assertEqual(row["categories"], {"agents": [], "skills": [], "commands": []})


class ComponentCounting(unittest.TestCase):
    """fix round 3: _count_components 는 '진짜 0개'와 '못 읽음'을 같은 0 으로 뭉개면 안 된다.
    실패한 카테고리는 counts 에 아예 안 나타나고 failed 로만 갈라져야 한다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cc_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_count_components_distinguishes_zero_from_unreadable(self):
        import library
        from unittest import mock
        os.makedirs(os.path.join(self.tmp, "skills", "s1"))
        with open(os.path.join(self.tmp, "skills", "s1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: s1\n---\n")
        os.makedirs(os.path.join(self.tmp, "commands"))   # 존재하지만 진짜 비어 있음
        # agents/ 는 아예 안 만든다 - 그것도 "진짜 없음" 의 또 다른 모양이다.
        broken = os.path.normcase(os.path.normpath(os.path.join(self.tmp, "skills")))
        real_walk = os.walk

        def flaky_walk(path, *args, **kwargs):
            if os.path.normcase(os.path.normpath(path)) == broken:
                def _gen():
                    raise OSError("simulated: enumeration failed (path too long)")
                    yield  # pragma: no cover - never reached, keeps this a generator
                return _gen()
            return real_walk(path, *args, **kwargs)

        with mock.patch("os.walk", side_effect=flaky_walk):
            result = library._count_components(self.tmp)

        self.assertEqual(result["counts"]["commands"], 0, "진짜 빈 카테고리는 0 이어야 한다")
        self.assertEqual(result["counts"]["agents"], 0, "디렉토리 자체가 없는 것도 0 이어야 한다")
        self.assertNotIn("skills", result["counts"],
                         "나열 실패한 카테고리는 counts 에 0 으로 나타나면 안 된다")
        self.assertIn("skills", result["failed"], "나열 실패는 failed 에 사유와 함께 남아야 한다")
        self.assertTrue(result["failed"]["skills"])   # 사유 문자열이 비어 있지 않다


class ConflictStatus(unittest.TestCase):
    """이름 충돌: 원장이 소유자를 기억해 두 번째 출처의 설치를 조용한 덮어쓰기가 아니라 conflict 로 만든다."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="conflict_test_")
        self.store = os.path.join(self.tmp, "store")
        self.target = os.path.join(self.tmp, "live")
        os.makedirs(self.target)
        self.libA = self._mkkit("kitA", "from A\n")
        self.libB = self._mkkit("kitB", "from B\n")
        run(CAS, "--store", self.store, "init")

    def _mkkit(self, name, body):
        d = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(d, "agents"))
        with open(os.path.join(d, "agents", "code-reviewer.md"), "w", encoding="utf-8") as f:
            f.write(body)
        return d

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def libcmd(self, *args):
        return run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot", *args)

    def _status_of(self, lib):
        rc, out, err = self.libcmd("scan", "--lib", lib)
        self.assertEqual(rc, 0, err)
        return json.loads(out)["libraries"][0]["categories"]["agents"][0]

    def test_second_origin_sees_conflict_not_modified(self):
        # 이 기능의 존재 이유. B 는 예전이라면 'modified'(=동기화 가능)로 보여서
        # 누르면 A 를 조용히 덮어썼다.
        self.libcmd("scan", "--lib", self.libA)
        rc, out, err = self.libcmd("install", "agents", "code-reviewer", "--lib", self.libA)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._status_of(self.libA)["status"], "installed")

        it = self._status_of(self.libB)
        self.assertEqual(it["status"], "conflict")
        self.assertEqual(it["owner"], "local:" + os.path.normcase(os.path.normpath(self.libA)))

    def test_ledger_absent_falls_back_to_hash_compare(self):
        # 하위호환: 기능 도입 전 설치분/대시보드 밖에서 생긴 것은 오늘과 동일하게 동작.
        os.makedirs(os.path.join(self.target, "agents"), exist_ok=True)
        with open(os.path.join(self.target, "agents", "code-reviewer.md"), "w", encoding="utf-8") as f:
            f.write("from A\n")
        self.assertEqual(self._status_of(self.libA)["status"], "installed")
        self.assertEqual(self._status_of(self.libB)["status"], "modified")

    def test_uninstall_refuses_when_origin_mismatches_owner(self):
        self.libcmd("scan", "--lib", self.libA)
        self.libcmd("install", "agents", "code-reviewer", "--lib", self.libA)
        rc, out, err = self.libcmd("uninstall", "agents", "code-reviewer",
                                   "--origin", "local:" + os.path.normcase(os.path.normpath(self.libB)))
        self.assertNotEqual(rc, 0)
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("local:", res.get("owner", ""))
        self.assertTrue(os.path.exists(os.path.join(self.target, "agents", "code-reviewer.md")),
                        "거부했으면 파일이 남아 있어야 한다")

    def test_uninstall_clears_ledger_entry(self):
        self.libcmd("scan", "--lib", self.libA)
        self.libcmd("install", "agents", "code-reviewer", "--lib", self.libA)
        rc, out, err = self.libcmd("uninstall", "agents", "code-reviewer")
        self.assertEqual(rc, 0, err)
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        self.assertIsNone(lib_store.ledger_get(cfg, self.target, "agents", "code-reviewer"))
        self.assertEqual(self._status_of(self.libB)["status"], "not_installed")

    def test_overwrite_after_conflict_transfers_ownership(self):
        self.libcmd("scan", "--lib", self.libA)
        self.libcmd("install", "agents", "code-reviewer", "--lib", self.libA)
        rc, out, err = self.libcmd("install", "agents", "code-reviewer", "--lib", self.libB)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._status_of(self.libB)["status"], "installed")
        self.assertEqual(self._status_of(self.libA)["status"], "conflict")   # 이제 A 가 남


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class RemoteAdd(unittest.TestCase):
    """remote-add: clone -> 레이아웃 탐지 -> 등록 영속화. 등록 실패는 ok:false 로 보고."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="remoteadd_test_")
        self.store = os.path.join(self.tmp, "store")
        self.target = os.path.join(self.tmp, "live")
        self.origin = os.path.join(self.tmp, "origin")
        os.makedirs(os.path.join(self.origin, "Skills", "s1"))
        os.makedirs(os.path.join(self.origin, "Agents"))
        with open(os.path.join(self.origin, "Skills", "s1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: s1\n---\nbody\n")
        with open(os.path.join(self.origin, "Agents", "a1.md"), "w", encoding="utf-8") as f:
            f.write("agent\n")
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        self.url = "file:///" + self.origin.replace("\\", "/").lstrip("/")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def libcmd(self, *args):
        return run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot", *args)

    def test_remote_add_registers_and_detects_uppercase_layout(self):
        run(CAS, "--store", self.store, "init")
        rc, out, err = self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["id"], "origin")                 # URL 에서 레포명 파생
        self.assertEqual(res["origin"], "remote:origin")
        self.assertEqual(sorted(res["layout"]["found"]), ["agents", "skills"])
        self.assertEqual(res["layout"]["map"]["skills"], "Skills")
        self.assertTrue(os.path.isdir(res["cache"]))
        # 등록이 영속돼 다음 scan 이 --lib 없이 잡는다
        rc, out, err = self.libcmd("scan")
        rec = [l for l in json.loads(out)["libraries"] if l["source"] == "remote"][0]
        self.assertEqual([i["name"] for i in rec["categories"]["skills"]], ["s1"])

    def test_remote_add_reports_store_uninitialized_instead_of_lying(self):
        # 회귀 가드: 예전 _register_lib 은 조용히 False 를 반환했고 UI 는 '등록됨' 토스트를 띄웠다.
        rc, out, err = self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("초기화", res["message"])

    def test_remote_add_is_idempotent_on_same_id(self):
        run(CAS, "--store", self.store, "init")
        self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        self.assertEqual(rc, 0, err)
        import lib_store
        self.assertEqual(len(lib_store.load_cfg(self.store)["remotes"]), 1)

    def test_remote_add_without_git_fails_clearly(self):
        run(CAS, "--store", self.store, "init")
        rc, out, err = run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot",
                           "remote-add", "--url", self.url, "--ref", "main", env={"PATH": ""})
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("git", res["message"])


class Manifest(unittest.TestCase):
    """매니페스트 파싱 + renames 는 조회 해석 전용(구→신 단방향)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mf_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, obj):
        d = os.path.join(self.tmp, ".claude-plugin")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "marketplace.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(obj, f)
        return p

    def test_parse_indexes_plugins_by_name(self):
        import marketplace
        p = self.write({"name": "official", "plugins": [
            {"name": "convex", "displayName": "Convex", "source": "./plugins/convex"},
            {"name": "superpowers", "source": {"source": "url", "url": "u", "sha": "s"}}]})
        mf = marketplace.parse_manifest(p)
        self.assertEqual(mf["name"], "official")
        self.assertEqual(sorted(mf["by_name"]), ["convex", "superpowers"])

    def test_renames_resolve_old_name_to_current_entry(self):
        # 업스트림 개명 후에도 원장의 옛 origin 이 붕 뜨지 않아야 한다.
        import marketplace
        p = self.write({"name": "official",
                        "plugins": [{"name": "convex", "source": "./plugins/convex"}],
                        "renames": {"convex-backend": "convex"}})
        mf = marketplace.parse_manifest(p)
        entry, canon = marketplace.resolve_plugin(mf, "convex-backend")
        self.assertEqual(canon, "convex")          # 신 이름으로 정규화해 돌려준다
        self.assertEqual(entry["name"], "convex")

    def test_renames_does_not_resolve_new_to_old(self):
        # 방향이 반대면 안 된다(key=구, value=신). 예전 버전은 resolve_plugin(mf, "nope") 로
        # "매니페스트 어디에도 없는 이름"을 던졌는데, 정방향 전용이든 버그로 역방향까지
        # 지원하든 결과가 똑같이 (None, None) 이라 실제로는 방향을 판별하지 못했다(리뷰 지적).
        # 판별 가능한 픽스처: old-name 엔트리를 실제로 살려 둬서, 역방향 해석 버그가 있으면
        # "new-name" 조회가 old-name 엔트리로 튀는 게 관측되게 만든다.
        import marketplace
        p = self.write({"name": "o",
                        "plugins": [{"name": "convex", "source": "./p"},
                                    {"name": "old-name", "source": "./p"}],
                        "renames": {"convex-backend": "convex", "old-name": "new-name"}})
        mf = marketplace.parse_manifest(p)
        # "convex"(신 이름)를 직접 조회하면 hop 없이 그 자신이 canonical 이어야 한다.
        entry, canon = marketplace.resolve_plugin(mf, "convex")
        self.assertEqual((canon, entry["name"]), ("convex", "convex"))
        # "new-name" 은 renames 의 값으로만 존재한다(정방향: old-name -> new-name).
        # 역방향(신->구) 해석이 있다면 old-name 엔트리(실재함)로 튀어 (entry, "old-name") 를
        # 반환할 것이다 - 정방향 전용이면 by_name 에 "new-name" 이 없으므로 (None, None).
        self.assertEqual(marketplace.resolve_plugin(mf, "new-name"), (None, None))

    def test_rename_chain_does_not_loop_forever(self):
        import marketplace
        p = self.write({"name": "o", "plugins": [{"name": "c", "source": "./p"}],
                        "renames": {"a": "b", "b": "a"}})     # 순환
        mf = marketplace.parse_manifest(p)
        self.assertEqual(marketplace.resolve_plugin(mf, "a"), (None, None))

    def test_display_name_prefers_displayName(self):
        import marketplace
        self.assertEqual(marketplace.display_name({"name": "convex", "displayName": "Convex"}), "Convex")
        self.assertEqual(marketplace.display_name({"name": "qodo"}), "qodo")

    def test_malformed_manifest_raises_manifesterror(self):
        import marketplace
        p = self.write({"name": "o"})                          # plugins 없음
        with self.assertRaises(marketplace.ManifestError):
            marketplace.parse_manifest(p)

    def test_malformed_entry_name_is_skipped_not_indexed(self):
        # 이름 자체가 경로 이탈("..")이면 by_name 에 아예 안 실린다 - resolve_plugin 이
        # 원천적으로 도달 못 하게 한다(리뷰 Finding 1).
        import marketplace
        p = self.write({"name": "o", "plugins": [
            {"name": "..", "source": "./p"},
            {"name": "ok", "source": "./p"}]})
        mf = marketplace.parse_manifest(p)
        self.assertEqual(sorted(mf["by_name"]), ["ok"])

    def test_resolve_plugin_rejects_unsafe_canonical_name_from_renames(self):
        # 리뷰 Finding 1 재현: renames 타깃이 경로 이탈이면 그 이름으로 해석해서는 안 된다.
        # plugins[] 에 같은 이름의 엔트리가 있어도(정상이라면 있을 수 없지만, 매니페스트는
        # 신뢰할 수 없는 입력이므로 있다고 가정한다) 여전히 거부해야 한다 - by_name 인덱싱과
        # resolve_plugin 양쪽에서 막는다(방어 심층화).
        import marketplace
        p = self.write({"name": "o",
                        "plugins": [{"name": "../../ESCAPED", "source": "./p"}],
                        "renames": {"good": "../../ESCAPED"}})
        mf = marketplace.parse_manifest(p)
        self.assertNotIn("../../ESCAPED", mf["by_name"])      # by_name 인덱싱 단계에서 걸러짐
        self.assertEqual(marketplace.resolve_plugin(mf, "good"), (None, None))

    def test_resolve_plugin_ignores_non_string_rename_target(self):
        # 매니페스트가 이상해도(rename 값이 dict 등 unhashable) TypeError 로 죽지 않고
        # (None, None) 이어야 한다 - cmd_plugin_fetch 가 이 예외를 잡지 않는다.
        import marketplace
        p = self.write({"name": "o", "plugins": [{"name": "c", "source": "./p"}],
                        "renames": {"a": {"b": "c"}}})
        mf = marketplace.parse_manifest(p)
        self.assertEqual(marketplace.resolve_plugin(mf, "a"), (None, None))


class PathGuards(unittest.TestCase):
    """매니페스트 source.path / 플러그인 이름은 신뢰할 수 없는 입력이다."""

    BAD_NAMES = ["..", ".", "", "a/b", "a\\b", "C:foo", "a:b", "/abs", "..\\evil"]
    BAD_PATHS = ["../outside", "plugins/../../etc", "/abs/path", "C:foo\\x",
                 "plugins/a:b", "plugins//", "plugins/./x"]

    def test_bad_plugin_names_rejected(self):
        import marketplace
        for n in self.BAD_NAMES:
            with self.subTest(name=n), self.assertRaises(marketplace.ManifestError):
                marketplace.safe_segment(n)

    def test_good_plugin_names_pass(self):
        import marketplace
        for n in ["superpowers", "agent-sdk-dev", "wordpress.com", "a_b", "qodo-skills"]:
            with self.subTest(name=n):
                self.assertEqual(marketplace.safe_segment(n), n)

    def test_trailing_dot_space_segments_rejected(self):
        # 리뷰 Finding 3: os.path.normpath 는 트레일링 dot/space 를 먹어치우므로
        # 이 가드의 판정이 OS 의 실제 해석과 어긋나면 안 된다. rstrip(" .\t") 결과가
        # 빈 문자열/./.. 이면 거부한다.
        import marketplace
        for n in ["..", ".. ", "..\t", "...", ". ", " ", "\t", ".  ."]:
            with self.subTest(name=repr(n)), self.assertRaises(marketplace.ManifestError):
                marketplace.safe_segment(n)

    def test_reserved_device_names_rejected_case_and_extension_insensitive(self):
        # CON/PRN/AUX/NUL/COM1-9/LPT1-9 는 확장자·대소문자 무관하게 예약됨(경로 이탈이
        # 아니라 기능적 장애 - 그런 이름으로 파일을 못 만든다).
        import marketplace
        for n in ["CON", "con", "Con.txt", "NUL", "nul.md", "COM1", "com9.json", "LPT3", "lpt9.txt"]:
            with self.subTest(name=n), self.assertRaises(marketplace.ManifestError):
                marketplace.safe_segment(n)

    def test_names_resembling_reserved_prefix_still_pass(self):
        # "con" 으로 시작하지만 예약어 자체가 아닌 이름까지 막으면 과잉 차단이다.
        import marketplace
        for n in ["conquest", "nullable", "communication", "console-app"]:
            with self.subTest(name=n):
                self.assertEqual(marketplace.safe_segment(n), n)

    def test_bad_relpaths_rejected(self):
        import marketplace
        for p in self.BAD_PATHS:
            with self.subTest(path=p), self.assertRaises(marketplace.ManifestError):
                marketplace.safe_relpath(p)

    def test_good_relpaths_pass_and_strip_dot_prefix(self):
        import marketplace
        self.assertEqual(marketplace.safe_relpath("./plugins/agent-sdk-dev"), "plugins/agent-sdk-dev")
        self.assertEqual(marketplace.safe_relpath("plugins/creative-cloud/adobe"), "plugins/creative-cloud/adobe")

    def test_source_spec_covers_all_four_kinds(self):
        # url/sha 는 실제로 검증되므로(리뷰 Finding 2) 플레이스홀더 "u"/"s" 대신
        # 스킴이 있는 URL 과 hex sha 를 쓴다 - 이 테스트가 검증하려는 대상(4종 판별)과
        # 무관한 값이라 결과는 이전과 동일하다.
        import marketplace
        self.assertEqual(marketplace.source_spec({"source": "./plugins/p"})["kind"], "str-path")
        gs = marketplace.source_spec({"source": {"source": "git-subdir", "url": "https://example.invalid/x.git",
                                                 "path": "plugins/x", "ref": "v1", "sha": "abc"}})
        self.assertEqual((gs["kind"], gs["path"], gs["sha"]), ("git-subdir", "plugins/x", "abc"))
        self.assertEqual(marketplace.source_spec(
            {"source": {"source": "url", "url": "https://example.invalid/x.git", "sha": "deadbeef"}})["kind"], "url")
        gh = marketplace.source_spec({"source": {"source": "github", "repo": "o/r",
                                                "commit": "c", "sha": "deadbeef"}})
        self.assertEqual((gh["kind"], gh["url"]), ("github", "https://github.com/o/r.git"))

    def test_github_repo_is_validated_as_two_segments(self):
        import marketplace
        with self.assertRaises(marketplace.ManifestError):
            marketplace.source_spec({"source": {"source": "github", "repo": "../evil", "sha": "deadbeef"}})

    def test_source_spec_rejects_malicious_urls(self):
        # 리뷰 Finding 2 재현: git 이 스스로 해석하는 문자열이라 subprocess 의 인자 리스트
        # 격리가 안 통한다. ext:: 는 전송 헬퍼(명령 실행), --upload-pack= 은 git 옵션 주입,
        # 그 외는 허용 스킴(https/http/ssh/git/file/scp-style) 밖의 임의 스킴이다.
        import marketplace
        bad_urls = [
            "ext::sh -c 'touch /tmp/pwned'",
            "--upload-pack=evil",
            "ftp://example.invalid/x.git",
            "javascript:alert(1)",
        ]
        for kind_src in ("url", "git-subdir"):
            for u in bad_urls:
                spec = {"source": kind_src, "url": u, "sha": "deadbeef"}
                if kind_src == "git-subdir":
                    spec["path"] = "plugins/x"
                with self.subTest(kind=kind_src, url=u), self.assertRaises(marketplace.ManifestError):
                    marketplace.source_spec({"source": spec})

    def test_source_spec_accepts_scp_style_and_common_schemes(self):
        import marketplace
        for u in ["https://example.invalid/x.git", "http://example.invalid/x.git",
                  "ssh://git@example.invalid/x.git", "git://example.invalid/x.git",
                  "file:///tmp/x", "git@github.com:owner/repo.git"]:
            with self.subTest(url=u):
                self.assertEqual(marketplace.source_spec(
                    {"source": {"source": "url", "url": u}})["url"], u)

    def test_source_spec_ipv6_url_not_falsely_rejected_as_transport_helper(self):
        # "::" 서브스트링만 보면 IPv6 리터럴 URL 을 전송 헬퍼로 오판한다 - 앵커된 접두 검사여야 한다.
        import marketplace
        self.assertEqual(marketplace.source_spec(
            {"source": {"source": "url", "url": "https://[::1]/r.git"}})["url"], "https://[::1]/r.git")

    def test_source_spec_rejects_option_like_ref_and_non_hex_sha(self):
        import marketplace
        with self.assertRaises(marketplace.ManifestError):
            marketplace.source_spec({"source": {"source": "url", "url": "https://example.invalid/x.git",
                                                "ref": "--upload-pack=evil"}})
        with self.assertRaises(marketplace.ManifestError):
            marketplace.source_spec({"source": {"source": "url", "url": "https://example.invalid/x.git",
                                                "sha": "not-hex!"}})


class Catalog(unittest.TestCase):
    """카탈로그: 메타데이터만. 네트워크 0, 'installable N' 칼럼 없음."""

    def setUp(self):
        import marketplace
        self.mp = marketplace
        self.mf = {"name": "official", "renames": {}, "by_name": {}, "plugins": [
            {"name": "alpha", "description": "A dev tool", "category": "development",
             "author": "x", "source": "./plugins/alpha"},
            {"name": "beta", "description": "DB helper", "category": "database",
             "source": {"source": "url", "url": "https://example.invalid/beta.git", "sha": "deadbeefcafe"}},
            {"name": "gamma", "description": "another dev thing", "category": "development",
             "source": "./plugins/gamma"},
        ]}
        self.mf["by_name"] = {e["name"]: e for e in self.mf["plugins"]}

    def test_rows_carry_metadata_but_no_component_count(self):
        r = self.mp.catalog(self.mf, {})
        self.assertEqual(r["total"], 3)
        row = [x for x in r["rows"] if x["name"] == "beta"][0]
        self.assertEqual(row["category"], "database")
        self.assertEqual(row["kind"], "url")
        self.assertNotIn("installable", row)      # fetch 전에는 알 수 없다
        self.assertFalse(row["fetched"])

    def test_category_filter_and_counts(self):
        r = self.mp.catalog(self.mf, {}, category="development")
        self.assertEqual([x["name"] for x in r["rows"]], ["alpha", "gamma"])
        self.assertEqual(r["categories"]["development"], 2)

    def test_query_matches_name_and_description_case_insensitively(self):
        self.assertEqual([x["name"] for x in self.mp.catalog(self.mf, {}, query="DEV")["rows"]],
                         ["alpha", "gamma"])
        self.assertEqual([x["name"] for x in self.mp.catalog(self.mf, {}, query="db")["rows"]], ["beta"])

    def test_pagination_is_stable(self):
        r1 = self.mp.catalog(self.mf, {}, limit=2, offset=0)
        r2 = self.mp.catalog(self.mf, {}, limit=2, offset=2)
        self.assertEqual([x["name"] for x in r1["rows"]], ["alpha", "beta"])
        self.assertEqual([x["name"] for x in r2["rows"]], ["gamma"])
        self.assertEqual(r1["total"], 3)

    def test_fetched_plugins_are_marked(self):
        r = self.mp.catalog(self.mf, {"alpha": {"sha": "abc1234", "cache": "/c"}})
        row = [x for x in r["rows"] if x["name"] == "alpha"][0]
        self.assertTrue(row["fetched"])
        self.assertEqual(row["sha"], "abc1234")
        self.assertEqual(row["origin"], "market:official/alpha")

    def test_malformed_entry_name_excluded_from_rows(self):
        # 리뷰 Finding 1: plugins[] 이름이 ".." 처럼 경로 이탈이면 카탈로그 행에도
        # 노출하지 않는다(집계에서도 제외) - by_name 필터와 별개로 catalog() 자체가 방어한다.
        mf = {"name": "official", "renames": {}, "by_name": {}, "plugins": [
            {"name": "..", "description": "evil", "category": "x", "source": "./p"},
            {"name": "ok", "description": "fine", "category": "x", "source": "./p"},
        ]}
        r = self.mp.catalog(mf, {})
        self.assertEqual([x["name"] for x in r["rows"]], ["ok"])
        self.assertEqual(r["total"], 1)


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class MarketAdd(unittest.TestCase):
    """market-add: .claude-plugin 만 sparse checkout -> 카탈로그. catalog 는 네트워크 0."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="market_test_")
        self.store = os.path.join(self.tmp, "store")
        self.target = os.path.join(self.tmp, "live")
        self.origin = os.path.join(self.tmp, "mk")
        os.makedirs(os.path.join(self.origin, ".claude-plugin"))
        # 번들 플러그인 1개(str-path) + 외부 1개(url)
        os.makedirs(os.path.join(self.origin, "plugins", "bundled", "skills", "bs1"))
        with open(os.path.join(self.origin, "plugins", "bundled", "skills", "bs1", "SKILL.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\nname: bs1\n---\nbundled skill\n")
        with open(os.path.join(self.origin, ".claude-plugin", "marketplace.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "testmarket", "plugins": [
                {"name": "bundled", "description": "bundled one", "category": "development",
                 "source": "./plugins/bundled"},
                {"name": "external", "description": "remote one", "category": "database",
                 "source": {"source": "url", "url": "https://example.invalid/x.git", "sha": "0" * 40}},
            ]}, f)
        _git(self.origin, "init", "-q", "-b", "main")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        self.url = "file:///" + self.origin.replace("\\", "/").lstrip("/")
        run(CAS, "--store", self.store, "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def libcmd(self, *args):
        return run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot", *args)

    def test_market_add_sparse_checks_out_manifest_only(self):
        rc, out, err = self.libcmd("market-add", "--url", self.url, "--ref", "main")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["id"], "mk")
        self.assertEqual(res["plugins"], 2)
        # 매니페스트는 있고 번들 플러그인 워킹트리는 아직 없다(지연 fetch)
        self.assertTrue(os.path.exists(os.path.join(res["cache"], ".claude-plugin", "marketplace.json")))
        self.assertFalse(os.path.exists(os.path.join(res["cache"], "plugins", "bundled")))

    def test_catalog_needs_no_network(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot",
                           "catalog", env={"PATH": ""})
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual(res["total"], 2)
        self.assertEqual(res["categories"], {"development": 1, "database": 1})

    def test_catalog_filters(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("catalog", "--category", "database")
        self.assertEqual([r["name"] for r in json.loads(out)["rows"]], ["external"])
        rc, out, err = self.libcmd("catalog", "--query", "BUNDLED")
        self.assertEqual([r["name"] for r in json.loads(out)["rows"]], ["bundled"])

    def test_market_does_not_join_library_until_fetched(self):
        # 278개를 Library 칸에 밀어 넣지 않는다. fetch 한 플러그인만 합류한다.
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("scan")
        self.assertEqual(rc, 0, err)
        self.assertEqual([l for l in json.loads(out)["libraries"] if l["source"] == "market"], [])

    def test_catalog_with_no_marketplace_is_empty_not_error(self):
        rc, out, err = self.libcmd("catalog")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["total"], 0)


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class PluginFetch(MarketAdd):
    """플러그인 지연 fetch: 번들은 sparse 확장, 외부는 별도 fetch. sha 층은 원자적 교체용."""

    def test_bundled_plugin_expands_sparse_set_in_place(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["origin"], "market:mk/bundled")
        self.assertEqual(res["components"]["skills"], 1)
        self.assertTrue(os.path.exists(os.path.join(res["cache"], "skills", "bs1", "SKILL.md")))

    def test_fetched_plugin_joins_library_with_market_source(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        rc, out, err = self.libcmd("scan")
        rec = [l for l in json.loads(out)["libraries"] if l["source"] == "market"][0]
        self.assertEqual(rec["origin"], "market:mk/bundled")
        self.assertEqual([i["name"] for i in rec["categories"]["skills"]], ["bs1"])

    def test_refetch_is_idempotent_and_keeps_one_sha_dir(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        self.assertEqual(rc, 0, err)
        import lib_store
        m = lib_store.load_cfg(self.store)["marketplaces"][0]
        self.assertEqual(len(m["plugins"]), 1)
        # 번들(str-path)은 마켓 레포의 sparse 집합을 확장할 뿐 plugins/<name>/<sha> 스테이징을
        # 쓰지 않는다(그건 외부 전용) - 브리프 원문은 plugins_dir 를 검사했지만 번들에서는
        # 그 디렉토리 자체가 생기지 않아 FileNotFoundError 가 난다. 대신 repo 안 같은 경로를
        # 재사용해 늘지 않음을 검증한다.
        mk_root = os.path.join(self.store, "lib-cache", "markets", "mk")
        self.assertFalse(os.path.isdir(os.path.join(mk_root, "plugins")))
        self.assertEqual(m["plugins"][0]["cache"], os.path.join(mk_root, "repo", "plugins", "bundled"))

    def test_unknown_plugin_is_rejected_with_available_hint(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "nope")
        res = json.loads(out)
        self.assertFalse(res["ok"])

    def test_malicious_plugin_name_is_rejected_before_touching_disk(self):
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        for bad in ["../evil", "..", "C:evil", "a/b"]:
            with self.subTest(name=bad):
                rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", bad)
                self.assertFalse(json.loads(out)["ok"])

    def test_external_plugin_failure_leaves_registry_untouched(self):
        # 외부 URL 이 죽어 있어도 마켓 등록과 기존 플러그인은 멀쩡해야 한다.
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "external")
        self.assertFalse(json.loads(out)["ok"])
        import lib_store
        self.assertEqual(lib_store.load_cfg(self.store)["marketplaces"][0]["plugins"], [])

    def test_external_refetch_with_multisegment_path_replaces_stale_sha_dir(self):
        # git-subdir(실측 80/278, source.path 가 흔히 2단 이상)의 옛 sha 정리 회귀 가드.
        # os.path.dirname(old_root) 로 스테이징 루트를 역산하면 다단 경로일 때 sha 루트보다
        # 한 단계 안쪽을 지워, 옛 sha 디렉토리 자체는 재-fetch 할 때마다 쌓여 남는다.
        ext = os.path.join(self.tmp, "ext")
        os.makedirs(os.path.join(ext, "packages", "tool", "skills", "es1"))
        with open(os.path.join(ext, "packages", "tool", "skills", "es1", "SKILL.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\nname: es1\n---\nv1\n")
        _git(ext, "init", "-q", "-b", "main")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "v1")
        sha1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ext,
                              capture_output=True, text=True).stdout.strip()
        ext_url = "file:///" + ext.replace("\\", "/").lstrip("/")

        def write_manifest(sha):
            with open(os.path.join(self.origin, ".claude-plugin", "marketplace.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"name": "testmarket", "plugins": [
                    {"name": "bundled", "description": "bundled one", "category": "development",
                     "source": "./plugins/bundled"},
                    {"name": "multiseg", "description": "multi-segment external", "category": "database",
                     "source": {"source": "git-subdir", "url": ext_url, "ref": "main",
                                "path": "packages/tool", "sha": sha}},
                ]}, f)
            _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
            _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                 "-m", f"manifest sha={sha[:8]}")

        write_manifest(sha1)
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "multiseg")
        self.assertEqual(rc, 0, err)

        with open(os.path.join(ext, "packages", "tool", "skills", "es1", "SKILL.md"),
                  "w", encoding="utf-8") as f:
            f.write("---\nname: es1\n---\nv2\n")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "v2")
        sha2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ext,
                              capture_output=True, text=True).stdout.strip()
        self.assertNotEqual(sha1, sha2)

        write_manifest(sha2)
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "multiseg")
        self.assertEqual(rc, 0, err)

        plugins_dir = os.path.join(self.store, "lib-cache", "markets", "mk", "plugins", "multiseg")
        # fix round 2: 스테이징 디렉토리 이름은 sha 앞 12자만 쓴다(Windows 260자 제한에 여유를
        # 번다) - 원장의 sha 필드 자체는 여전히 전체 40자 그대로다(아래에서 별도 검증).
        self.assertEqual(sorted(os.listdir(plugins_dir)), [sha2[:12]],
                         "옛 sha 스테이징 디렉토리가 안 지워지고 남음(다단 source.path 정리 버그)")
        import lib_store
        rec = lib_store.load_cfg(self.store)["marketplaces"][0]["plugins"][0]
        self.assertEqual(rec["sha"], sha2, "원장의 sha 는 자르지 않고 전체를 저장해야 한다")

    def test_plugin_fetch_reports_failure_when_materialized_root_is_unreadable(self):
        # 실제 260+ 문자 경로를 만들지 않고 같은 실패 모양을 시뮬레이션한다: 매니페스트가
        # 레포에 실제로 없는 하위경로를 선언하면 git fetch/sparse-checkout 은 성공(rc=0)하지만
        # 결과 root 디렉토리는 생기지 않는다 - Windows MAX_PATH 초과로 root 가 안 생기는
        # 케이스와 정확히 같은 증상(materialize 는 "성공"을 보고하지만 root 를 못 읽음).
        ext = os.path.join(self.tmp, "ext_ghost")
        os.makedirs(ext)
        with open(os.path.join(ext, "README.md"), "w", encoding="utf-8") as f:
            f.write("no ghost subdir here\n")
        _git(ext, "init", "-q", "-b", "main")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        ext_url = "file:///" + ext.replace("\\", "/").lstrip("/")

        with open(os.path.join(self.origin, ".claude-plugin", "marketplace.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "testmarket", "plugins": [
                {"name": "ghost", "description": "declared path missing in repo", "category": "development",
                 "source": {"source": "git-subdir", "url": ext_url, "ref": "main", "path": "nope/deep"}},
            ]}, f)
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "ghost plugin")

        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "ghost")
        res = json.loads(out)
        self.assertFalse(res["ok"], "materialize 가 root 를 못 만들었는데 ok:true 를 보고하면 안 된다")
        self.assertIn("찾을 수 없음", res["message"])
        import lib_store
        self.assertEqual(lib_store.load_cfg(self.store)["marketplaces"][0]["plugins"], [],
                         "검증 실패는 원장에 흔적을 남기면 안 된다(거짓 성공 등록 금지)")

    def test_plugin_fetch_warns_when_a_category_cannot_be_enumerated(self):
        # fix round 3: root 자체는 있고(1번 수정 통과) skills/ 나열만 실패하는 상황을
        # 실제 260+ 문자 경로 없이 시뮬레이션한다 - 이미 fetch 된 bundled 캐시에 대해
        # os.walk 를 부분적으로 몽키패치한 뒤 cmd_plugin_fetch 를 프로세스 안에서 재호출한다.
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")

        import argparse, contextlib, io, library
        from unittest import mock
        cache_root = os.path.join(self.store, "lib-cache", "markets", "mk", "repo", "plugins", "bundled")
        broken = os.path.normcase(os.path.normpath(os.path.join(cache_root, "skills")))
        real_walk = os.walk

        def flaky_walk(path, *args, **kwargs):
            if os.path.normcase(os.path.normpath(path)) == broken:
                def _gen():
                    raise OSError("simulated: enumeration failed (path too long)")
                    yield  # pragma: no cover - never reached, keeps this a generator
                return _gen()
            return real_walk(path, *args, **kwargs)

        a = argparse.Namespace(store=self.store, marketplace="mk", plugin="bundled")
        buf = io.StringIO()
        with mock.patch("os.walk", side_effect=flaky_walk), contextlib.redirect_stdout(buf):
            library.cmd_plugin_fetch(a)
        res = json.loads(buf.getvalue())

        self.assertTrue(res["ok"], "fetch 자체는 성공이다 - 파일은 이미 디스크에 있고 등록도 유효하다")
        self.assertNotIn("skills", res["components"],
                         "나열 실패한 카테고리를 0 으로 보고하면 '진짜 비어있음'으로 오독된다")
        self.assertIn("skills", res["components_failed"])
        self.assertIsNotNone(res["warning"], "실패를 아는 사람은 이 응답을 보는 사람도 알아야 한다")
        self.assertIn("skills", res["warning"])

    def test_plugin_fetch_reports_genuinely_empty_category_as_zero_without_warning(self):
        # bundled 픽스처는 skills/bs1 하나만 있고 agents/commands 는 아예 없다 -
        # 이건 나열 실패가 아니라 진짜 빈 카테고리다. round 3 의 구분이 이 경우를
        # 여전히 조용한 0 으로 다뤄야 한다(경고를 남발하면 그것도 오독을 만든다).
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        rc, out, err = self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual(res["components"]["agents"], 0)
        self.assertEqual(res["components"]["commands"], 0)
        self.assertEqual(res["components"]["skills"], 1)
        self.assertEqual(res["components_failed"], {})
        self.assertIsNone(res["warning"])


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class MarketUnregister(MarketAdd):
    """unregister --origin market:<id>/<plugin> 은 그 플러그인만 지운다(fix round 1 회귀 가드).
    market:<id> (플러그인 세그먼트 없음)는 여전히 마켓 전체(레포+모든 플러그인)를 지운다."""

    def _external_fixture(self, name="ext"):
        """실제로 fetch 가능한 로컬 git 픽스처 하나를 만들어 (url, sha) 를 돌려준다."""
        ext = os.path.join(self.tmp, name)
        os.makedirs(os.path.join(ext, "skills", "es1"))
        with open(os.path.join(ext, "skills", "es1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: es1\n---\nbody\n")
        _git(ext, "init", "-q", "-b", "main")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(ext, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ext,
                             capture_output=True, text=True).stdout.strip()
        url = "file:///" + ext.replace("\\", "/").lstrip("/")
        return url, sha

    def _add_two_fetchable_plugins(self):
        """bundled(str-path) + real2(외부, 실제로 fetch 가능) 두 개를 매니페스트에 심고 fetch 까지 한다.
        MarketAdd.setUp 의 "external" 은 죽은 URL(https://example.invalid) 이라 실제 fetch 테스트엔
        못 쓴다 - 여기서는 실제로 물질화되는 두 번째 플러그인이 필요하다."""
        url, sha = self._external_fixture()
        with open(os.path.join(self.origin, ".claude-plugin", "marketplace.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"name": "testmarket", "plugins": [
                {"name": "bundled", "description": "bundled one", "category": "development",
                 "source": "./plugins/bundled"},
                {"name": "real2", "description": "real external", "category": "database",
                 "source": {"source": "url", "url": url, "sha": sha}},
            ]}, f)
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "two real plugins")
        self.libcmd("market-add", "--url", self.url, "--ref", "main")
        self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "bundled")
        self.libcmd("plugin-fetch", "--marketplace", "mk", "--plugin", "real2")

    def test_plugin_level_unregister_removes_only_that_plugin(self):
        self._add_two_fetchable_plugins()
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        real2_cache = next(p["cache"] for p in cfg["marketplaces"][0]["plugins"] if p["name"] == "real2")
        self.assertTrue(os.path.isdir(real2_cache))

        rc, out, err = self.libcmd("unregister", "--origin", "market:mk/real2")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])

        cfg2 = lib_store.load_cfg(self.store)
        self.assertEqual(len(cfg2.get("marketplaces", [])), 1, "마켓 등록 자체는 살아있어야 한다")
        m = cfg2["marketplaces"][0]
        self.assertEqual(m["id"], "mk")
        self.assertEqual([p["name"] for p in m["plugins"]], ["bundled"], "다른 플러그인은 살아있어야 한다")
        self.assertFalse(os.path.exists(real2_cache), "지운 플러그인 캐시는 사라져야 한다")
        self.assertTrue(os.path.exists(os.path.join(m["cache"], ".claude-plugin", "marketplace.json")),
                        "매니페스트 캐시는 살아있어야 한다")
        # bundled 는 여전히 스캔에 걸려야 한다(공유 레포가 안 다쳤다는 증거)
        rc, out, err = self.libcmd("scan")
        recs = [l for l in json.loads(out)["libraries"] if l["source"] == "market"]
        self.assertEqual([r["origin"] for r in recs], ["market:mk/bundled"])

    def test_market_level_unregister_still_removes_everything(self):
        self._add_two_fetchable_plugins()
        import lib_store
        market_cache = lib_store.load_cfg(self.store)["marketplaces"][0]["cache"]
        market_root = os.path.join(self.store, "lib-cache", "markets", "mk")

        rc, out, err = self.libcmd("unregister", "--origin", "market:mk")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        self.assertEqual(lib_store.load_cfg(self.store).get("marketplaces", []), [])
        self.assertFalse(os.path.exists(market_root))
        self.assertFalse(os.path.exists(market_cache))

    def test_plugin_level_unregister_refused_when_its_own_cache_is_held(self):
        self._add_two_fetchable_plugins()
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        real2_cache = next(p["cache"] for p in cfg["marketplaces"][0]["plugins"] if p["name"] == "real2")
        lib_store.ledger_put(cfg, self.target, "hooks", "real2-hook",
                             {"kind": "hooks", "origin": "market:mk/real2", "root": real2_cache})
        lib_store.save_cfg(self.store, cfg)

        rc, out, err = self.libcmd("unregister", "--origin", "market:mk/real2")
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("hooks/real2-hook", json.dumps(res, ensure_ascii=False))
        self.assertTrue(os.path.exists(real2_cache), "거부했으면 캐시가 남아 있어야 한다")
        self.assertEqual(len(lib_store.load_cfg(self.store)["marketplaces"][0]["plugins"]), 2,
                         "거부했으면 등록도 그대로여야 한다")

    def test_plugin_level_unregister_allowed_when_a_different_plugins_cache_is_held(self):
        self._add_two_fetchable_plugins()
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        bundled_cache = next(p["cache"] for p in cfg["marketplaces"][0]["plugins"] if p["name"] == "bundled")
        # bundled 소유의 hooks 가 걸려 있어도 real2 해제는(다른 플러그인 캐시라) 막지 않아야 한다.
        lib_store.ledger_put(cfg, self.target, "hooks", "bundled-hook",
                             {"kind": "hooks", "origin": "market:mk/bundled", "root": bundled_cache})
        lib_store.save_cfg(self.store, cfg)

        rc, out, err = self.libcmd("unregister", "--origin", "market:mk/real2")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        m = lib_store.load_cfg(self.store)["marketplaces"][0]
        self.assertEqual([p["name"] for p in m["plugins"]], ["bundled"])

    def test_bundled_plugin_unregister_does_not_delete_shared_repo_files(self):
        # 번들 플러그인 캐시는 마켓 레포 워킹트리 안이다 - 손으로 지우면 매니페스트나
        # 다른(외부) 플러그인까지 함께 날아갈 수 있다.
        self._add_two_fetchable_plugins()
        import lib_store
        m = lib_store.load_cfg(self.store)["marketplaces"][0]
        manifest_path = os.path.join(m["cache"], ".claude-plugin", "marketplace.json")
        real2_cache = next(p["cache"] for p in m["plugins"] if p["name"] == "real2")

        rc, out, err = self.libcmd("unregister", "--origin", "market:mk/bundled")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        self.assertTrue(os.path.exists(manifest_path), "매니페스트는 살아있어야 한다")
        self.assertTrue(os.path.exists(real2_cache), "다른(외부) 플러그인 캐시는 살아있어야 한다")
        m2 = lib_store.load_cfg(self.store)["marketplaces"][0]
        self.assertEqual([p["name"] for p in m2["plugins"]], ["real2"])


@unittest.skipIf(shutil.which("git") is None, "git 없음")
class FetchAndUnregister(RemoteAdd):
    """명시적 fetch 만(자동 pull 없음) + unregister 는 원장이 붙잡은 캐시를 못 지운다."""

    def test_fetch_updates_sha_and_refreshes_content(self):
        run(CAS, "--store", self.store, "init")
        self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        with open(os.path.join(self.origin, "Agents", "a2.md"), "w", encoding="utf-8") as f:
            f.write("second agent\n")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "more")
        rc, out, err = self.libcmd("fetch", "--origin", "remote:origin")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        rc, out, err = self.libcmd("scan")
        rec = [l for l in json.loads(out)["libraries"] if l["source"] == "remote"][0]
        self.assertEqual(sorted(i["name"] for i in rec["categories"]["agents"]), ["a1", "a2"])

    def test_scan_never_pulls_by_itself(self):
        # 자동 pull 금지: 업스트림이 앞서 나가도 fetch 를 부르기 전까지 캐시는 그대로다.
        run(CAS, "--store", self.store, "init")
        self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        with open(os.path.join(self.origin, "Agents", "a3.md"), "w", encoding="utf-8") as f:
            f.write("third\n")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
        _git(self.origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "third")
        rc, out, err = self.libcmd("scan")
        rec = [l for l in json.loads(out)["libraries"] if l["source"] == "remote"][0]
        self.assertEqual([i["name"] for i in rec["categories"]["agents"]], ["a1"])

    def test_unregister_removes_registration_and_cache(self):
        run(CAS, "--store", self.store, "init")
        rc, out, err = self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        cache = json.loads(out)["cache"]
        rc, out, err = self.libcmd("unregister", "--origin", "remote:origin")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        import lib_store
        self.assertEqual(lib_store.load_cfg(self.store).get("remotes", []), [])
        self.assertFalse(os.path.exists(cache))

    def test_unregister_refuses_when_ledger_still_holds_cache(self):
        # 캐시는 load-bearing 이다(hooks/MCP 가 참조). 붙잡혀 있으면 거부하고 무엇이 걸렸는지 알린다.
        run(CAS, "--store", self.store, "init")
        rc, out, err = self.libcmd("remote-add", "--url", self.url, "--ref", "main")
        cache = json.loads(out)["cache"]
        import lib_store
        cfg = lib_store.load_cfg(self.store)
        lib_store.ledger_put(cfg, self.target, "hooks", "some-hook",
                             {"kind": "hooks", "origin": "remote:origin", "root": cache})
        lib_store.save_cfg(self.store, cfg)
        rc, out, err = self.libcmd("unregister", "--origin", "remote:origin")
        res = json.loads(out)
        self.assertFalse(res["ok"])
        self.assertIn("hooks/some-hook", json.dumps(res, ensure_ascii=False))
        self.assertTrue(os.path.exists(cache), "거부했으면 캐시가 남아 있어야 한다")

    def test_fetch_unknown_origin_is_rejected(self):
        run(CAS, "--store", self.store, "init")
        rc, out, err = self.libcmd("fetch", "--origin", "remote:nope")
        self.assertFalse(json.loads(out)["ok"])
