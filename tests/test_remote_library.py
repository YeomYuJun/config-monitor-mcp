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
