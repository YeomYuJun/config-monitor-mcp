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


if __name__ == "__main__":
    unittest.main(verbosity=2)
