"""Engine tests. Run with:  python3 -m unittest discover -s engine/tests -v"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import FakeHome  # noqa: E402


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self.h = FakeHome()

    def tearDown(self):
        self.h.cleanup()


class ScanTests(EngineTestCase):
    def test_empty_home(self):
        v = self.h.scan()
        self.assertEqual(v["totals"], {"claude": 0, "codex": 0, "pairs": 0, "echoes": 0})

    def test_counts_human_turns_not_tool_results(self):
        self.h.claude_session([("hi", "hello", [("Bash", {"command": "ls"}, "a b")]), ("again", "ok")])
        self.h.codex_thread([("q1", "a1", [("exec_command", {"cmd": "pwd"}, "/x")]), ("q2", "a2"), ("q3", "a3")])
        v = self.h.scan()
        self.assertEqual(v["unpaired"]["claude"][0]["turns"], 2)
        self.assertEqual(v["unpaired"]["codex"][0]["turns"], 3)

    def test_subagent_threads_are_hidden(self):
        self.h.codex_thread([("q", "a")], thread_source="subagent")
        self.assertEqual(self.h.scan()["totals"]["codex"], 0)


class BootstrapTests(EngineTestCase):
    def test_same_id_pairs(self):
        sid = self.h.claude_session([("hi", "hello")])
        self.h.codex_thread([("hi", "hello")], sid=sid)
        out = self.h.run("bootstrap")
        self.assertEqual(out["added_pairs"], 1)
        self.assertEqual(self.h.pair(out["view"], sid)["status"], "in_sync")

    def test_codex_import_record_pairs(self):
        cid = self.h.claude_session([("hi", "hello")])
        did = self.h.codex_thread([("hi", "hello")])
        self.h.codex_import_record(cid, did)
        out = self.h.run("bootstrap")
        p = self.h.pair(out["view"], cid)
        self.assertEqual(p["codex"]["id"], did)
        self.assertEqual(p["origin"], "bootstrap_codex_import")


class SyncTests(EngineTestCase):
    def paired(self):
        sid = self.h.claude_session([("hi", "hello")])
        self.h.codex_thread([("hi", "hello")], sid=sid)
        self.h.run("bootstrap")
        return sid

    def test_codex_newer_pushes_to_claude(self):
        sid = self.paired()
        self.h.add_codex_turns(sid, [("from codex", "codex answer", [("exec_command", {"cmd": "ls"}, "f1")])])
        self.assertEqual(self.h.pair(self.h.scan(), sid)["status"], "codex_newer")
        r = self.h.run("sync", "--pair", sid)
        self.assertEqual(r["pushed_to_claude"], 1)
        v = self.h.scan()
        self.assertEqual(self.h.pair(v, sid)["status"], "in_sync")
        self.assertEqual(self.h.pair(v, sid)["claude"]["turns"], 2)
        # tool call arrived as a real tool_use/tool_result pair
        recs = [json.loads(l) for l in open(self.h.claude_path(sid))]
        uses = [b for r in recs for b in ((r.get("message") or {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
        results = [b for r in recs for b in ((r.get("message") or {}).get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0]["id"], results[0]["tool_use_id"])

    def test_claude_newer_pushes_to_codex(self):
        sid = self.paired()
        self.h.add_claude_turns(sid, [("from claude", "claude answer", [("Bash", {"command": "ls"}, "f1")])])
        self.assertEqual(self.h.pair(self.h.scan(), sid)["status"], "claude_newer")
        r = self.h.run("sync", "--pair", sid)
        self.assertEqual(r["pushed_to_codex"], 1)
        v = self.h.scan()
        self.assertEqual(self.h.pair(v, sid)["status"], "in_sync")
        self.assertEqual(self.h.pair(v, sid)["codex"]["turns"], 2)

    def test_parent_chain_is_intact_after_append(self):
        sid = self.paired()
        self.h.add_codex_turns(sid, [("q2", "a2"), ("q3", "a3")])
        self.h.run("sync", "--pair", sid)
        recs = [json.loads(l) for l in open(self.h.claude_path(sid)) if '"uuid"' in l]
        uuids = {r["uuid"] for r in recs}
        for r in recs[1:]:
            self.assertIn(r["parentUuid"], uuids)

    def test_conflict_requires_choice(self):
        sid = self.paired()
        self.h.add_claude_turns(sid, [("c", "c")])
        self.h.add_codex_turns(sid, [("x", "x")])
        self.assertEqual(self.h.pair(self.h.scan(), sid)["status"], "conflict")
        out = self.h.run("sync", "--pair", sid, ok=False)
        self.assertIn("conflict", out["error"])

    def test_conflict_merge_both(self):
        sid = self.paired()
        self.h.add_claude_turns(sid, [("c", "c")])
        self.h.add_codex_turns(sid, [("x", "x")])
        r = self.h.run("sync", "--pair", sid, "--prefer", "both")
        self.assertEqual((r["pushed_to_codex"], r["pushed_to_claude"]), (1, 1))
        p = self.h.pair(self.h.scan(), sid)
        self.assertEqual(p["status"], "in_sync")
        self.assertEqual(p["claude"]["turns"], 3)
        self.assertEqual(p["codex"]["turns"], 3)

    def test_sync_is_idempotent(self):
        sid = self.paired()
        self.h.add_codex_turns(sid, [("q2", "a2")])
        self.h.run("sync", "--pair", sid)
        self.h.age_all()
        r = self.h.run("sync", "--pair", sid)
        self.assertEqual((r["pushed_to_codex"], r["pushed_to_claude"]), (0, 0))

    def test_recently_written_file_is_not_touched(self):
        sid = self.paired()
        self.h.add_codex_turns(sid, [("q2", "a2")])
        os.utime(self.h.claude_path(sid), None)  # "just written"
        out = self.h.run("sync", "--pair", sid, ok=False)
        self.assertIn("just written", out["error"])


class CreateTests(EngineTestCase):
    def test_create_codex_from_claude(self):
        sid = self.h.claude_session([("hi", "hello"), ("more", "sure")])
        out = self.h.run("create", "--from", "claude", "--id", sid)
        self.assertEqual(out["side"], "codex")
        p = self.h.pair(self.h.scan(), sid)
        self.assertEqual(p["status"], "in_sync")
        self.assertEqual(p["codex"]["turns"], 2)

    def test_create_claude_from_codex_registers_sidebar(self):
        did = self.h.codex_thread([("hi", "hello")], name="Nice title")
        self.h.run("create", "--from", "codex", "--id", did)
        p = self.h.pair(self.h.scan(), did)
        self.assertEqual(p["status"], "in_sync")
        regs = [json.load(open(os.path.join(self.h.desktop_dir, f))) for f in os.listdir(self.h.desktop_dir)]
        self.assertEqual([r["cliSessionId"] for r in regs], [did])


class EchoTests(EngineTestCase):
    """Codex Desktop auto-imports Claude sessions, including the Claude copies this engine made of Codex threads."""

    def make_echo(self):
        did = self.h.codex_thread([("hi", "hello")], name="Original")
        self.h.run("create", "--from", "codex", "--id", did)       # Claude copy with the same id
        echo = self.h.codex_thread([("hi", "hello")], title="[Codex] Original")
        self.h.codex_import_record(did, echo)                      # Codex re-imports that Claude copy
        return did, echo

    def test_echo_is_not_unpaired(self):
        did, echo = self.make_echo()
        v = self.h.scan()
        self.assertEqual(v["unpaired"]["codex"], [])
        self.assertEqual([e["id"] for e in v["echoes"]], [echo])
        self.assertEqual(v["echoes"][0]["echo_of"], did)

    def test_bootstrap_does_not_pair_echo(self):
        did, echo = self.make_echo()
        self.h.run("bootstrap")
        v = self.h.scan()
        self.assertEqual(v["totals"]["pairs"], 1)
        self.assertEqual(self.h.pair(v, did)["codex"]["id"], did)

    def test_cannot_create_copy_of_echo(self):
        did, echo = self.make_echo()
        out = self.h.run("create", "--from", "codex", "--id", echo, ok=False)
        self.assertIn("re-import", out["error"])

    def test_cannot_create_second_copy(self):
        sid = self.h.claude_session([("hi", "hello")])
        self.h.run("create", "--from", "claude", "--id", sid)
        out = self.h.run("create", "--from", "claude", "--id", sid, ok=False)
        self.assertIn("counterpart", out["error"])

    def test_cannot_copy_claude_session_codex_already_imported(self):
        cid = self.h.claude_session([("hi", "hello")])
        did = self.h.codex_thread([("hi", "hello")])
        self.h.codex_import_record(cid, did)
        out = self.h.run("create", "--from", "claude", "--id", cid, ok=False)
        self.assertIn("counterpart", out["error"])
        out = self.h.run("create", "--from", "codex", "--id", did, ok=False)
        self.assertIn("counterpart", out["error"])


class MissingTests(EngineTestCase):
    def test_codex_deleted_reports_side_and_recreates(self):
        cid = self.h.claude_session([("hi", "hello"), ("more", "ok")])
        did = self.h.codex_thread([("hi", "hello"), ("more", "ok")])
        self.h.codex_import_record(cid, did)
        self.h.run("bootstrap")
        self.h.delete_codex_thread(did)
        p = self.h.pair(self.h.scan(), cid)
        self.assertEqual((p["status"], p["missing_side"]), ("missing", "codex"))
        out = self.h.run("recreate", "--pair", cid)
        self.assertEqual(out["recreated"], "codex")
        p = self.h.pair(self.h.scan(), cid)
        self.assertEqual(p["status"], "in_sync")
        self.assertEqual(p["codex"]["turns"], 2)

    def test_claude_deleted_recreates_from_codex(self):
        did = self.h.codex_thread([("hi", "hello")])
        cid = self.h.claude_session([("hi", "hello")])
        self.h.codex_import_record(cid, did)
        self.h.run("bootstrap")
        os.remove(self.h.claude_path(cid))
        p = self.h.pair(self.h.scan(), cid)
        self.assertEqual(p["missing_side"], "claude")
        out = self.h.run("recreate", "--pair", cid)
        self.assertEqual(out["recreated"], "claude")
        v = self.h.scan()
        self.assertEqual(v["totals"]["pairs"], 1)
        self.assertEqual(self.h.pair(v, did)["status"], "in_sync")

    def test_recreate_refuses_when_both_exist(self):
        sid = self.h.claude_session([("hi", "hello")])
        self.h.codex_thread([("hi", "hello")], sid=sid)
        self.h.run("bootstrap")
        out = self.h.run("recreate", "--pair", sid, ok=False)
        self.assertIn("nothing to recreate", out["error"])

    def test_archived_codex_thread_is_not_missing(self):
        sid = self.h.claude_session([("hi", "hello")])
        self.h.codex_thread([("hi", "hello")], sid=sid, archived=True)
        self.h.run("bootstrap")
        self.assertEqual(self.h.pair(self.h.scan(), sid)["status"], "in_sync")


if __name__ == "__main__":
    unittest.main()
