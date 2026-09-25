#!/usr/bin/env python3
"""SessionSync engine: two-way sync of coding-agent sessions between Claude Code and OpenAI Codex.

Think "cloud save sync": every conversation can exist on both sides; the engine tracks how many
human turns each side had at the last sync and pushes the newer side's extra turns to the other.

CLI (all output is JSON unless --pretty):
  sessionsync.py scan                       list sessions on both sides, pairs and their status
  sessionsync.py bootstrap                  seed pairs from same-id sessions and Codex's own import records
  sessionsync.py sync --pair ID [--prefer claude|codex|both]
  sessionsync.py sync-all                   sync every pair that is not in conflict
  sessionsync.py create --from claude|codex --id ID    make the counterpart on the other side
  sessionsync.py link --claude ID --codex ID           pair two existing sessions manually
  sessionsync.py unlink --pair ID
  sessionsync.py ignore --side claude|codex --id ID    hide a session from the "unpaired" list
  sessionsync.py open --side claude|codex --id ID      print the command to resume that session
  sessionsync.py register --id CLAUDE_ID               add a CLI session to the Claude desktop app sidebar
  sessionsync.py register-all [--all]                  add Codex-derived (or, with --all, every) CLI session to the sidebar
  sessionsync.py retitle                               use Codex's generated titles for Codex-derived Claude sessions
"""
import argparse, glob, json, os, re, sqlite3, sys, time, uuid, datetime, subprocess, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex2claude as c2c  # noqa: E402

HOME = os.path.expanduser("~")
CLAUDE_PROJECTS = os.path.join(HOME, ".claude", "projects")
CLAUDE_SESSIONS_REG = os.path.join(HOME, ".claude", "sessions")
CODEX_DIR = os.path.join(HOME, ".codex")
CODEX_SESSIONS = os.path.join(CODEX_DIR, "sessions")
CODEX_ARCHIVED = os.path.join(CODEX_DIR, "archived_sessions")
CODEX_DB = os.path.join(CODEX_DIR, "state_5.sqlite")
CODEX_LOCKS = os.path.join(CODEX_DIR, "thread-writer-locks")
STATE_DIR = os.path.join(HOME, "Library", "Application Support", "SessionSync")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
CACHE_FILE = os.path.join(STATE_DIR, "cache.json")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ACTIVE_LOCK_MAX_AGE = 3600          # a Codex writer lock younger than this means "thread is open"
RECENT_WRITE_GUARD = 20             # never touch a file modified in the last N seconds
TOOL_RESULT_MAX = 20_000            # chars of a Claude tool result copied into Codex


# ----------------------------------------------------------------------------- utilities

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def iso_from_ms(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ms_from_iso(s):
    if not s:
        return 0
    try:
        s2 = s.replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(s2).timestamp() * 1000)
    except Exception:
        return 0


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def file_sig(path):
    st = os.stat(path)
    return "%d:%d" % (st.st_size, int(st.st_mtime))


def recently_written(path):
    try:
        return time.time() - os.stat(path).st_mtime < RECENT_WRITE_GUARD
    except Exception:
        return False


class Cache:
    def __init__(self):
        self.data = load_json(CACHE_FILE, {})
        self.dirty = False

    def get(self, path, compute):
        real = path.split("#")[0]
        try:
            sig = file_sig(real)
        except FileNotFoundError:
            return None
        ent = self.data.get(path)
        if ent and ent.get("sig") == sig:
            return ent["v"]
        v = compute(path)
        self.data[path] = {"sig": sig, "v": v}
        self.dirty = True
        return v

    def save(self):
        if self.dirty:
            save_json(CACHE_FILE, self.data)


# ----------------------------------------------------------------------------- Claude side

def claude_is_human(rec):
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isVisibleInTranscriptOnly"):
        return False
    m = rec.get("message") or {}
    c = m.get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def claude_text(content):
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def parse_claude_file(path):
    """Cheap summary of a Claude session file."""
    info = {"turns": 0, "first_prompt": None, "title": None, "last_activity": None, "cwd": None,
            "leaf": None, "assistant_msgs": 0, "imported_from_codex": False}
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"custom-title"' in line and '"customTitle"' in line:
                try:
                    o = json.loads(line)
                    if o.get("type") == "custom-title":
                        info["title"] = o.get("customTitle")
                        if (info["title"] or "").startswith("[Codex] "):
                            info["imported_from_codex"] = True
                        continue
                except Exception:
                    pass
            if '"type":"user"' not in line and '"type": "user"' not in line and \
               '"type":"assistant"' not in line and '"type": "assistant"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            t = o.get("type")
            if t not in ("user", "assistant"):
                continue
            if o.get("cwd") and not info["cwd"]:
                info["cwd"] = o["cwd"]
            ts = o.get("timestamp")
            if ts and (info["last_activity"] is None or ts > info["last_activity"]):
                info["last_activity"] = ts
            if o.get("uuid"):
                info["leaf"] = o["uuid"]
            if t == "assistant":
                info["assistant_msgs"] += 1
            elif claude_is_human(o):
                info["turns"] += 1
                if info["first_prompt"] is None:
                    info["first_prompt"] = claude_text((o.get("message") or {}).get("content"))[:200]
    return info


