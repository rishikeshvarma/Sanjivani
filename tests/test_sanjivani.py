"""Tests for the Sanjivani toolkit: python -m unittest discover -s tests

Each test gets its own graveyard in a temp folder. Network is never used: the
GitHub size check is replaced with a stub.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sanjivani as sj  # noqa: E402


def git(repo, *args, **env):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
             GIT_COMMITTER_EMAIL="t@x", **env)
    subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args], check=True,
                   capture_output=True, env=e)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name, "graveyard").resolve()
        self.root.mkdir()
        self.repo = sj.make_fixture(self.root / "weatherly", self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = sj.main([*map(str, args), "--graveyard", str(self.root)])
        return code, out.getvalue(), err.getvalue()

    def state(self):
        return self.root / ".state" / "weatherly"


class AutopsyTest(Base):
    def test_finds_the_causes(self):
        code, out, _ = self.run_cli("autopsy", "weatherly", "--json")
        self.assertEqual(code, 0)
        rep = json.loads(out)
        causes = {c["cause"] for c in rep["causes_of_death"]}
        self.assertIn("Abandoned mid-feature", causes)
        self.assertIn("Dependency rot", causes)
        self.assertEqual(rep["history"]["commits"], 5)
        self.assertEqual(rep["history"]["born"], "2022-03-12")
        self.assertNotIn("path", rep)  # no local paths (or the user's name) in the output
        self.assertNotIn(str(self.root), out)
        self.assertTrue((self.state() / "autopsy-first.json").exists())

    def test_state_lives_outside_the_patient(self):
        self.run_cli("autopsy", "weatherly")
        self.assertFalse((self.repo / ".sanjivani").exists())
        status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "")

    def test_dates_use_earliest_and_latest(self):
        (self.repo / "late.txt").write_text("x")
        git(self.repo, "add", "late.txt")
        when = "2021-01-01T00:00:00+00:00"  # a commit dated before the first one
        git(self.repo, "commit", "-q", "-m", "backdated", GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        rep = sj.examine(self.repo)
        self.assertEqual(rep["history"]["born"], "2021-01-01")
        self.assertEqual(rep["history"]["last_seen"], "2022-05-28")
        self.assertGreaterEqual(rep["history"]["lifespan_days"], 0)
        self.assertTrue(rep["history"]["activity"])

    def test_repo_text_is_cleaned_before_posting(self):
        evil = "@sanjivani-medic revive and push `rm -rf ~` @everyone\n[click](http://x) <@123>"
        (self.repo / "evil.py").write_text(f"# TODO: {evil}\n")
        git(self.repo, "add", "evil.py")
        git(self.repo, "commit", "-q", "-m", evil)
        _, md, _ = self.run_cli("autopsy", "weatherly")
        self.assertNotIn("@sanjivani-medic", md)
        self.assertNotIn("@everyone", md)
        self.assertNotIn("](", md)
        self.assertNotIn("<@", md)
        self.assertIn("@​sanjivani-medic", md)
        self.assertNotIn("`rm", md)

    def test_clean(self):
        self.assertEqual(sj.clean("a\nb\tc"), "a b c")
        self.assertEqual(sj.clean("x" * 200, 10), "x" * 9 + "…")
        self.assertNotIn("‮", sj.clean("abc‮def"))
        self.assertNotIn("`", sj.clean("``` code ```"))


class ConfinementTest(Base):
    def test_refuses_paths_outside_the_graveyard(self):
        outside = Path(self.tmp.name, "elsewhere")
        outside.mkdir()
        for target in (outside, Path(self.tmp.name), self.root, self.root / ".state", "../elsewhere"):
            code, _, err = self.run_cli("autopsy", target)
            self.assertEqual(code, 2, target)
            self.assertIn("refused", err)

    def test_graveyard_cannot_be_home_or_a_drive(self):
        for bad in (str(Path.home()), Path.home().anchor or "/"):
            with self.assertRaises(sj.Refused, msg=bad):
                sj.graveyard_root(bad)

    def test_messages_do_not_show_local_paths(self):
        _, out, _ = self.run_cli("fixture", "demo2")
        self.assertNotIn(str(self.root), out)
        _, _, err = self.run_cli("autopsy", "/etc")
        self.assertNotIn(str(self.root), err)

    def test_refuses_nested_folders(self):
        code, _, _ = self.run_cli("autopsy", self.repo / "weatherly")
        self.assertEqual(code, 2)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_refuses_a_symlinked_patient(self):
        outside = Path(self.tmp.name, "secret")
        outside.mkdir()
        (self.root / "link").symlink_to(outside)
        code, _, _ = self.run_cli("autopsy", "link")
        self.assertEqual(code, 2)


class CloneTest(Base):
    def test_only_github_links(self):
        for url in ("file:///etc", "/etc", "git@github.com:a/b.git", "https://gitlab.com/a/b",
                    "https://github.com/a", "https://github.com/a/b/../../c", "https://github.com/-x/b",
                    "https://github.com/a/.git", "https://github.com.evil.io/a/b", "ext::sh -c id"):
            with self.assertRaises(sj.Refused, msg=url):
                sj.parse_github(url)
        self.assertEqual(sj.parse_github("https://github.com/octocat/Hello-World.git"), ("octocat", "Hello-World"))
        self.assertEqual(sj.slug("Octo", "My.Repo"), "octo-my.repo")

    def test_private_and_size_rules(self):
        with self.assertRaises(sj.Refused):
            sj.clone("https://github.com/a/b", self.root, "bob", False, True, 50, 3, False)  # private, not owner
        with self.assertRaises(sj.Refused):
            sj.clone("https://github.com/a/b", self.root, "bob", False, False, 50, 3, True)  # skip check, not owner
        with mock.patch.object(sj, "github_info", return_value={"size": 200_000, "private": False}):
            with self.assertRaisesRegex(sj.Refused, "limit is 50 MB"):
                sj.clone("https://github.com/a/b", self.root, "bob", False, False, 50, 3, False)
        with mock.patch.object(sj, "github_info", return_value={"size": 1, "private": True}):
            with self.assertRaisesRegex(sj.Refused, "private"):
                sj.clone("https://github.com/a/b", self.root, "bob", False, False, 50, 3, False)

    def test_per_person_limit(self):
        f = sj.state_dir(self.root, "_requests") / "requests.json"
        sj.save_json(f, [{"by": "bob", "at": time.time(), "patient": f"p{i}"} for i in range(3)])
        with self.assertRaisesRegex(sj.Refused, "limit 3"):
            sj.clone("https://github.com/a/b", self.root, "Bob", False, False, 50, 3, False)

    def test_clone_command_is_safe(self):
        seen = {}

        def fake_run(argv, cwd, env, timeout, on_timeout=None):
            seen["argv"] = argv
            Path(argv[-1]).mkdir()
            return 0, False, ""

        with mock.patch.object(sj, "github_info", return_value={"size": 10, "private": False}), \
                mock.patch.object(sj, "run_capped", side_effect=fake_run):
            dest = sj.clone("https://github.com/Octo/Repo", self.root, "bob", False, False, 50, 3, False)
        argv = seen["argv"]
        self.assertEqual(dest.name, "octo-repo")
        self.assertIn("--", argv)
        self.assertLess(argv.index("--"), argv.index("https://github.com/Octo/Repo.git"))
        self.assertIn("credential.helper=", argv)
        self.assertIn("core.symlinks=false", argv)
        self.assertIn("--no-recurse-submodules", argv)
        origin = json.loads((self.root / ".state" / "octo-repo" / "origin.json").read_text())
        self.assertEqual(origin["requested_by"], "bob")


class VitalsTest(Base):
    def vitals(self, label):
        code, out, err = self.run_cli("vitals", "weatherly", "--label", label, "--sandbox", "host", "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    @unittest.skipIf(sys.version_info < (3, 10), "weatherly only breaks on Python 3.10+")
    def test_revival_flow(self):
        self.run_cli("autopsy", "weatherly")
        before = self.vitals("time of death")
        self.assertEqual(before["sandbox"], "host")
        self.assertLess(before["passed"], before["total"])
        cfg = self.repo / "weatherly" / "config.py"
        cfg.write_text(cfg.read_text().replace("from collections import", "from collections.abc import"))
        cache = self.repo / "weatherly" / "cache.py"
        cache.write_text(cache.read_text().replace("import imp", "# imp removed"))
        git(self.repo, "commit", "-qam", "sanjivani: fix removed APIs")
        after = self.vitals("after surgery")
        self.assertEqual(after["passed"], after["total"])
        code, _, err = self.run_cli("certificate", "weatherly")
        self.assertEqual(code, 0, err)
        cert = (self.repo / "REVIVAL.md").read_text(encoding="utf-8")
        self.assertIn("Certificate of Revival", cert)
        self.assertIn("strong", cert)
        self.assertIn("Surgery since the first reading", cert)
        # a second certificate may replace the toolkit's own file
        self.assertEqual(self.run_cli("certificate", "weatherly")[0], 0)

    def test_checks_get_a_clean_environment(self):
        (self.repo / "tests" / "test_env.py").write_text(
            "import os, unittest\n"
            "class T(unittest.TestCase):\n"
            "    def test_no_secret(self):\n"
            "        self.assertNotIn('SANJIVANI_TEST_SECRET', os.environ)\n"
            "        self.assertNotIn('GH_TOKEN', os.environ)\n")
        with mock.patch.dict(os.environ, {"SANJIVANI_TEST_SECRET": "x", "GH_TOKEN": "y"}):
            env = sj.clean_env(self.tmp.name)
        self.assertNotIn("SANJIVANI_TEST_SECRET", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertEqual(env["HOME"], self.tmp.name)

    def test_no_tests_is_not_a_failure(self):
        empty = self.root / "empty"
        empty.mkdir()
        (empty / "main.py").write_text("print('hi')\n")
        code, out, _ = self.run_cli("vitals", "empty", "--sandbox", "host", "--json")
        beat = json.loads(out)
        statuses = {r["check"]: r["status"] for r in beat["results"]}
        self.assertEqual(statuses["python: tests"], "none")
        self.assertEqual((beat["passed"], beat["total"]), (1, 1))

    def test_syntax_check_survives_bad_files(self):
        (self.repo / "nul.py").write_bytes(b"x = 1\x00\n")
        (self.repo / "deep.py").write_text("x = " + "(" * 5000 + ")" * 5000 + "\n")
        (self.repo / "broken.py").write_text("def f(:\n")
        ok, tail = sj.syntax_check(self.repo)
        self.assertFalse(ok)
        self.assertIn("broken.py", tail)

    def test_custom_cmd_uses_no_shell(self):
        marker = Path(self.tmp.name, "pwned")
        self.run_cli("vitals", "weatherly", "--sandbox", "host",
                     "--cmd", f"{sys.executable} -c pass ; touch {marker}")
        self.assertFalse(marker.exists())

    @unittest.skipIf(os.name == "nt", "process groups are POSIX here")
    def test_timeout_kills_the_whole_tree(self):
        script = ("import subprocess, sys, time; "
                  "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)")
        start = time.time()
        rc, timed_out, _ = sj.run_capped([sys.executable, "-c", script], Path(self.tmp.name),
                                         dict(os.environ), timeout=2)
        self.assertTrue(timed_out)
        self.assertLess(time.time() - start, 20)

    def test_output_is_capped(self):
        rc, _, tail = sj.run_capped([sys.executable, "-c", "print('x' * 2_000_000)"], Path(self.tmp.name),
                                    dict(os.environ), timeout=30)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(tail), sj.TAIL_BYTES)

    def test_docker_command(self):
        argv = sj.docker_argv(self.repo, "python:3.12-slim", ["python", "-m", "unittest"], "sanjivani-x")
        for flag in ("--network", "none", "--cap-drop", "ALL", "--rm", "--pids-limit"):
            self.assertIn(flag, argv)
        self.assertIn(f"type=bind,source={self.repo},target=/patient", argv)

    def test_docker_flow_with_install(self):
        calls = []

        def fake_run(argv, cwd, env, timeout, on_timeout=None):
            calls.append(argv)
            return 0, False, "ok"

        with mock.patch.object(sj, "docker_available", return_value=True), \
                mock.patch.object(sj, "run_capped", side_effect=fake_run):
            beat = sj.run_vitals(self.repo, self.root, "t", [], 60, "auto", "auto", None, install=True)
        self.assertEqual(beat["sandbox"], "docker")
        install, tests = calls[0], calls[1]
        self.assertEqual(install[install.index("--network") + 1], "bridge")  # only the install gets network
        self.assertIn("--target", install)
        self.assertEqual(tests[tests.index("--network") + 1], "none")
        self.assertIn("PYTHONPATH=/deps", tests)
        self.assertEqual(beat["total"], 2)  # the install step is not counted as a vital sign

    def test_docker_keeps_git_read_only_and_does_not_run_as_root(self):
        argv = sj.docker_argv(self.repo, "python:3.12-slim", ["python"], "sanjivani-x")
        self.assertIn(f"type=bind,source={self.repo / '.git'},target=/patient/.git,readonly", argv)
        if hasattr(os, "getuid"):
            self.assertIn(f"{os.getuid()}:{os.getgid()}", argv)

    def test_docker_is_the_default_and_never_falls_back(self):
        with mock.patch.object(sj, "docker_available", return_value=False):
            code, _, err = self.run_cli("vitals", "weatherly")
        self.assertEqual(code, 2)
        self.assertIn("Docker is not running", err)

    def test_installs_run_no_package_code(self):
        steps = sj.install_steps(self.repo)
        self.assertIn("--only-binary=:all:", steps[0]["argv"])
        proj = self.root / "proj"
        proj.mkdir()
        (proj / "setup.py").write_text("from setuptools import setup\nsetup()\n")
        self.assertIn("skip", sj.install_steps(proj)[0])
        self.assertEqual(sj.install_steps(proj, allow_source=True)[0]["argv"][-1], ".")
        with self.assertRaises(sj.Refused):
            sj.run_vitals(self.repo, self.root, "t", [], 60, "host", "auto", None, allow_source=True)

    def test_checks_are_planned_after_the_install(self):
        (self.repo / "package.json").write_text('{"scripts": {"test": "node t.js"}}')

        def fake_run(argv, cwd, env, timeout, on_timeout=None):
            if "npm" in argv:
                (self.repo / "node_modules").mkdir(exist_ok=True)
            return 0, False, "ok"

        with mock.patch.object(sj, "docker_available", return_value=True), \
                mock.patch.object(sj, "run_capped", side_effect=fake_run):
            beat = sj.run_vitals(self.repo, self.root, "t", [], 60, "docker", "auto", None, install=True)
        names = [r["check"] for r in beat["results"]]
        self.assertIn("node: install packages", names)
        self.assertNotIn("node: dependencies installed", names)

    def test_deep_folders_do_not_crash(self):
        deep = self.repo.joinpath(*["d"] * (sj.MAX_DEPTH + 5))
        deep.mkdir(parents=True)
        (deep / "x.py").write_text("x = 1\n")
        files, _ = sj.list_files(self.repo)
        self.assertNotIn(deep / "x.py", files)

    def test_install_needs_docker(self):
        with self.assertRaises(sj.Refused):
            sj.run_vitals(self.repo, self.root, "t", [], 60, "host", "auto", None, install=True)

    def test_damaged_notes_do_not_crash(self):
        sd = sj.state_dir(self.root, "weatherly")
        (sd / "vitals.json").write_text("{not json")
        (sd / "autopsy-first.json").write_text("[1, 2]")
        self.assertEqual(self.vitals("first")["label"], "first")
        code, _, err = self.run_cli("certificate", "weatherly")
        self.assertEqual(code, 2)
        self.assertIn("two vitals readings", err)

    def test_shipped_state_is_ignored_and_commit_must_be_a_hash(self):
        (self.repo / ".sanjivani").mkdir()
        (self.repo / ".sanjivani" / "vitals.json").write_text(json.dumps(
            [{"label": "fake", "passed": 9, "total": 9, "commit": "--output=/tmp/x"}]))
        self.assertEqual(len(sj.load_json(self.state() / "vitals.json", [])), 0)
        sd = sj.state_dir(self.root, "weatherly")
        sj.save_json(sd / "vitals.json", [{"label": "a", "passed": 0, "total": 1, "commit": "--output=/tmp/x"},
                                          {"label": "b", "passed": 1, "total": 1, "commit": "abc"}])
        self.assertEqual(self.run_cli("certificate", "weatherly")[0], 0)
        self.assertFalse(Path("/tmp/x").exists() and os.path.getmtime("/tmp/x") > time.time() - 60)


class WriteSafetyTest(Base):
    def test_will_not_overwrite_files_it_did_not_write(self):
        (self.repo / "TOMBSTONE.md").write_text("mine")
        code, _, err = self.run_cli("tombstone", "weatherly", "--epitaph", "bye")
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.assertEqual((self.repo / "TOMBSTONE.md").read_text(), "mine")
        self.assertEqual(self.run_cli("tombstone", "weatherly", "--epitaph", "bye", "--force")[0], 0)

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_will_not_write_through_symlinks(self):
        victim = Path(self.tmp.name, "victim.txt")
        victim.write_text("safe")
        (self.repo / "TOMBSTONE.md").symlink_to(victim)
        code, _, _ = self.run_cli("tombstone", "weatherly", "--epitaph", "bye", "--force")
        self.assertEqual(code, 2)
        self.assertEqual(victim.read_text(), "safe")

    @unittest.skipIf(os.name == "nt", "symlinks need extra rights on Windows")
    def test_symlinks_are_not_read_or_donated(self):
        secret = Path(self.tmp.name, "secret.py")
        secret.write_text("# TODO: steal me\n")
        (self.repo / "leak.py").symlink_to(secret)
        files, _ = sj.list_files(self.repo)
        self.assertNotIn(self.repo / "leak.py", files)
        code, _, err = self.run_cli("tombstone", "weatherly", "--epitaph", "bye", "--donate", "leak.py")
        self.assertEqual(code, 2)
        self.assertIn("symlink", err)

    def test_tombstone_from_json_and_organ_bank(self):
        funeral = self.root / ".state" / "weatherly" / "funeral.json"
        sj.state_dir(self.root, "weatherly")
        funeral.write_text(json.dumps({"epitaph": 'It had "clouds" `$(whoami)`', "lessons": ["ship small"],
                                       "donate": ["weatherly/config.py"]}))
        code, _, err = self.run_cli("tombstone", "weatherly", "--input", funeral)
        self.assertEqual(code, 0, err)
        stone = (self.repo / "TOMBSTONE.md").read_text(encoding="utf-8")
        self.assertIn("R . I . P", stone)
        self.assertIn("ship small", stone)
        self.assertNotIn("`$(", stone)
        bank = self.root / "_organ-bank" / "weatherly"
        self.assertTrue((bank / "weatherly_config.py").exists())
        self.assertIn("own project", (bank / "ORIGIN.md").read_text(encoding="utf-8"))

    def test_tombstone_rerun_and_default_funeral_file(self):
        code, out, _ = self.run_cli("where", "weatherly")
        notes = Path(out.strip())
        self.assertEqual(notes, self.state())
        (notes / "funeral.json").write_text(json.dumps(
            {"epitaph": "bye", "lessons": "one lesson as a string",
             "donate": ["weatherly/format.py", "weatherly\\format.py", "weatherly/format.py"]}))
        self.assertEqual(self.run_cli("tombstone", "weatherly")[0], 0)
        stone = (self.repo / "TOMBSTONE.md").read_text(encoding="utf-8")
        self.assertIn("one lesson as a string", stone)
        self.assertEqual(stone.count("weatherly/format.py"), 1)
        # running it again replaces the toolkit's own tombstone and organs, no --force needed
        code, _, err = self.run_cli("tombstone", "weatherly")
        self.assertEqual(code, 0, err)

    def test_donations_must_stay_inside_the_patient(self):
        code, _, _ = self.run_cli("tombstone", "weatherly", "--epitaph", "bye", "--donate", "../../etc/passwd")
        self.assertEqual(code, 2)

    def test_organ_names_cannot_collide(self):
        (self.repo / "a_b").mkdir()
        (self.repo / "a_b" / "c.py").write_text("x = 1\n")
        (self.repo / "a").mkdir()
        (self.repo / "a" / "b_c.py").write_text("y = 2\n")
        code, _, err = self.run_cli("tombstone", "weatherly", "--epitaph", "bye",
                                    "--donate", "a_b/c.py", "--donate", "a/b_c.py")
        self.assertEqual(code, 2)
        self.assertIn("same organ-bank name", err)
        self.assertFalse((self.root / "_organ-bank" / "weatherly" / "a_b_c.py").exists())
        self.assertFalse((self.repo / "TOMBSTONE.md").exists())

    def test_binary_organs_are_refused(self):
        (self.repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")
        code, _, err = self.run_cli("tombstone", "weatherly", "--epitaph", "bye", "--donate", "logo.png")
        self.assertEqual(code, 2)
        self.assertIn("not a text file", err)

    def test_funeral_file_inside_a_patient_is_refused(self):
        (self.repo / "funeral.json").write_text('{"epitaph": "x"}')
        code, _, err = self.run_cli("tombstone", "weatherly", "--input", self.repo / "funeral.json")
        self.assertEqual(code, 2)
        self.assertIn("notes folder", err)

    def test_funeral_file_must_be_in_the_graveyard(self):
        other = Path(self.tmp.name, "f.json")
        other.write_text('{"epitaph": "x"}')
        code, _, _ = self.run_cli("tombstone", "weatherly", "--input", other)
        self.assertEqual(code, 2)


class ProgramLookupTest(unittest.TestCase):
    def test_programs_come_from_path_never_the_current_folder(self):
        # A fake tool sits in the current folder and in a folder on PATH; only
        # the PATH one may ever be picked. On Windows the fake is a .exe, the
        # case that matters most there.
        name = "sanjivani-fake-tool"
        fname = name + (".exe" if os.name == "nt" else "")
        with tempfile.TemporaryDirectory() as here, tempfile.TemporaryDirectory() as bindir:
            for d in (here, bindir):
                tool = Path(d, fname)
                tool.write_text("#!/bin/sh\n")
                tool.chmod(0o755)
            old = os.getcwd()
            os.chdir(here)
            try:
                for path in ("", os.pathsep, ".", os.pathsep.join(["", "."])):
                    with mock.patch.dict(os.environ, {"PATH": path}):
                        with self.assertRaises(FileNotFoundError, msg=repr(path)):
                            sj.exe(name)
                with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(["", ".", bindir])}):
                    self.assertEqual(os.path.normcase(sj.exe(name)),
                                     os.path.normcase(os.path.join(bindir, fname)))
            finally:
                os.chdir(old)


class FixtureTest(Base):
    def test_fixture_refuses_a_file(self):
        (self.root / "afile").write_text("x")
        code, _, err = self.run_cli("fixture", "afile")
        self.assertEqual(code, 2)
        self.assertIn("not a folder", err)

    def test_fixture_is_on_main(self):
        branch = subprocess.run(["git", "-C", str(self.repo), "branch", "--show-current"],
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, "main")


if __name__ == "__main__":
    unittest.main()
