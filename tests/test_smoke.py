#!/usr/bin/env python3
r"""
test_smoke.py - cas.py / claude_config.py / config_edit.py 스모크 테스트.

의존성 0: 표준 unittest 로 작성(pytest 로도 실행됨).
  python -m unittest tests.test_smoke -v      (프로젝트 루트에서)
  python -m pytest tests/test_smoke.py         (pytest 있으면)

E2E 디버깅에서 잡은 회귀를 가드한다:
  - claude_config dump 가 cp949 콘솔에서 UnicodeEncodeError 로 죽지 않는다(P0 원인).
  - cas watcher-status 가 PS 의 BOM + tz-aware heartbeat 를 견딘다.
"""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
CAS = os.path.join(SRC, "cas.py")
CFG = os.path.join(SRC, "claude_config.py")
EDIT = os.path.join(SRC, "config_edit.py")
LIB = os.path.join(SRC, "library.py")


def run(script, *args, env=None):
    """스크립트를 서브프로세스로 실행. (returncode, stdout, stderr) 반환.
    CLAUDE_CAS_NO_DEFAULT_TRACK=1: 테스트 store 에 실사용자 설정파일이
    자동 병합(DEFAULT_TRACKED)되지 않게 격리."""
    e = dict(os.environ)
    e["CLAUDE_CAS_NO_DEFAULT_TRACK"] = "1"
    if env:
        e.update(env)
    p = subprocess.run([sys.executable, script, *args], capture_output=True, text=True,
                       encoding="utf-8", env=e, timeout=60)
    return p.returncode, p.stdout, p.stderr


class CasRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cas_test_")
        self.store = os.path.join(self.tmp, "store")
        self.target = os.path.join(self.tmp, "target.txt")
        with open(self.target, "w", encoding="utf-8") as f:
            f.write("v1 line A\nv1 line B\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cas(self, *args):
        return run(CAS, "--store", self.store, *args)

    def test_full_flow(self):
        rc, out, err = self.cas("init")
        self.assertEqual(rc, 0, err)
        rc, out, err = self.cas("track", self.target)
        self.assertEqual(rc, 0, err)

        rc, out, err = self.cas("snapshot", "-m", "baseline")
        self.assertEqual(rc, 0, err)
        sid = next(tok for tok in out.split() if tok[:8].isdigit() and "T" in tok)

        # status --json 는 순수 JSON 한 줄
        rc, out, err = self.cas("status", "--json")
        self.assertEqual(rc, 0, err)
        st = json.loads(out)
        self.assertIn(self.target, st["unchanged"])

        # 수정 후 modified 감지
        with open(self.target, "w", encoding="utf-8") as f:
            f.write("v2 CHANGED\nv1 line B\n")
        rc, out, _ = self.cas("status", "--json")
        self.assertIn(self.target, json.loads(out)["modified"])

        # diff 텍스트
        rc, out, err = self.cas("diff", self.target, "--from", sid)
        self.assertEqual(rc, 0, err)
        self.assertIn("v2 CHANGED", out)

        # history --json
        rc, out, err = self.cas("history", self.target, "--json")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["revisions"])

        # restore -> 원복 + 백업/되돌림 스냅샷
        rc, out, err = self.cas("restore", self.target, "--from", sid)
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertIsNotNone(res["pre_snapshot"])  # 복원 전 상태 보존
        with open(self.target, encoding="utf-8") as f:
            self.assertEqual(f.read(), "v1 line A\nv1 line B\n")

    def test_cat_tracked_only(self):
        # cat: 추적 파일은 현재 내용 그대로, 추적 밖 경로는 거부.
        self.cas("init")
        self.cas("track", self.target)
        rc, out, err = self.cas("cat", self.target)
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, "v1 line A\nv1 line B\n")
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w") as f:
            f.write("x")
        rc, out, err = self.cas("cat", outside)
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(out)["ok"])

    def test_watcher_status_no_watcher(self):
        self.cas("init")
        rc, out, err = self.cas("watcher-status")
        self.assertEqual(rc, 0, err)
        self.assertFalse(json.loads(out)["running"])

    def test_watcher_status_bom_and_tzaware(self):
        # PS 가 쓰는 형태(UTF-8 BOM + tz-aware "+09:00" heartbeat) 를 견디는지.
        self.cas("init")
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=9))).isoformat()
        state = {"pid": 9999, "started": now, "heartbeat": now, "debounceMs": 2000, "dirs": []}
        wj = os.path.join(self.store, "watcher.json")
        with open(wj, "w", encoding="utf-8-sig") as f:  # BOM 포함
            f.write(json.dumps(state))
        rc, out, err = self.cas("watcher-status")
        self.assertEqual(rc, 0, err)
        s = json.loads(out)
        self.assertTrue(s["running"])      # 방금 heartbeat -> 살아있음
        self.assertIsNotNone(s["age_sec"])  # tz-aware 빼기 성공

    def test_watcher_status_ps_roundtrip_fraction(self):
        # 회귀 가드: PowerShell (Get-Date).ToString("o") 는 소수부 7자리(tick) —
        # Python<3.11 fromisoformat 이 못 읽어 watcher 가 영원히 '정지'로 보였다.
        self.cas("init")
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=9)))
        hb = now.strftime("%Y-%m-%dT%H:%M:%S") + ".1970395+09:00"  # 7자리 소수부
        state = {"pid": 9999, "started": hb, "heartbeat": hb, "debounceMs": 2000, "dirs": []}
        with open(os.path.join(self.store, "watcher.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(state))
        rc, out, err = self.cas("watcher-status")
        self.assertEqual(rc, 0, err)
        s = json.loads(out)
        self.assertIsNone(s["error"], f"7자리 소수부 파싱 실패: {s['error']}")
        self.assertTrue(s["running"])

    def test_default_tracked_merge(self):
        # DEFAULT_TRACKED 자동 병합: 가짜 HOME 으로 존재를 보장해 머신 독립적으로 검증.
        self.cas("init")
        fake_home = os.path.join(self.tmp, "home")
        os.makedirs(os.path.join(fake_home, ".claude"), exist_ok=True)
        with open(os.path.join(fake_home, ".claude.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(fake_home, ".claude", "settings.json"), "w") as f:
            f.write("{}")
        e = dict(os.environ)
        e.pop("CLAUDE_CAS_NO_DEFAULT_TRACK", None)
        e["HOME"] = fake_home
        e["USERPROFILE"] = fake_home
        p = subprocess.run([sys.executable, CAS, "--store", self.store, "status", "--json"],
                           capture_output=True, text=True, encoding="utf-8", env=e, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(os.path.join(self.store, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn(os.path.join(fake_home, ".claude.json"), cfg["tracked"])
        self.assertIn(os.path.join(fake_home, ".claude", "settings.json"), cfg["tracked"])

    def test_untrack_default_not_resurrected(self):
        # untrack 한 기본 대상이 자동 병합으로 되살아나지 않아야 한다(ignore_defaults).
        self.cas("init")
        fake_home = os.path.join(self.tmp, "home")
        os.makedirs(fake_home, exist_ok=True)
        cj = os.path.join(fake_home, ".claude.json")
        with open(cj, "w") as f:
            f.write("{}")
        e = dict(os.environ)
        e.pop("CLAUDE_CAS_NO_DEFAULT_TRACK", None)
        e["HOME"] = fake_home
        e["USERPROFILE"] = fake_home
        def cas_env(*args):
            return subprocess.run([sys.executable, CAS, "--store", self.store, *args],
                                  capture_output=True, text=True, encoding="utf-8", env=e, timeout=60)
        cas_env("status", "--json")     # 병합 발생
        cas_env("untrack", cj)          # 명시 제외
        cas_env("status", "--json")     # 재병합 시도
        with open(os.path.join(self.store, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertNotIn(cj, cfg["tracked"])
        self.assertIn(cj, cfg.get("ignore_defaults", []))


class ConfigEdit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="edit_test_")
        self.settings = os.path.join(self.tmp, "settings.json")
        with open(self.settings, "w", encoding="utf-8") as f:
            json.dump({"permissions": {"allow": ["Bash(ls)"]}}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, *args):
        return run(EDIT, "--settings", self.settings, "--no-snapshot", *args)

    def _load(self):
        with open(self.settings, encoding="utf-8") as f:
            return json.load(f)

    def test_perm_add_remove(self):
        rc, out, err = self.edit("perm-add", "allow", "Bash(git*)")
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        self.assertIn("Bash(git*)", self._load()["permissions"]["allow"])

        rc, out, err = self.edit("perm-remove", "allow", "Bash(git*)")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Bash(git*)", self._load()["permissions"]["allow"])

    def test_hook_add_remove(self):
        rc, out, err = self.edit("hook-add", "PostToolUse", "echo hi", "--matcher", "Edit")
        self.assertEqual(rc, 0, err)
        self.assertTrue(any("echo hi" in json.dumps(h) for h in self._load()["hooks"]["PostToolUse"]))

        rc, out, err = self.edit("hook-remove", "PostToolUse", "echo hi")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._load()["hooks"]["PostToolUse"], [])


class ConfigEditExtended(unittest.TestCase):
    """mcpServers / skills / agents 확장 ops. 실사용자 파일 대신 temp 경로로 격리."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="edit_ext_")
        self.claude_json = os.path.join(self.tmp, "claude.json")
        self.desktop = os.path.join(self.tmp, "desktop_config.json")
        self.skills = os.path.join(self.tmp, "skills")
        self.agents = os.path.join(self.tmp, "agents")
        with open(self.claude_json, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {}, "other": "keep"}, f)
        with open(self.desktop, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {"pre": {"command": "x"}}}, f)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def edit(self, *args):
        return run(EDIT, "--claude-json", self.claude_json, "--desktop-config", self.desktop,
                   "--skills-dir", self.skills, "--agents-dir", self.agents, "--no-snapshot", *args)

    def _load(self, path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_mcp_add_remove_user_scope(self):
        rc, out, err = self.edit("mcp-add", "weather", "--json", '{"command":"npx","args":["-y","weather-mcp"]}')
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        d = self._load(self.claude_json)
        self.assertEqual(d["mcpServers"]["weather"]["command"], "npx")
        self.assertEqual(d["other"], "keep")  # 무관 키 보존

        rc, out, err = self.edit("mcp-remove", "weather")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("weather", self._load(self.claude_json)["mcpServers"])

    def test_mcp_desktop_scope_and_invalid_json(self):
        rc, out, err = self.edit("mcp-add", "s2", "--scope", "desktop", "--json", '{"command":"uv"}')
        self.assertEqual(rc, 0, err)
        d = self._load(self.desktop)
        self.assertIn("s2", d["mcpServers"])
        self.assertIn("pre", d["mcpServers"])  # 기존 서버 보존

        rc, out, err = self.edit("mcp-add", "bad", "--json", "not-json")
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(out)["ok"])

        # remove no-op 은 ok=True/changed=False
        rc, out, err = self.edit("mcp-remove", "ghost")
        self.assertEqual(rc, 0, err)
        self.assertFalse(json.loads(out)["changed"])

    def test_skill_scaffold_remove_roundtrip(self):
        rc, out, err = self.edit("skill-scaffold", "myskill", "--desc", "테스트")
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(os.path.join(self.skills, "myskill", "SKILL.md")))

        rc, out, err = self.edit("skill-remove", "myskill")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertFalse(os.path.exists(os.path.join(self.skills, "myskill")))
        self.assertTrue(os.path.exists(res["trashed"]))  # 복구 가능(.trash 이동)

    def test_agent_scaffold_remove_roundtrip(self):
        rc, out, err = self.edit("agent-scaffold", "reviewer", "--desc", "코드리뷰", "--tools", "Read,Grep")
        self.assertEqual(rc, 0, err)
        md = os.path.join(self.agents, "reviewer.md")
        with open(md, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("tools: Read,Grep", body)

        rc, out, err = self.edit("agent-remove", "reviewer")
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(md))

    def test_path_traversal_rejected(self):
        rc, out, err = self.edit("skill-remove", "../evil")
        self.assertNotEqual(rc, 0)
        self.assertFalse(json.loads(out)["ok"])

    def test_agent_full_content_install(self):
        # --content: 업로드된 완전한 md(frontmatter+본문)를 그대로 설치, 스텁 아님.
        content = ("---\nname: sec\ndescription: 보안 리뷰어\n"
                   'tools: ["Read", "Bash"]\nmodel: sonnet\n---\n\n# 본문\n\n워크플로우 상세.\n')
        rc, out, err = self.edit("agent-scaffold", "sec", "--content", content)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.agents, "sec.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), content)   # 그대로(verbatim) 기록
        # 중복 설치는 거부
        rc, out, err = self.edit("agent-scaffold", "sec", "--content", content)
        self.assertNotEqual(rc, 0)

    def test_skill_full_content_install(self):
        content = "---\nname: myskill\ndescription: d\n---\n\n# 단계\n1. one\n"
        rc, out, err = self.edit("skill-scaffold", "myskill", "--content", content)
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.skills, "myskill", "SKILL.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), content)


class LibraryToggle(unittest.TestCase):
    """library.py: 스캔 3상태 판정 + 설치/동기화/제거 라운드트립."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lib_test_")
        self.store = os.path.join(self.tmp, "store")
        self.lib = os.path.join(self.tmp, "kit", ".claude")
        self.target = os.path.join(self.tmp, "live")
        # 라이브러리 구성: agent 1, skill 1, command 1
        os.makedirs(os.path.join(self.lib, "agents"))
        os.makedirs(os.path.join(self.lib, "skills", "s1"))
        os.makedirs(os.path.join(self.lib, "commands"))
        with open(os.path.join(self.lib, "agents", "a1.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: a1\n---\nagent body\n")
        with open(os.path.join(self.lib, "skills", "s1", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: s1\n---\nskill body\n")
        with open(os.path.join(self.lib, "commands", "c1.md"), "w", encoding="utf-8") as f:
            f.write("command body — ${CLAUDE_PROJECT_DIR} 참조\n")
        # store 초기화 + 라이브러리 등록
        run(CAS, "--store", self.store, "init")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def libcmd(self, *args):
        return run(LIB, "--store", self.store, "--target", self.target, "--no-snapshot", *args)

    def _scan(self):
        rc, out, err = self.libcmd("scan", "--lib", self.lib)
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        items = {}
        for l in res["libraries"]:
            for cat, arr in l["categories"].items():
                for it in arr:
                    items[f"{cat}/{it['name']}"] = it
        return items

    def test_scan_install_modify_sync_uninstall(self):
        # 1) 초기 스캔: 전부 미설치, kit 참조 휴리스틱 동작
        items = self._scan()
        self.assertEqual(items["agents/a1"]["status"], "not_installed")
        self.assertEqual(items["skills/s1"]["status"], "not_installed")
        self.assertTrue(items["commands/c1"]["kit_ref"])   # ${CLAUDE_PROJECT_DIR} 감지
        self.assertFalse(items["agents/a1"]["kit_ref"])

        # 2) 설치 -> installed
        for cat, name in (("agents", "a1"), ("skills", "s1"), ("commands", "c1")):
            rc, out, err = self.libcmd("install", cat, name, "--lib", self.lib)
            self.assertEqual(rc, 0, err)
        items = self._scan()
        self.assertEqual(items["agents/a1"]["status"], "installed")
        self.assertEqual(items["skills/s1"]["status"], "installed")

        # 3) 라이브 쪽 수정 -> modified
        with open(os.path.join(self.target, "agents", "a1.md"), "a", encoding="utf-8") as f:
            f.write("local edit\n")
        with open(os.path.join(self.target, "skills", "s1", "extra.txt"), "w") as f:
            f.write("x")   # 파일 추가도 감지되어야 함
        items = self._scan()
        self.assertEqual(items["agents/a1"]["status"], "modified")
        self.assertEqual(items["skills/s1"]["status"], "modified")

        # 4) 동기화(재설치): 파일은 .bak, 디렉토리는 .trash 백업 후 라이브러리 버전으로
        rc, out, err = self.libcmd("install", "agents", "a1", "--lib", self.lib)
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["synced"])
        self.assertTrue(res["backup"] and os.path.exists(res["backup"]))
        rc, out, err = self.libcmd("install", "skills", "s1", "--lib", self.lib)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(json.loads(out)["backup"]))  # .trash 이동분
        items = self._scan()
        self.assertEqual(items["agents/a1"]["status"], "installed")
        self.assertEqual(items["skills/s1"]["status"], "installed")

        # 5) 제거 -> .trash + not_installed
        rc, out, err = self.libcmd("uninstall", "agents", "a1")
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(json.loads(out)["trashed"]))
        items = self._scan()
        self.assertEqual(items["agents/a1"]["status"], "not_installed")

    def test_library_registration_persisted(self):
        self.libcmd("scan", "--lib", self.lib)
        with open(os.path.join(self.store, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertIn(self.lib, cfg.get("libraries", []))
        # 등록 후에는 --lib 없이 스캔 가능
        rc, out, err = self.libcmd("scan")
        self.assertEqual(rc, 0, err)

    def test_invalid_name_rejected(self):
        rc, out, err = self.libcmd("uninstall", "agents", "..\\evil")
        self.assertNotEqual(rc, 0)

    def test_install_refuses_phantom_target(self):
        # local 설치: target 의 부모(프로젝트 폴더)가 없으면 거부 — 엉뚱한 위치에 .claude 흩뿌리기 방지.
        self._scan()  # 라이브러리 등록
        phantom = os.path.join(self.tmp, "nonexistent-project", ".claude")  # 부모 미존재
        rc, out, err = run(LIB, "--store", self.store, "--target", phantom, "--no-snapshot",
                           "install", "agents", "a1", "--lib", self.lib)
        self.assertNotEqual(rc, 0, "phantom 부모면 거부해야 함")
        self.assertFalse(json.loads(out)["ok"])
        self.assertFalse(os.path.exists(phantom), "거부 시 .claude 를 만들지 않아야 함")

    def test_install_allows_existing_parent_target(self):
        # 부모(프로젝트 폴더)가 존재하면 .claude 하위 생성은 정상 첫 설치로 허용.
        self._scan()
        proj = os.path.join(self.tmp, "real-project")
        os.makedirs(proj)  # 부모 존재, .claude 는 아직 없음
        tgt = os.path.join(proj, ".claude")
        rc, out, err = run(LIB, "--store", self.store, "--target", tgt, "--no-snapshot",
                           "install", "agents", "a1", "--lib", self.lib)
        self.assertEqual(rc, 0, err)
        self.assertTrue(os.path.exists(os.path.join(tgt, "agents", "a1.md")))

    def test_env_declared_library(self):
        # CLAUDE_CONFIG_LIBRARIES: 등록 없이 env 만으로 스캔 대상이 되고(선언적),
        # env 에서 빠지면 목록에서도 빠진다(store 에 영속되지 않음).
        env = {"CLAUDE_CONFIG_LIBRARIES": self.lib}
        rc, out, err = run(LIB, "--store", self.store, "--target", self.target,
                           "--no-snapshot", "scan", env=env)
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertEqual([l["lib"] for l in res["libraries"]], [self.lib])
        # store config 에는 영속되지 않아야 함
        with open(os.path.join(self.store, "config.json"), encoding="utf-8") as f:
            self.assertNotIn(self.lib, json.load(f).get("libraries", []))
        # env 제거 -> 미설정은 오류가 아니라 빈 결과(정상 종료)
        rc, out, err = self.libcmd("scan")
        self.assertEqual(rc, 0, err)
        res = json.loads(out)
        self.assertTrue(res["ok"])
        self.assertEqual(res["libraries"], [])


class TrackProjectPreset(unittest.TestCase):
    """cas.py track: 디렉토리 -> 프로젝트 프리셋(settings.json + settings.local.json) 확장,
    파일/글롭은 그대로. status --json 의 defaults 로 전역/프로젝트 분류."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="track_test_")
        self.store = os.path.join(self.tmp, "store")
        run(CAS, "--store", self.store, "init")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cas(self, *args):
        return run(CAS, "--store", self.store, *args)

    def _nc(self, p):
        return os.path.normcase(os.path.abspath(p))

    def _make(self, path, body="{}"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def test_track_project_dir_expands_preset(self):
        # 프로젝트 루트를 주면 <root>/.claude 의 settings.json + settings.local.json 을 추적.
        proj = os.path.join(self.tmp, "repoA")
        s = self._make(os.path.join(proj, ".claude", "settings.json"))
        sl = self._make(os.path.join(proj, ".claude", "settings.local.json"))
        rc, out, err = self.cas("track", "--json", proj)
        self.assertEqual(rc, 0, err)
        added = {self._nc(x) for x in json.loads(out)["added"]}
        self.assertEqual(added, {self._nc(s), self._nc(sl)})

    def test_track_claude_dir_directly(self):
        # .claude 디렉토리를 직접 주면 그 안의 프리셋을 추적(settings.local 없으면 settings 만).
        cdir = os.path.join(self.tmp, "repoB", ".claude")
        s = self._make(os.path.join(cdir, "settings.json"))
        rc, out, err = self.cas("track", "--json", cdir)
        self.assertEqual(rc, 0, err)
        added = {self._nc(x) for x in json.loads(out)["added"]}
        self.assertEqual(added, {self._nc(s)})

    def test_track_file_path_backward_compat(self):
        # 파일 경로를 직접 주면 그 파일만 추적(기존 동작 유지).
        f = self._make(os.path.join(self.tmp, "repoC", ".claude", "settings.json"))
        rc, out, err = self.cas("track", "--json", f)
        self.assertEqual(rc, 0, err)
        self.assertEqual({self._nc(x) for x in json.loads(out)["added"]}, {self._nc(f)})

    def test_track_dir_without_config_adds_nothing(self):
        # 설정 파일 없는 폴더 -> 추가 0건(오류 아님).
        empty = os.path.join(self.tmp, "repoD", ".claude")
        os.makedirs(empty)
        rc, out, err = self.cas("track", "--json", empty)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["added"], [])

    def test_untrack_removes_project_file(self):
        f = self._make(os.path.join(self.tmp, "repoE", ".claude", "settings.json"))
        self.cas("track", "--json", f)
        rc, out, err = self.cas("untrack", "--json", f)
        self.assertEqual(rc, 0, err)
        self.assertTrue(json.loads(out)["ok"])
        st = json.loads(self.cas("status", "--json")[1])
        allpaths = [p for k in ("new", "modified", "deleted", "unchanged") for p in st.get(k, [])]
        self.assertNotIn(self._nc(f), [self._nc(x) for x in allpaths])

    def test_status_defaults_classifies_global_vs_project(self):
        # fake HOME 의 ~/.claude.json 은 DEFAULT_TRACKED(전역)로 분류, 프로젝트 파일은 아님.
        fake_home = os.path.join(self.tmp, "home")
        gj = self._make(os.path.join(fake_home, ".claude.json"))
        pf = self._make(os.path.join(self.tmp, "repoF", ".claude", "settings.json"))
        e = dict(os.environ)
        e.pop("CLAUDE_CAS_NO_DEFAULT_TRACK", None)  # 기본 병합 켬
        e["HOME"] = fake_home
        e["USERPROFILE"] = fake_home
        def cas_env(*args):
            return subprocess.run([sys.executable, CAS, "--store", self.store, *args],
                                  capture_output=True, text=True, encoding="utf-8", env=e, timeout=60)
        cas_env("track", "--json", pf)          # 프로젝트 파일 수동 추적
        p = cas_env("status", "--json")         # 이때 fake ~/.claude.json 자동 병합
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads(p.stdout)
        defaults = st.get("defaults", [])
        self.assertTrue(any(self._nc(d) == self._nc(gj) for d in defaults))   # 전역 = default
        self.assertFalse(any(self._nc(d) == self._nc(pf) for d in defaults))  # 프로젝트 = default 아님
        # UI 불변식: renderTracked 는 defaults(원문 문자열) 집합에 버킷 경로를 .has() 로 매칭.
        # 따라서 모든 default 문자열이 status 버킷에 byte-identical 로 존재해야 배지가 안 깨진다.
        bset = {x for k in ("new", "modified", "deleted", "unchanged") for x in st.get(k, [])}
        for d in defaults:
            self.assertIn(d, bset, f"default {d!r} 가 status 버킷과 정규화 불일치 -> UI 전역/프로젝트 배지 깨짐")


class DesktopPathResolve(unittest.TestCase):
    r"""paths.py: Claude Desktop 데이터 디렉토리 해석 (설치 방식별 겸용).
      Win32 설치본 :  %APPDATA%\Claude
      MSIX/Store  :  %LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude
    두 후보를 모두 프로브해 '실제 존재하며 config 가 최신인' 쪽을 고른다."""

    def setUp(self):
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        import paths                       # 없으면 여기서 실패(=RED, 기능 미구현)
        self.paths = paths
        self.tmp = tempfile.mkdtemp(prefix="paths_test_")
        self.appdata = os.path.join(self.tmp, "Roaming")
        self.localappdata = os.path.join(self.tmp, "Local")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _win32_dir(self):
        return os.path.join(self.appdata, "Claude")

    def _msix_dir(self):
        return os.path.join(self.localappdata, "Packages",
                            "Claude_pzs8sxrjxfjjc", "LocalCache", "Roaming", "Claude")

    def _make_config(self, d, mtime=None):
        os.makedirs(d, exist_ok=True)
        cfg = os.path.join(d, "claude_desktop_config.json")
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"mcpServers": {}}, f)
        if mtime is not None:
            os.utime(cfg, (mtime, mtime))
        return cfg

    def _resolve(self):
        return self.paths.resolve_desktop_dir(appdata=self.appdata, localappdata=self.localappdata)

    def test_win32_only(self):
        self._make_config(self._win32_dir())
        self.assertEqual(self._resolve(), self._win32_dir())

    def test_msix_only(self):
        # Win32 후보 디렉토리는 아예 없고 MSIX 패키지 폴더에만 config 존재.
        self._make_config(self._msix_dir())
        self.assertEqual(self._resolve(), self._msix_dir())

    def test_both_prefers_most_recent_config(self):
        self._make_config(self._win32_dir(), mtime=1000)
        self._make_config(self._msix_dir(), mtime=2000)      # MSIX 가 더 최신 -> MSIX
        self.assertEqual(self._resolve(), self._msix_dir())
        os.utime(os.path.join(self._win32_dir(), "claude_desktop_config.json"), (3000, 3000))
        self.assertEqual(self._resolve(), self._win32_dir())  # Win32 가 최신 -> Win32

    def test_none_falls_back_to_win32(self):
        # 아무 후보도 없으면 Win32 기본 경로로 결정적 폴백(존재하지 않아도).
        self.assertEqual(self._resolve(), self._win32_dir())

    def test_desktop_config_path_appends_filename(self):
        self._make_config(self._msix_dir())
        self.assertEqual(
            self.paths.desktop_config_path(appdata=self.appdata, localappdata=self.localappdata),
            os.path.join(self._msix_dir(), "claude_desktop_config.json"))


class ClaudeConfigDump(unittest.TestCase):
    def test_dump_no_unicode_crash(self):
        # 핵심 회귀 가드: PYTHONUTF8/IOENCODING 없이도(=스크립트 내 reconfigure) 죽지 않아야.
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        p = subprocess.run([sys.executable, CFG, "dump"], capture_output=True, env=env,
                           timeout=60)
        self.assertEqual(p.returncode, 0,
                         f"dump 가 종료코드 {p.returncode} (cp949 회귀?): {p.stderr.decode('utf-8','replace')[-400:]}")
        data = json.loads(p.stdout.decode("utf-8"))
        self.assertIn("sections", data)

    def test_projects_has_claude_flag(self):
        # .claude.json projects -> {path,name,claude_dir,has_claude}. .claude 있는 것만 True.
        tmp = tempfile.mkdtemp(prefix="proj_test_")
        try:
            pa = os.path.join(tmp, "projA"); os.makedirs(os.path.join(pa, ".claude"))
            pb = os.path.join(tmp, "projB"); os.makedirs(pb)  # .claude 없음
            cj = os.path.join(tmp, ".claude.json")
            with open(cj, "w", encoding="utf-8") as f:
                json.dump({"projects": {pa: {}, pb: {}}}, f)
            p = subprocess.run([sys.executable, CFG, "projects", "--paths", f"claude_json={cj}"],
                               capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
            by = {x["path"]: x for x in json.loads(p.stdout)["projects"]}
            self.assertTrue(by[pa]["has_claude"])
            self.assertFalse(by[pb]["has_claude"])
            self.assertEqual(by[pa]["claude_dir"], os.path.join(pa, ".claude"))
        finally:
            import shutil; shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