def claude_turns(path):
    """Full parse: list of turns, each a list of records starting with a human message."""
    turns, cur = [], None
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("type") not in ("user", "assistant"):
                continue
            if claude_is_human(o):
                cur = [o]
                turns.append(cur)
            elif cur is not None:
                cur.append(o)
    return turns


def scan_claude(cache):
    out = []
    registered = claude_desktop_registered()
    for d in sorted(glob.glob(os.path.join(CLAUDE_PROJECTS, "*"))):
        if not os.path.isdir(d):
            continue
        for f in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
            sid = os.path.basename(f)[:-6]
            if not UUID_RE.match(sid):
                continue
            info = cache.get(f, parse_claude_file)
            if not info or (info["turns"] == 0 and info["assistant_msgs"] == 0):
                continue
            out.append({"side": "claude", "id": sid, "path": f, "cwd": info["cwd"] or "",
                        "title": info["title"] or (info["first_prompt"] or "").split("\n")[0][:80],
                        "first_prompt": info["first_prompt"], "turns": info["turns"],
                        "last_activity": info["last_activity"], "size": os.path.getsize(f),
                        "imported": info["imported_from_codex"], "in_desktop": sid in registered})
    return out


def claude_active_ids():
    ids = set()
    for f in glob.glob(os.path.join(CLAUDE_SESSIONS_REG, "*.json")):
        o = load_json(f, {})
        if o.get("sessionId"):
            try:
                os.kill(int(o.get("pid", 0)), 0)
                ids.add(o["sessionId"])
            except Exception:
                pass
    return ids


# ----------------------------------------------------------------------------- Codex side

def codex_db(readonly=True):
    if readonly:
        return sqlite3.connect("file:%s?mode=ro" % CODEX_DB, uri=True, timeout=5)
    return sqlite3.connect(CODEX_DB, timeout=10)


_CODEX_INDEX = None


def codex_file_index(cache):
    """One walk of the rollout tree: sid -> [(meta_ts, path)] for non-subagent segments (metadata cached)."""
    global _CODEX_INDEX
    if _CODEX_INDEX is not None:
        return _CODEX_INDEX
    idx = collections.defaultdict(list)
    files = glob.glob(os.path.join(CODEX_SESSIONS, "**", "*.jsonl"), recursive=True) + \
        glob.glob(os.path.join(CODEX_ARCHIVED, "*.jsonl"))

    def head(path):
        try:
            with open(path, errors="replace") as fh:
                meta = (json.loads(fh.readline()) or {}).get("payload") or {}
        except Exception:
            return None
        return {"sid": meta.get("session_id"), "ts": meta.get("timestamp") or "", "sub": c2c.is_subagent(meta)}

    for f in files:
        m = cache.get(f + "#head", lambda p: {"head": head(p[:-5])})
        h = (m or {}).get("head")
        if h and h.get("sid") and not h.get("sub"):
            idx[h["sid"]].append((h["ts"], f))
    _CODEX_INDEX = idx
    return idx


def codex_segments(sid, cache=None):
    """Chronological, de-duplicated segment files of one thread (Codex writes a new rollout per resume/retry)."""
    cache = cache or Cache()
    segs = sorted(codex_file_index(cache).get(sid, []))
    best, order = {}, []
    for ts, path in segs:
        info = cache.get(path, parse_codex_segment) or {}
        key, n = info.get("first_prompt"), info.get("turns", 0)
        if key is None:
            continue
        if key not in best:
            order.append(key)
            best[key] = (n, ts, path)
        elif (n, ts) >= best[key][:2]:
            best[key] = (n, ts, path)
    return [best[k][2] for k in order]


def parse_codex_segment(path):
    info = {"turns": 0, "first_prompt": None, "last_activity": None, "last_ordinal": -1}
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"role":"user"' in line or '"role": "user"' in line or '"ordinal"' in line:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if isinstance(o.get("ordinal"), int):
                    info["last_ordinal"] = max(info["last_ordinal"], o["ordinal"])
                ts = o.get("timestamp")
                p = o.get("payload") or {}
                if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                    c = c2c.user_blocks(p.get("content"))
                    if c is not None:
                        info["turns"] += 1
                        if info["first_prompt"] is None:
                            info["first_prompt"] = (c if isinstance(c, str) else c2c.text_of(
                                [b for b in c if b.get("type") == "text"]))[:200]
                if o.get("type") == "response_item" and p.get("type") in ("message",) and ts:
                    if info["last_activity"] is None or ts > info["last_activity"]:
                        info["last_activity"] = ts
    return info


