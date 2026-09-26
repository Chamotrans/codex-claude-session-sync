"""Synthetic Claude Code + Codex session stores inside a temporary HOME.

Every test builds its own fake home directory and runs the engine as a subprocess with HOME pointing at it,
so the real ~/.claude and ~/.codex are never read or written.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ENGINE_DIR, "sessionsync.py")

THREADS_DDL = """
CREATE TABLE threads (
    id TEXT PRIMARY KEY, rollout_path TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    source TEXT NOT NULL, model_provider TEXT NOT NULL, cwd TEXT NOT NULL, title TEXT NOT NULL,
    sandbox_policy TEXT NOT NULL, approval_mode TEXT NOT NULL, tokens_used INTEGER NOT NULL DEFAULT 0,
    has_user_event INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, archived_at INTEGER,
    git_sha TEXT, git_branch TEXT, git_origin_url TEXT, cli_version TEXT NOT NULL DEFAULT '',
    first_user_message TEXT NOT NULL DEFAULT '', agent_nickname TEXT, agent_role TEXT,
    memory_mode TEXT NOT NULL DEFAULT 'enabled', model TEXT, reasoning_effort TEXT, agent_path TEXT,
    created_at_ms INTEGER, updated_at_ms INTEGER, thread_source TEXT, preview TEXT NOT NULL DEFAULT '',
    recency_at INTEGER NOT NULL DEFAULT 0, recency_at_ms INTEGER NOT NULL DEFAULT 0,
    history_mode TEXT NOT NULL DEFAULT 'legacy', name TEXT, is_pinned INTEGER NOT NULL DEFAULT 0,
    thread_section_id TEXT, section_position INTEGER, section_entered_at_ms INTEGER, project_id TEXT,
    originator TEXT, daybreak_enabled BOOLEAN
);
"""

OLD = time.time() - 3600  # mtime used for fixture files so the engine's "just written" guard does not trip


def slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def iso(offset_s=0):
    t = time.time() - 7200 + offset_s
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


class FakeHome:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sessionsync-test-")
        self.home = os.path.realpath(self._tmp.name)
        real_home = os.path.realpath(os.path.expanduser("~"))
        assert self.home != real_home and not self.home.startswith(real_home + "/."), "fixture must not live in real HOME"
        self.cwd = os.path.join(self.home, "work", "proj")
        os.makedirs(self.cwd)
        os.makedirs(os.path.join(self.home, ".codex", "sessions"))
        os.makedirs(os.path.join(self.home, ".claude", "projects"))
        db = sqlite3.connect(self.codex_db)
        db.executescript(THREADS_DDL)
        db.commit()
        db.close()
        self.desktop_dir = os.path.join(self.home, "Library", "Application Support", "Claude",
                                        "claude-code-sessions", "org", "acct")
        os.makedirs(self.desktop_dir)

    def cleanup(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------------ paths
    @property
    def codex_db(self):
        return os.path.join(self.home, ".codex", "state_5.sqlite")

    def claude_path(self, sid, cwd=None):
        return os.path.join(self.home, ".claude", "projects", slug(cwd or self.cwd), sid + ".jsonl")

    # ------------------------------------------------------------------ Claude
    def claude_session(self, turns, sid=None, cwd=None, title=None):
        """turns: list of (user_text, assistant_text, [tool (name, input, result)...])."""
        sid = sid or str(uuid.uuid4())
        cwd = cwd or self.cwd
        path = self.claude_path(sid, cwd)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._write_claude_turns(path, sid, cwd, turns, parent=None, t0=0)
        if title:
            with open(path, "a") as f:
                # Claude Code writes compact JSON; the engine's own title lines use default separators.
                f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": sid}, separators=(",", ":")) + "\n")
        os.utime(path, (OLD, OLD))
        return sid

    def add_claude_turns(self, sid, turns, cwd=None):
        path = self.claude_path(sid, cwd)
        leaf = None
        for line in open(path):
            o = json.loads(line)
            if o.get("uuid"):
                leaf = o["uuid"]
        self._write_claude_turns(path, sid, cwd or self.cwd, turns, parent=leaf, t0=600)
        os.utime(path, (OLD, OLD))

    def _write_claude_turns(self, path, sid, cwd, turns, parent, t0):
        with open(path, "a") as f:
            k = 0
            for turn in turns:
                user, reply = turn[0], turn[1]
                tools = turn[2] if len(turn) > 2 else []
                base = {"isSidechain": False, "userType": "external", "cwd": cwd, "sessionId": sid, "version": "2.1.0"}
                rec = dict(base, parentUuid=parent, type="user", uuid=str(uuid.uuid4()), timestamp=iso(t0 + k),
                           message={"role": "user", "content": user}, origin={"kind": "human"})
                f.write(json.dumps(rec) + "\n"); parent = rec["uuid"]; k += 1
                for name, inp, result in tools:
                    tid = "toolu_" + uuid.uuid4().hex[:20]
                    rec = dict(base, parentUuid=parent, type="assistant", uuid=str(uuid.uuid4()), timestamp=iso(t0 + k),
                               message={"role": "assistant", "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]})
                    f.write(json.dumps(rec) + "\n"); parent = rec["uuid"]; k += 1
                    rec = dict(base, parentUuid=parent, type="user", uuid=str(uuid.uuid4()), timestamp=iso(t0 + k),
                               message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]})
                    f.write(json.dumps(rec) + "\n"); parent = rec["uuid"]; k += 1
                rec = dict(base, parentUuid=parent, type="assistant", uuid=str(uuid.uuid4()), timestamp=iso(t0 + k),
                           message={"role": "assistant", "content": [{"type": "text", "text": reply}]})
                f.write(json.dumps(rec) + "\n"); parent = rec["uuid"]; k += 1

    def register_desktop(self, cli_id, title="x"):
        lid = "local_" + str(uuid.uuid4())
        with open(os.path.join(self.desktop_dir, lid + ".json"), "w") as f:
            json.dump({"sessionId": lid, "cliSessionId": cli_id, "cwd": self.cwd, "title": title}, f)
        return lid

    # ------------------------------------------------------------------ Codex
    def codex_thread(self, turns, sid=None, cwd=None, title="", name=None, archived=False, thread_source="user",
                     day="2026/09/20"):
        """turns: list of (user_text, assistant_text, [tool (name, args_dict, output)...])."""
        sid = sid or str(uuid.uuid4())
        cwd = cwd or self.cwd
        base_dir = os.path.join(self.home, ".codex", "archived_sessions" if archived else "sessions",
                                *([] if archived else day.split("/")))
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, "rollout-2026-09-20T10-00-00-%s.jsonl" % sid)
        lines = [{"timestamp": iso(0), "ordinal": 0, "type": "session_meta",
                  "payload": {"session_id": sid, "id": sid, "timestamp": iso(0), "cwd": cwd, "originator": "Codex Desktop",
                              "cli_version": "0.1", "source": "vscode", "thread_source": thread_source,
                              "model_provider": "openai", "history_mode": "paginated"}}]
        self._codex_turn_lines(lines, turns, t0=1)
        with open(path, "w") as f:
            for l in lines:
                f.write(json.dumps(l) + "\n")
        os.utime(path, (OLD, OLD))
        first = turns[0][0] if turns else ""
        db = sqlite3.connect(self.codex_db)
        now_ms = int(time.time() * 1000) - 7_200_000
        db.execute("""insert into threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                      sandbox_policy, approval_mode, archived, first_user_message, created_at_ms, updated_at_ms,
                      thread_source, name, history_mode) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (sid, path, now_ms // 1000, now_ms // 1000, "vscode", "openai", cwd, title or first,
                    '{"type":"read-only"}', "on-request", 1 if archived else 0, first, now_ms, now_ms,
                    thread_source, name, "paginated"))
        db.commit()
        db.close()
        return sid

    def add_codex_turns(self, sid, turns):
        db = sqlite3.connect(self.codex_db)
        path = db.execute("select rollout_path from threads where id=?", (sid,)).fetchone()[0]
        db.close()
        last = -1
        for line in open(path):
            last = max(last, json.loads(line).get("ordinal", -1))
        lines = []
        self._codex_turn_lines(lines, turns, t0=last + 1)
        with open(path, "a") as f:
            for l in lines:
                f.write(json.dumps(l) + "\n")
        os.utime(path, (OLD, OLD))

    def _codex_turn_lines(self, lines, turns, t0):
        n = t0
        for turn in turns:
            user, reply = turn[0], turn[1]
            tools = turn[2] if len(turn) > 2 else []
            lines.append({"timestamp": iso(n), "ordinal": n, "type": "response_item",
                          "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": user}]}}); n += 1
            for name, args, out in tools:
                cid = "call_" + uuid.uuid4().hex[:16]
                lines.append({"timestamp": iso(n), "ordinal": n, "type": "response_item",
                              "payload": {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": cid}}); n += 1
                lines.append({"timestamp": iso(n), "ordinal": n, "type": "response_item",
                              "payload": {"type": "function_call_output", "call_id": cid, "output": out}}); n += 1
            lines.append({"timestamp": iso(n), "ordinal": n, "type": "response_item",
                          "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply}]}}); n += 1

    def codex_import_record(self, claude_id, codex_id, cwd=None):
        """What Codex Desktop writes when it auto-imports a Claude session."""
        p = os.path.join(self.home, ".codex", "external_agent_session_imports.json")
        data = json.load(open(p)) if os.path.exists(p) else {"records": []}
        data["records"].append({"source_path": self.claude_path(claude_id, cwd), "imported_thread_id": codex_id})
        json.dump(data, open(p, "w"))

    def delete_codex_thread(self, sid):
        db = sqlite3.connect(self.codex_db)
        row = db.execute("select rollout_path from threads where id=?", (sid,)).fetchone()
        db.execute("delete from threads where id=?", (sid,))
        db.commit()
        db.close()
        if row and os.path.exists(row[0]):
            os.remove(row[0])

    # ------------------------------------------------------------------ engine
    def run(self, *args, ok=True):
        env = dict(os.environ, HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        p = subprocess.run([sys.executable, ENGINE] + list(args), env=env, capture_output=True, text=True, timeout=120)
        try:
            out = json.loads(p.stdout)
        except Exception:
            raise AssertionError("engine output not JSON (rc=%d): %s\n%s" % (p.returncode, p.stdout[-800:], p.stderr[-800:]))
        if ok and p.returncode != 0:
            raise AssertionError("engine failed: %s" % out)
        return out

    def scan(self):
        return self.run("scan")

    def pair(self, view, pair_id):
        for p in view["pairs"]:
            if p["pair_id"] == pair_id:
                return p
        raise AssertionError("pair %s not in view; pairs=%s" % (pair_id, [p["pair_id"] for p in view["pairs"]]))

    def age_all(self):
        """Push every session file's mtime into the past (the engine refuses to touch just-written files)."""
        for root in (os.path.join(self.home, ".claude"), os.path.join(self.home, ".codex")):
            for d, _, files in os.walk(root):
                for f in files:
                    if f.endswith(".jsonl"):
                        os.utime(os.path.join(d, f), (OLD, OLD))
