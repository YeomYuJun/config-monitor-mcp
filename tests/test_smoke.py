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


def run(script, *args, env=None):
    """스크립트를 서브프로세스로 실행. (returncode, stdout, stderr) 반환."""
    e = dict(os.environ)
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