def codex_turns(sid):
    """All human turns across the deduplicated segments: list of lists of (payload, timestamp)."""
    turns, cur = [], None
    for f in codex_segments(sid):
        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "response_item":
                    continue
                p = o.get("payload") or {}
                if p.get("type") == "message" and p.get("role") == "user" and c2c.user_blocks(p.get("content")) is not None:
                    cur = [(p, o.get("timestamp"))]
                    turns.append(cur)
                elif cur is not None:
                    cur.append((p, o.get("timestamp")))
    return turns


def scan_codex(cache):
    out = []
    if not os.path.exists(CODEX_DB):
        return out
    db = codex_db()
    rows = db.execute("""select id, rollout_path, cwd, title, name, first_user_message, updated_at_ms,
                                created_at_ms, archived, thread_source, agent_role, source
                         from threads""").fetchall()
    db.close()
    for (sid, rollout, cwd, title, name, first_msg, upd_ms, cre_ms, archived, tsrc, arole, source) in rows:
        if tsrc == "subagent" or (arole or "") or "subagent" in (source or ""):
            continue
        segs = codex_segments(sid, cache)
        if not segs:
            continue
        turns, first, last = 0, None, None
        for f in segs:
            info = cache.get(f, parse_codex_segment)
            if not info:
                continue
            turns += info["turns"]
            first = first or info["first_prompt"]
            if info["last_activity"] and (last is None or info["last_activity"] > last):
                last = info["last_activity"]
        if turns == 0:
            continue
        out.append({"side": "codex", "id": sid, "path": segs[-1], "segments": segs, "cwd": cwd or "",
                    "title": (name or title or first or "").split("\n")[0][:80], "first_prompt": first,
                    "turns": turns, "last_activity": last or iso_from_ms(upd_ms or cre_ms or 0),
                    "size": sum(os.path.getsize(f) for f in segs), "archived": bool(archived)})
    return out


def codex_active_ids():
    ids = set()
    for f in glob.glob(os.path.join(CODEX_LOCKS, "*.lock")):
        sid = os.path.basename(f)[:-5]
        if UUID_RE.match(sid) and time.time() - os.stat(f).st_mtime < ACTIVE_LOCK_MAX_AGE:
            ids.add(sid)
    return ids


# ----------------------------------------------------------------------------- Claude Desktop sidebar

CLAUDE_DESKTOP_ROOT = os.path.join(HOME, "Library", "Application Support", "Claude", "claude-code-sessions")


def claude_desktop_dir():
    """The account folder the Claude desktop app is currently using (the one with the newest local_*.json)."""
    best, best_m = None, -1
    for d in glob.glob(os.path.join(CLAUDE_DESKTOP_ROOT, "*", "*")):
        if not os.path.isdir(d):
            continue
        files = glob.glob(os.path.join(d, "local_*.json"))
        m = max([os.stat(f).st_mtime for f in files], default=-1)
        if m > best_m:
            best, best_m = d, m
    return best


def claude_desktop_registered():
    """cli session id -> desktop session id, for every session the desktop sidebar knows about."""
    out = {}
    d = claude_desktop_dir()
    if not d:
        return out
    for f in glob.glob(os.path.join(d, "local_*.json")):
        o = load_json(f, {})
        if o.get("cliSessionId"):
            out[o["cliSessionId"]] = o.get("sessionId")
    return out


def register_claude_desktop(sess):
    """Add a CLI session to the Claude desktop app's sidebar by writing a registry entry next to the app's own.
    User-approved feature: the entry only points the sidebar at an existing CLI session file."""
    d = claude_desktop_dir()
    if not d:
        raise RuntimeError("Claude desktop session folder not found")
    if sess["id"] in claude_desktop_registered():
        return None
    lid = "local_" + str(uuid.uuid4())
    now = int(time.time() * 1000)
    last = ms_from_iso(sess.get("last_activity")) or now
    entry = {"sessionId": lid, "cliSessionId": sess["id"], "cwd": sess.get("cwd") or HOME, "originCwd": sess.get("cwd") or HOME,
             "lastFocusedAt": last, "createdAt": last, "lastActivityAt": last, "isArchived": False,
             "title": (sess.get("title") or "")[:120], "titleSource": "auto", "permissionMode": "default",
             "completedTurns": sess.get("turns", 0), "titleTurn": 0, "bridgeSessionIds": [],
             "alwaysAllowedReasons": [], "sessionPermissionUpdates": [], "spawnSeed": {}}
    save_json(os.path.join(d, lid + ".json"), entry)
    return lid


def codex_titles():
    """thread id -> Codex's own generated title (name or title), when it is not just the first prompt."""
    out = {}
    try:
        db = codex_db()
        for sid, name, title, first in db.execute("select id, name, title, first_user_message from threads"):
            t = (name or title or "").strip()
            f = (first or "").strip()
            if not t or (f and (f.startswith(t) or t.startswith(f[:40]))):
                continue
            out[sid] = t
        db.close()
    except Exception:
        pass
    return out


def retitle_claude_desktop(claude, state, pairs_only=True):
    """Give Codex-derived sessions Codex's generated title, in the desktop registry and in the CLI session file."""
    titles = codex_titles()
    d = claude_desktop_dir()
    reg = {}
    if d:
        for f in glob.glob(os.path.join(d, "local_*.json")):
            o = load_json(f, {})
            if o.get("cliSessionId"):
                reg[o["cliSessionId"]] = (f, o)
    # only pairs that originated on the Codex side; Claude-origin threads carry a "[Claude] …" title over there
    codex_for_claude = {p["claude_id"]: p["codex_id"] for p in state["pairs"].values()
                        if p.get("origin") in ("bootstrap_same_id", "created_claude")}
    changed = []
    for s in claude:
        cid = codex_for_claude.get(s["id"])
        if not cid or cid not in titles:
            continue
        new = "[Codex] " + titles[cid]
        if s.get("title") == new:
            continue
        if s["id"] in reg:
            f, o = reg[s["id"]]
            o["title"] = new[:120]
            save_json(f, o)
        with open(s["path"], "a") as w:
            w.write(json.dumps({"type": "custom-title", "customTitle": new[:120], "sessionId": s["id"]}, ensure_ascii=False) + "\n")
        changed.append((s["id"], new))
    return changed


# ----------------------------------------------------------------------------- writers

class CodexWriter:
    """Appends Claude turns to a Codex rollout file, in the same flattened style Codex itself uses
    when it imports a Claude session (tool calls become tagged assistant text)."""

    def __init__(self, sid, start_ordinal):
        self.sid = sid
        self.ordinal = start_ordinal
        self.lines = []

    def rec(self, ts, typ, payload):
        self.lines.append({"timestamp": ts or now_iso(), "ordinal": self.ordinal, "type": typ, "payload": payload})
        self.ordinal += 1

    def item(self, ts, turn_id, item):
        self.rec(ts, "event_msg", {"type": "item_completed", "thread_id": self.sid, "turn_id": turn_id,
                                   "item": item, "completed_at_ms": ms_from_iso(ts) or int(time.time() * 1000)})

    def user(self, ts, turn_id, content):
        text = claude_text(content) if not isinstance(content, str) else content
        parts = []
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    parts.append({"type": "input_text", "text": b["text"]})
                elif b.get("type") == "image" and (b.get("source") or {}).get("type") == "base64":
                    s = b["source"]
                    parts.append({"type": "input_image", "image_url": "data:%s;base64,%s" % (s.get("media_type", "image/png"), s.get("data", ""))})
        else:
            parts = [{"type": "input_text", "text": content}]
        self.item(ts, turn_id, {"type": "UserMessage", "id": "item-" + uuid.uuid4().hex[:12],
                                "content": [{"type": "text", "text": text or "[image]", "text_elements": []}]})
        self.rec(ts, "response_item", {"type": "message", "role": "user", "content": parts})

    def assistant(self, ts, turn_id, text):
        if not text.strip():
            return
        self.item(ts, turn_id, {"type": "AgentMessage", "id": "item-" + uuid.uuid4().hex[:12],
                                "content": [{"type": "Text", "text": text}]})
        self.rec(ts, "response_item", {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})

    def turn(self, records):
        """records: Claude JSONL records of one human turn (first is the human message)."""
        turn_id = "sync-" + uuid.uuid4().hex[:16]
        first = records[0]
        ts0 = first.get("timestamp") or now_iso()
        self.rec(ts0, "event_msg", {"type": "task_started", "turn_id": turn_id, "started_at": ms_from_iso(ts0) // 1000,
                                    "model_context_window": None, "collaboration_mode_kind": "default"})
        self.user(ts0, turn_id, (first.get("message") or {}).get("content"))
        last_agent = None
        for r in records[1:]:
            m = r.get("message") or {}
            c = m.get("content")
            ts = r.get("timestamp")
            if r.get("type") == "assistant" and isinstance(c, list):
                for b in c:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text":
                        self.assistant(ts, turn_id, b["text"])
                        last_agent = b["text"]
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") or {}
                        body = "\n".join("%s: %s" % (k, v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                                         for k, v in inp.items()) if isinstance(inp, dict) else json.dumps(inp, ensure_ascii=False)
                        self.assistant(ts, turn_id, "[external_agent_tool_call: %s]\n%s\n[/external_agent_tool_call]" % (b.get("name"), body[:TOOL_RESULT_MAX]))
            elif r.get("type") == "user" and isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        t = b.get("content")
                        if isinstance(t, list):
                            t = "".join(x.get("text", "") for x in t if isinstance(x, dict))
                        t = (t or "")[:TOOL_RESULT_MAX]
                        self.assistant(ts, turn_id, "[external_agent_tool_result]\n%s\n[/external_agent_tool_result]" % t)
            elif r.get("type") == "user" and r.get("isCompactSummary"):
                self.assistant(ts, turn_id, "[context summary]\n%s" % claude_text(c)[:TOOL_RESULT_MAX])
        self.rec(records[-1].get("timestamp") or ts0, "event_msg", {"type": "task_complete", "turn_id": turn_id, "last_agent_message": last_agent})

    def dump(self):
        return "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in self.lines)


def codex_base_instructions():
    """Reuse the base_instructions text from the newest native Codex session so a resumed thread behaves normally."""
    try:
        db = codex_db()
        row = db.execute("select rollout_path from threads where thread_source='user' and rollout_path!='' order by updated_at desc limit 5").fetchall()
        db.close()
        for (p,) in row:
            with open(p, errors="replace") as fh:
                meta = (json.loads(fh.readline()) or {}).get("payload") or {}
            if meta.get("base_instructions"):
                return meta["base_instructions"], meta.get("cli_version", "")
    except Exception:
        pass
    return None, ""


def codex_defaults():
    try:
        db = codex_db()
        row = db.execute("select sandbox_policy, approval_mode, model, reasoning_effort, cli_version from threads where thread_source='user' order by updated_at desc limit 1").fetchone()
        db.close()
        if row:
            return {"sandbox_policy": row[0], "approval_mode": row[1], "model": row[2], "reasoning_effort": row[3], "cli_version": row[4]}
    except Exception:
        pass
    return {"sandbox_policy": '{"type":"read-only"}', "approval_mode": "on-request", "model": None, "reasoning_effort": None, "cli_version": ""}


def create_codex_from_claude(claude_sess, state):
    sid = claude_sess["id"]
    turns = claude_turns(claude_sess["path"])
    if not turns:
        raise RuntimeError("no turns in Claude session")
    first_ts = turns[0][0].get("timestamp") or now_iso()
    local = datetime.datetime.fromtimestamp(ms_from_iso(first_ts) / 1000)
    out_dir = os.path.join(CODEX_SESSIONS, local.strftime("%Y"), local.strftime("%m"), local.strftime("%d"))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "rollout-%s-%s.jsonl" % (local.strftime("%Y-%m-%dT%H-%M-%S"), sid))
    if os.path.exists(out_path):
        raise RuntimeError("Codex rollout already exists: " + out_path)
    base, cli_version = codex_base_instructions()
    defaults = codex_defaults()
    cwd = claude_sess.get("cwd") or HOME
    w = CodexWriter(sid, 0)
    meta = {"session_id": sid, "id": sid, "timestamp": first_ts, "cwd": cwd, "originator": "Codex Desktop",
            "cli_version": defaults.get("cli_version") or cli_version, "source": "vscode", "thread_source": "user",
            "model_provider": "openai", "history_mode": "paginated",
            "context_window": {"window_id": str(uuid.uuid4())}}
    if base:
        meta["base_instructions"] = base
    w.rec(first_ts, "session_meta", meta)
    for t in turns:
        w.turn(t)
    w.rec(now_iso(), "event_msg", {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0},
                                                                    "last_token_usage": {"input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0},
                                                                    "model_context_window": None}, "rate_limits": None})
    with open(out_path, "w") as f:
        f.write(w.dump())
    title = ("[Claude] " + (claude_sess.get("title") or claude_sess.get("first_prompt") or "").split("\n")[0])[:120]
    first_prompt = claude_sess.get("first_prompt") or ""
    now_ms = int(time.time() * 1000)
    cre_ms = ms_from_iso(first_ts) or now_ms
    last_ms = ms_from_iso(claude_sess.get("last_activity")) or now_ms
    db = codex_db(readonly=False)
    db.execute("""insert into threads (id, rollout_path, created_at, updated_at, source, model_provider, cwd, title,
                    sandbox_policy, approval_mode, tokens_used, has_user_event, archived, cli_version, first_user_message,
                    model, reasoning_effort, created_at_ms, updated_at_ms, thread_source, preview, recency_at, recency_at_ms,
                    history_mode, name, is_pinned, originator)
                  values (?,?,?,?,?,?,?,?,?,?,0,0,0,?,?,?,?,?,?,?,?,?,?,?,?,0,?)""",
               (sid, out_path, cre_ms // 1000, last_ms // 1000, "vscode", "openai", cwd, title,
                defaults["sandbox_policy"], defaults["approval_mode"], defaults.get("cli_version") or cli_version, first_prompt,
                defaults.get("model"), defaults.get("reasoning_effort"), cre_ms, last_ms, "user", first_prompt[:200],
                last_ms // 1000, last_ms, "paginated", title, "Codex Desktop"))
    db.commit()
    db.close()
    state["pairs"][sid] = {"claude_id": sid, "codex_id": sid, "synced_claude_turns": len(turns),
                           "synced_codex_turns": len(turns), "last_sync": now_iso(), "origin": "created_codex"}
    return out_path


def create_claude_from_codex(codex_sess, state):
    sid = codex_sess["id"]
    segs = codex_sess.get("segments") or codex_segments(sid)
    lines = c2c.convert_files(segs)
    if not lines:
        raise RuntimeError("nothing to convert")
    out_dir = os.path.join(CLAUDE_PROJECTS, c2c.slug(codex_sess.get("cwd") or HOME))
    out_path = os.path.join(out_dir, sid + ".jsonl")
    if os.path.exists(out_path):
        raise RuntimeError("Claude session already exists: " + out_path)
    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w") as w:
        for rec in lines:
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
    claude_n = parse_claude_file(out_path)["turns"]
    try:
        register_claude_desktop({"id": sid, "cwd": codex_sess.get("cwd"), "title": "[Codex] " + (codex_titles().get(sid) or codex_sess.get("title") or ""),
                                 "last_activity": codex_sess.get("last_activity"), "turns": claude_n})
    except Exception:
        pass
    state["pairs"][sid] = {"claude_id": sid, "codex_id": sid, "synced_claude_turns": claude_n,
                           "synced_codex_turns": codex_sess["turns"], "last_sync": now_iso(), "origin": "created_claude"}
    return out_path


def append_to_codex(codex_sess, new_turns):
    """new_turns: list of Claude turn record lists."""
    path = codex_sess["path"]
    info = parse_codex_segment(path)
    w = CodexWriter(codex_sess["id"], info["last_ordinal"] + 1)
    for t in new_turns:
        w.turn(t)
    with open(path, "a") as f:
        f.write(w.dump())
    last_ms = int(time.time() * 1000)
    db = codex_db(readonly=False)
    db.execute("update threads set updated_at=?, updated_at_ms=?, recency_at=?, recency_at_ms=? where id=?",
               (last_ms // 1000, last_ms, last_ms // 1000, last_ms, codex_sess["id"]))
    db.commit()
    db.close()
    return len(w.lines)


def append_to_claude(claude_sess, new_turns):
    """new_turns: list of Codex turns, each a list of (payload, timestamp)."""
    path = claude_sess["path"]
    info = parse_claude_file(path)
    meta = {"session_id": claude_sess["id"], "cwd": claude_sess.get("cwd") or HOME}
    conv = c2c.Converter(meta)
    conv.parent = info["leaf"]
    last_ts = None
    for t in new_turns:
        for p, ts in t:
            last_ts = ts or last_ts
            conv.feed(p, ts or now_iso())
    conv.close_pending(last_ts or now_iso())
    lines = conv.lines
    if not lines:
        return 0
    lines.append({"type": "last-prompt", "lastPrompt": (conv.last_prompt or "")[:500], "leafUuid": conv.parent,
                  "sessionId": claude_sess["id"]})
    with open(path, "a") as f:
        for rec in lines:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(lines)


# ----------------------------------------------------------------------------- pairing & status

def load_state():
    st = load_json(STATE_FILE, {})
    st.setdefault("pairs", {})
    st.setdefault("ignored", {"claude": [], "codex": []})
    return st


def pair_status(pair, cs, cd):
    if cs is None or cd is None:
        return "missing"
    sc, sd = pair.get("synced_claude_turns", 0), pair.get("synced_codex_turns", 0)
    c_new, d_new = cs["turns"] - sc, cd["turns"] - sd
    if c_new < 0 or d_new < 0:
        return "rebased"
    if c_new == 0 and d_new == 0:
        return "in_sync"
    if c_new > 0 and d_new == 0:
        return "claude_newer"
    if d_new > 0 and c_new == 0:
        return "codex_newer"
    return "conflict"


def build_view(state, claude, codex):
    cmap = {s["id"]: s for s in claude}
    dmap = {s["id"]: s for s in codex}
    active_c, active_d = claude_active_ids(), codex_active_ids()
    pairs = []
    paired_c, paired_d = set(), set()
    for pid, p in state["pairs"].items():
        cs, cd = cmap.get(p["claude_id"]), dmap.get(p["codex_id"])
        if cs is not None and p.get("origin") in ("bootstrap_same_id", "created_claude"):
            cs["imported"] = True
        paired_c.add(p["claude_id"]); paired_d.add(p["codex_id"])
        st = pair_status(p, cs, cd)
        pairs.append({"pair_id": pid, "status": st, "claude": cs, "codex": cd,
                      "synced_claude_turns": p.get("synced_claude_turns", 0), "synced_codex_turns": p.get("synced_codex_turns", 0),
                      "last_sync": p.get("last_sync"), "origin": p.get("origin"),
                      "claude_active": p["claude_id"] in active_c, "codex_active": p["codex_id"] in active_d,
                      "title": (cs or cd or {}).get("title", ""), "cwd": (cs or cd or {}).get("cwd", "")})
    ign_c, ign_d = set(state["ignored"].get("claude", [])), set(state["ignored"].get("codex", []))
    unpaired = {"claude": [s for s in claude if s["id"] not in paired_c and s["id"] not in ign_c],
                "codex": [s for s in codex if s["id"] not in paired_d and s["id"] not in ign_d and not s.get("archived")]}
    for s in unpaired["claude"]:
        s["active"] = s["id"] in active_c
    for s in unpaired["codex"]:
        s["active"] = s["id"] in active_d
    pairs.sort(key=lambda p: max(ms_from_iso((p["claude"] or {}).get("last_activity")), ms_from_iso((p["codex"] or {}).get("last_activity"))), reverse=True)
    counts = collections.Counter(p["status"] for p in pairs)
    return {"generated_at": now_iso(), "pairs": pairs, "unpaired": unpaired, "counts": dict(counts),
            "totals": {"claude": len(claude), "codex": len(codex), "pairs": len(pairs)}}


def bootstrap(state, claude, codex):
    cmap = {s["id"]: s for s in claude}
    dmap = {s["id"]: s for s in codex}
    added = 0
    paired_c = {p["claude_id"] for p in state["pairs"].values()}
    paired_d = {p["codex_id"] for p in state["pairs"].values()}
    # 1. same id on both sides (sessions imported by codex2claude, or created by this engine)
    for sid in set(cmap) & set(dmap):
        if sid in paired_c or sid in paired_d:
            continue
        state["pairs"][sid] = {"claude_id": sid, "codex_id": sid, "synced_claude_turns": cmap[sid]["turns"],
                               "synced_codex_turns": dmap[sid]["turns"], "last_sync": now_iso(), "origin": "bootstrap_same_id"}
        added += 1
    # 2. Codex's own record of Claude sessions it imported
    recs = load_json(os.path.join(CODEX_DIR, "external_agent_session_imports.json"), {}).get("records", [])
    for r in recs:
        cid = os.path.basename(r.get("source_path", ""))[:-6]
        did = r.get("imported_thread_id")
        if cid in cmap and did in dmap and cid not in paired_c and did not in paired_d:
            n = dmap[did]["turns"]
            state["pairs"][cid] = {"claude_id": cid, "codex_id": did, "synced_claude_turns": min(n, cmap[cid]["turns"]),
                                   "synced_codex_turns": n, "last_sync": now_iso(), "origin": "bootstrap_codex_import"}
            paired_c.add(cid); paired_d.add(did)
            added += 1
    return added


def do_sync(state, pair_id, claude, codex, prefer="both", force=False):
    p = state["pairs"].get(pair_id)
    if not p:
        raise RuntimeError("unknown pair " + pair_id)
    cs = next((s for s in claude if s["id"] == p["claude_id"]), None)
    cd = next((s for s in codex if s["id"] == p["codex_id"]), None)
    st = pair_status(p, cs, cd)
    result = {"pair_id": pair_id, "before": st, "pushed_to_codex": 0, "pushed_to_claude": 0}
    if st == "missing":
        raise RuntimeError("one side is missing")
    if st == "rebased":
        p["synced_claude_turns"] = min(p["synced_claude_turns"], cs["turns"])
        p["synced_codex_turns"] = min(p["synced_codex_turns"], cd["turns"])
        st = pair_status(p, cs, cd)
        result["before"] = st
    if st == "in_sync":
        result["after"] = "in_sync"
        return result
    if st == "conflict" and prefer == "ask":
        raise RuntimeError("conflict: both sides have new turns; choose --prefer claude|codex|both")
    push_c2d = st == "claude_newer" or (st == "conflict" and prefer in ("claude", "both"))
    push_d2c = st == "codex_newer" or (st == "conflict" and prefer in ("codex", "both"))
    active_c, active_d = claude_active_ids(), codex_active_ids()
    if push_c2d:
        if not force and (cd["id"] in active_d or recently_written(cd["path"])):
            raise RuntimeError("Codex thread is open or was just written; try again later")
        turns = claude_turns(cs["path"])[p["synced_claude_turns"]:]
        if turns:
            append_to_codex(cd, turns)
            result["pushed_to_codex"] = len(turns)
    if push_d2c:
        if not force and (cs["id"] in active_c or recently_written(cs["path"])):
            raise RuntimeError("Claude session is open or was just written; try again later")
        turns = codex_turns(cd["id"])[p["synced_codex_turns"]:]
        if turns:
            append_to_claude(cs, turns)
            result["pushed_to_claude"] = len(turns)
    # re-count both sides after writing
    p["synced_claude_turns"] = parse_claude_file(cs["path"])["turns"]
    p["synced_codex_turns"] = sum(parse_codex_segment(f)["turns"] for f in codex_segments(cd["id"]))
    p["last_sync"] = now_iso()
    result["after"] = "in_sync"
    return result


# ----------------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "bootstrap", "sync", "sync-all", "create", "link", "unlink", "ignore", "open",
                                    "register", "register-all", "retitle"])
    ap.add_argument("--pair"); ap.add_argument("--prefer", default="ask", choices=["ask", "claude", "codex", "both"])
    ap.add_argument("--from", dest="src", choices=["claude", "codex"]); ap.add_argument("--id")
    ap.add_argument("--claude"); ap.add_argument("--codex"); ap.add_argument("--side", choices=["claude", "codex"])
    ap.add_argument("--force", action="store_true"); ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--all", action="store_true", help="register-all: include sessions that did not come from Codex")
    a = ap.parse_args()

    cache = Cache()
    state = load_state()
    out = {}
    try:
        claude = scan_claude(cache)
        codex = scan_codex(cache)
        if a.cmd == "scan":
            out = build_view(state, claude, codex)
        elif a.cmd == "bootstrap":
            n = bootstrap(state, claude, codex)
            save_json(STATE_FILE, state)
            out = {"added_pairs": n, "view": build_view(state, claude, codex)}
        elif a.cmd == "sync":
            out = do_sync(state, a.pair, claude, codex, a.prefer, a.force)
            save_json(STATE_FILE, state)
        elif a.cmd == "sync-all":
            results = []
            for pid in list(state["pairs"]):
                try:
                    r = do_sync(state, pid, claude, codex, "ask", a.force)
                    if r.get("pushed_to_codex") or r.get("pushed_to_claude"):
                        results.append(r)
                except Exception as e:
                    results.append({"pair_id": pid, "error": str(e)})
            save_json(STATE_FILE, state)
            out = {"results": results}
        elif a.cmd == "create":
            if a.src == "claude":
                s = next((x for x in claude if x["id"] == a.id), None)
                if not s:
                    raise RuntimeError("Claude session not found")
                out = {"created": create_codex_from_claude(s, state), "side": "codex"}
            else:
                s = next((x for x in codex if x["id"] == a.id), None)
                if not s:
                    raise RuntimeError("Codex session not found")
                out = {"created": create_claude_from_codex(s, state), "side": "claude"}
            save_json(STATE_FILE, state)
        elif a.cmd == "link":
            cs = next((x for x in claude if x["id"] == a.claude), None)
            cd = next((x for x in codex if x["id"] == a.codex), None)
            if not cs or not cd:
                raise RuntimeError("session not found")
            n = min(cs["turns"], cd["turns"])
            state["pairs"][a.claude] = {"claude_id": a.claude, "codex_id": a.codex, "synced_claude_turns": n,
                                        "synced_codex_turns": n, "last_sync": now_iso(), "origin": "manual"}
            save_json(STATE_FILE, state)
            out = {"linked": a.claude}
        elif a.cmd == "unlink":
            state["pairs"].pop(a.pair, None)
            save_json(STATE_FILE, state)
            out = {"unlinked": a.pair}
        elif a.cmd == "ignore":
            lst = state["ignored"].setdefault(a.side, [])
            if a.id not in lst:
                lst.append(a.id)
            save_json(STATE_FILE, state)
            out = {"ignored": a.id}
        elif a.cmd == "register":
            s = next((x for x in claude if x["id"] == a.id), None)
            if not s:
                raise RuntimeError("Claude session not found")
            lid = register_claude_desktop(s)
            out = {"registered": lid, "already": lid is None}
        elif a.cmd == "register-all":
            # Default: only sessions that came from Codex (imported or created by this engine); --all = every CLI session.
            synced_ids = {p["claude_id"] for p in state["pairs"].values() if p.get("origin") in ("bootstrap_same_id", "created_claude")}
            done = []
            for s in claude:
                if s.get("in_desktop"):
                    continue
                if not a.all and not (s.get("imported") or s["id"] in synced_ids):
                    continue
                lid = register_claude_desktop(s)
                if lid:
                    done.append(s["id"])
            out = {"registered": len(done), "ids": done}
        elif a.cmd == "retitle":
            ch = retitle_claude_desktop(claude, state)
            out = {"retitled": len(ch), "items": [{"id": i, "title": t} for i, t in ch]}
        elif a.cmd == "open":
            s = next((x for x in (claude if a.side == "claude" else codex) if x["id"] == a.id), None)
            if not s:
                raise RuntimeError("session not found")
            cmd = 'cd "%s" && claude --resume %s' % (s["cwd"], a.id) if a.side == "claude" else 'cd "%s" && codex resume %s' % (s["cwd"], a.id)
            out = {"command": cmd, "cwd": s["cwd"]}
    except Exception as e:
        out = {"error": str(e)}
        cache.save()
        print(json.dumps(out, ensure_ascii=False))
        sys.exit(1)
    cache.save()
    print(json.dumps(out, ensure_ascii=False, indent=(1 if a.pretty else None)))


if __name__ == "__main__":
    main()
