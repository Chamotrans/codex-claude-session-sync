#!/usr/bin/env python3
"""Convert OpenAI Codex rollout JSONL sessions into Claude Code session JSONL.

Output goes to ~/.claude/projects/<cwd-slug>/<codex-session-id>.jsonl so the
sessions show up in `claude --resume` / the /resume picker for that project.

Usage:
  codex2claude.py [--dry-run] [--force] [--include-subagents] [--limit N] [--only ID]
"""
import argparse, glob, json, os, re, sys, uuid, collections

HOME = os.path.expanduser("~")
CODEX = os.path.join(HOME, ".codex")
OUT_ROOT = os.path.join(HOME, ".claude", "projects")
CLAUDE_VERSION = "2.1.282"

# Codex injects these as fake "user" messages; they are not human prompts.
INJECTED_PREFIXES = (
    "<environment_context>", "<permissions instructions>", "# AGENTS.md instructions",
    "<recommended_plugins>", "<codex_internal_context", "<user_instructions>",
    "<INSTRUCTIONS>", "<skills", "<app_context", "<turn_aborted", "<system_reminder",
    "<collaboration_mode", "<realtime", "<plugin",
)
MAX_TOOL_RESULT_CHARS = 60_000
MAX_IMAGE_B64 = 1_500_000  # larger images become a placeholder
COMPACT_ABOVE_TOKENS = 80_000  # sessions estimated above this get a compact boundary + synthetic summary
JSONL_FULL_LIMIT = 15_000_000  # above this, keep only summary + tail in the Claude session file
TAIL_BYTES = 250_000
TRANSCRIPTS_DIR = os.path.join(HOME, ".claude", "codex-transcripts")


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def load_excluded_thread_ids():
    p = os.path.join(CODEX, "external_agent_session_imports.json")
    try:
        recs = json.load(open(p)).get("records", [])
        # only skip when the original Claude session file still exists
        return {r["imported_thread_id"] for r in recs
                if r.get("imported_thread_id") and os.path.exists(r.get("source_path", ""))}
    except Exception:
        return set()


def text_of(content):
    """Codex content: str | list[{type:input_text|output_text, text}] -> str"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    out = []
    for c in content:
        if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text"):
            out.append(c.get("text", ""))
        elif isinstance(c, str):
            out.append(c)
    return "".join(out)


def user_blocks(content):
    """Return Claude content (str or list of blocks) for a Codex user message, or None if injected."""
    if isinstance(content, str):
        t = content
        if t.lstrip().startswith(INJECTED_PREFIXES) or not t.strip():
            return None
        return t
    blocks, texts = [], []
    for c in content or []:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("input_text", "text"):
            t = c.get("text", "")
            if t.lstrip().startswith(INJECTED_PREFIXES):
                continue
            texts.append(t)
        elif c.get("type") == "input_image":
            url = c.get("image_url", "")
            m = re.match(r"data:(image/[a-zA-Z0-9.+-]+);base64,(.*)$", url, re.S)
            if m and len(m.group(2)) <= MAX_IMAGE_B64:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}})
            else:
                texts.append("\n[image omitted on import from Codex]\n")
    text = "".join(texts)
    if not text.strip() and not blocks:
        return None
    if not blocks:
        return text
    if text.strip():
        blocks.insert(0, {"type": "text", "text": text})
    return blocks


def parse_args_json(s):
    if isinstance(s, dict):
        return s
    try:
        v = json.loads(s) if isinstance(s, str) else {}
        return v if isinstance(v, dict) else {"value": v}
    except Exception:
        return {"raw": s}


def tool_name(p):
    ns = p.get("namespace")
    name = p.get("name") or p.get("type")
    if ns:
        ns = ns.rstrip("_")
        return f"{ns}__{name}" if ns.startswith("mcp__") else f"mcp__{ns}__{name}"
    return name


def result_text(output):
    if isinstance(output, list):
        t = text_of(output)
    elif isinstance(output, dict):
        t = json.dumps(output, ensure_ascii=False)
    else:
        t = "" if output is None else str(output)
    if len(t) > MAX_TOOL_RESULT_CHARS:
        t = t[:MAX_TOOL_RESULT_CHARS] + f"\n\n[... truncated {len(t)-MAX_TOOL_RESULT_CHARS} chars on import from Codex]"
    return t


class Converter:
    def __init__(self, meta):
        self.meta = meta
        self.session_id = meta["session_id"]
        self.cwd = meta.get("cwd") or HOME
        self.git_branch = ((meta.get("git") or {}).get("branch")) or ""
        self.lines = []
        self.parent = None
        self.first_prompt = None
        self.last_prompt = None
        self.pending_tool_ids = []   # tool_use ids awaiting a tool_result
        self.seen_tool_ids = set()
        self.human_turns = 0
        self.compacted = False
        self.truncated = False

    def base(self, ts):
        return {"parentUuid": self.parent, "isSidechain": False, "userType": "external",
                "entrypoint": "cli", "cwd": self.cwd, "sessionId": self.session_id,
                "version": CLAUDE_VERSION, "gitBranch": self.git_branch, "timestamp": ts}

    def emit(self, rec):
        rec["uuid"] = str(uuid.uuid4())
        self.lines.append(rec)
        self.parent = rec["uuid"]
        return rec["uuid"]

    def close_pending(self, ts):
        """Anthropic API needs a tool_result for every tool_use; synthesize missing ones."""
        for tid in self.pending_tool_ids:
            self.emit_tool_result(tid, "[no tool output recorded in Codex session]", ts)
        self.pending_tool_ids = []

    def emit_user_text(self, content, ts):
        self.close_pending(ts)
        rec = self.base(ts)
        rec.update({"type": "user", "promptId": str(uuid.uuid4()),
                    "message": {"role": "user", "content": content},
                    "origin": {"kind": "human"}, "permissionMode": "default"})
        self.emit(rec)
        text = content if isinstance(content, str) else text_of([b for b in content if b.get("type") == "text"]) or "[image]"
        if self.first_prompt is None:
            self.first_prompt = text
        self.last_prompt = text
        self.human_turns += 1

    def emit_assistant_block(self, block, ts, msg_id, idx):
        rec = self.base(ts)
        rec.update({"type": "assistant", "apiBlockIndex": idx,
                    "message": {"id": msg_id, "type": "message", "role": "assistant",
                                "model": self.meta.get("model") or "codex",
                                "content": [block], "stop_reason": None, "stop_sequence": None,
                                "usage": {"input_tokens": 0, "output_tokens": 0}}})
        self.emit(rec)

    def emit_assistant_text(self, text, ts):
        if not text.strip():
            return
        self.close_pending(ts)
        self.emit_assistant_block({"type": "text", "text": text}, ts, "msg_" + uuid.uuid4().hex[:24], 0)

    def emit_tool_use(self, call_id, name, inp, ts):
        # A new tool_use after an unanswered one is fine within a single assistant turn,
        # but if a *result* has been emitted since, we're in a new turn; ensure ordering.
        call_id = call_id or ("call_" + uuid.uuid4().hex[:20])
        if call_id in self.seen_tool_ids:
            call_id = call_id + "_" + uuid.uuid4().hex[:6]
        self.seen_tool_ids.add(call_id)
        self.emit_assistant_block({"type": "tool_use", "id": call_id, "name": name, "input": inp},
                                  ts, "msg_" + uuid.uuid4().hex[:24], 0)
        self.pending_tool_ids.append(call_id)
        return call_id

    def emit_tool_result(self, call_id, text, ts, is_error=False):
        rec = self.base(ts)
        rec.update({"type": "user",
                    "message": {"role": "user", "content": [{"tool_use_id": call_id, "type": "tool_result",
                                                              "content": text, "is_error": is_error}]}})
        self.emit(rec)

    def handle_tool_output(self, call_id, text, ts):
        if call_id in self.pending_tool_ids:
            self.pending_tool_ids.remove(call_id)
            self.emit_tool_result(call_id, text, ts)
        # orphan outputs (no matching tool_use) are dropped

    def feed(self, p, ts):
        t = p.get("type")
        if t == "message":
            role = p.get("role")
            if role == "user":
                c = user_blocks(p.get("content"))
                if c is not None:
                    self.emit_user_text(c, ts)
            elif role == "assistant":
                self.emit_assistant_text(text_of(p.get("content")), ts)
            # developer/system messages are Codex-internal: skipped
        elif t == "function_call":
            self.emit_tool_use(p.get("call_id"), tool_name(p), parse_args_json(p.get("arguments")), ts)
        elif t == "custom_tool_call":
            name = p.get("name") or "custom_tool"
            inp = {"patch": p.get("input")} if name == "apply_patch" else {"input": p.get("input")}
            self.emit_tool_use(p.get("call_id"), name, inp, ts)
        elif t in ("function_call_output", "custom_tool_call_output"):
            self.handle_tool_output(p.get("call_id"), result_text(p.get("output")), ts)
        elif t == "tool_search_call":
            self.emit_tool_use(p.get("call_id"), "ToolSearch", parse_args_json(p.get("arguments")), ts)
        elif t == "tool_search_output":
            tools = p.get("tools") or []
            names = [x.get("name") for x in tools if isinstance(x, dict)]
            self.handle_tool_output(p.get("call_id"), "Found tools: " + json.dumps(names, ensure_ascii=False), ts)
        elif t == "web_search_call":
            a = p.get("action") or {}
            q = a.get("queries") or ([a["query"]] if a.get("query") else [])
            if q:
                self.emit_assistant_text("[web search: " + "; ".join(q) + "]", ts)
        elif t == "agent_message":
            body = text_of(p.get("content"))
            if body.strip():
                self.emit_user_text(f"[agent message {p.get('author')} → {p.get('recipient')}]\n{body}", ts)
        # reasoning (encrypted), image_generation_call, etc.: skipped

    def estimate_tokens(self):
        chars = 0
        for r in self.lines:
            m = r.get("message")
            if m:
                chars += len(json.dumps(m.get("content"), ensure_ascii=False))
        return chars // 3   # conservative for mixed CJK/English

    def build_summary(self):
        prompts, recent = [], []
        for r in self.lines:
            m = r.get("message") or {}
            c = m.get("content")
            if r.get("type") == "user" and r.get("origin", {}).get("kind") == "human":
                t = c if isinstance(c, str) else text_of([b for b in c if b.get("type") == "text"]) or "[image]"
                prompts.append(t.strip())
                recent.append(("User", t.strip()))
            elif r.get("type") == "assistant" and isinstance(c, list) and c and c[0].get("type") == "text":
                recent.append(("Assistant", c[0]["text"].strip()))
        head = "This session was imported from OpenAI Codex. The full original transcript is kept above the compaction boundary for reading, but it is too long to send to the model, so this summary stands in for it.\n\n"
        head += "## All user requests, in order\n"
        for i, p in enumerate(prompts, 1):
            head += f"{i}. {p[:300].replace(chr(10), ' ')}\n"
        tail_budget = 60_000
        tail, used = [], 0
        for who, t in reversed(recent):
            t = t[:6000]
            if used + len(t) > tail_budget:
                break
            tail.append(f"**{who}:** {t}")
            used += len(t)
        head += "\n## Most recent exchanges (verbatim, oldest first)\n\n" + "\n\n".join(reversed(tail))
        return head

    def transcript_path(self):
        return os.path.join(TRANSCRIPTS_DIR, self.session_id + ".md")

    def write_markdown(self):
        os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
        out = [f"# Codex session {self.session_id}", f"- cwd: {self.cwd}", f"- imported: full transcript export\n"]
        for r in self.lines:
            m = r.get("message") or {}
            c = m.get("content")
            ts = (r.get("timestamp") or "")[:19]
            if r.get("type") == "user" and r.get("origin", {}).get("kind") == "human":
                t = c if isinstance(c, str) else text_of([b for b in c if b.get("type") == "text"]) or "[image]"
                out.append(f"\n## User ({ts})\n\n{t}\n")
            elif r.get("type") == "user" and isinstance(c, list) and c and c[0].get("type") == "tool_result":
                t = c[0].get("content") or ""
                out.append(f"<details><summary>tool result</summary>\n\n```\n{t[:20000]}\n```\n</details>\n")
            elif r.get("type") == "assistant" and isinstance(c, list) and c:
                b = c[0]
                if b.get("type") == "text":
                    out.append(f"\n**Assistant ({ts}):**\n\n{b['text']}\n")
                elif b.get("type") == "tool_use":
                    inp = json.dumps(b.get("input"), ensure_ascii=False)
                    out.append(f"`{b.get('name')}` {inp[:3000]}\n")
        with open(self.transcript_path(), "w") as w:
            w.write("\n".join(out))

    def truncate_to_tail(self):
        """Keep only records after a human-turn boundary so that the tail is <= TAIL_BYTES."""
        sizes = [len(json.dumps(r, ensure_ascii=False)) + 1 for r in self.lines]
        total, cut, fallback = 0, None, None
        for i in range(len(self.lines) - 1, -1, -1):
            total += sizes[i]
            if total > TAIL_BYTES:
                break
            r = self.lines[i]
            if r.get("type") == "user" and r.get("origin", {}).get("kind") == "human":
                cut = i
            elif r.get("type") == "assistant":
                fallback = i   # cutting before an assistant record keeps tool_use/tool_result pairs intact
        if cut is None:
            cut = fallback if fallback is not None else max(0, len(self.lines) - 1)
        self.lines = self.lines[cut:]
        self.parent = None
        for r in self.lines:              # re-chain parent uuids
            r["parentUuid"] = self.parent
            self.parent = r["uuid"]

    def append_compact_boundary(self, ts, truncated=False):
        pre = self.estimate_tokens()
        summary = self.build_summary()
        body = ("This session is being continued from a previous conversation that ran out of context. "
                "The summary below covers the earlier portion of the conversation.\n\n" + summary +
                f"\n\nIf you need specific details from before compaction (like exact code snippets, error messages, "
                f"or content you generated), read the full transcript at: {self.transcript_path()}" +
                ("\n\nRecent messages are preserved verbatim." if truncated else "") +
                "\n\nContinue the conversation from where it left off without asking the user any further questions. "
                "Resume directly \u2014 do not acknowledge the summary, do not recap what was done.")
        b = self.base(ts)
        b.update({"parentUuid": None, "logicalParentUuid": self.parent, "type": "system",
                  "subtype": "compact_boundary", "content": "Conversation compacted", "isMeta": False,
                  "level": "info", "compactMetadata": {"trigger": "manual", "preTokens": pre,
                                                       "postTokens": len(body) // 3, "durationMs": 0}})
        self.emit(b)
        s = self.base(ts)
        s.update({"type": "user", "message": {"role": "user", "content": body},
                  "isCompactSummary": True, "isVisibleInTranscriptOnly": True})
        self.emit(s)

    def finish(self, ts):
        self.close_pending(ts)
        if self.human_turns == 0:
            return None
        title = ("[Codex] " + (self.first_prompt or "").strip().split("\n")[0])[:80]
        if self.estimate_tokens() > COMPACT_ABOVE_TOKENS:
            self.write_markdown()
            est_bytes = sum(len(json.dumps(r, ensure_ascii=False)) + 1 for r in self.lines)
            if est_bytes > JSONL_FULL_LIMIT:
                # boundary + summary go FIRST, then the verbatim tail (Claude sends everything after the boundary)
                full = list(self.lines)
                self.truncate_to_tail()
                tail = self.lines
                self.lines = list(full)     # summary is built from the full history
                self.parent = None
                self.append_compact_boundary(ts, truncated=True)
                boundary = self.lines[len(full):]
                self.lines = boundary + tail
                for r in tail:              # chain tail onto the summary record
                    r["parentUuid"] = self.parent
                    self.parent = r["uuid"]
                self.truncated = True
            else:
                self.append_compact_boundary(ts)
            self.compacted = True
        leaf = self.parent
        self.lines.append({"type": "custom-title", "customTitle": title, "sessionId": self.session_id})
        self.lines.append({"type": "last-prompt", "lastPrompt": (self.last_prompt or "")[:500],
                           "leafUuid": leaf, "sessionId": self.session_id})
        return self.lines


def convert_files(paths):
    """Merge several rollout segments (same session_id, chronological) into one session."""
    conv = None
    last_ts = None
    for path in paths:
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = o.get("timestamp") or last_ts
                last_ts = ts
                t = o.get("type")
                p = o.get("payload") or {}
                if t == "session_meta":
                    if conv is None:
                        if not p.get("session_id"):
                            return None
                        conv = Converter(p)
                    else:
                        br = (p.get("git") or {}).get("branch")
                        if br:
                            conv.git_branch = br
                elif t == "turn_context" and conv is not None:
                    if p.get("model"):
                        conv.meta["model"] = p["model"]
                elif t == "response_item" and conv is not None:
                    conv.feed(p, ts)
    if conv is None:
        return None
    return conv.finish(last_ts)


def first_prompt_and_count(path):
    """(first human prompt[:200], number of human prompts) for segment de-duplication."""
    first, n = None, 0
    with open(path, errors="replace") as fh:
        for line in fh:
            if '"role":"user"' not in line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            p = o.get("payload") or {}
            if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                c = user_blocks(p.get("content"))
                if c is None:
                    continue
                n += 1
                if first is None:
                    first = (c if isinstance(c, str) else text_of([b for b in c if b.get("type") == "text"]))[:200]
    return first, n


def dedupe_segments(segs):
    """segs: list of (ts, path). Keep, per identical first prompt, the segment with most turns."""
    best = {}
    order = []
    for ts, path in sorted(segs):
        key, n = first_prompt_and_count(path)
        if key is None:
            continue
        if key not in best:
            order.append(key)
            best[key] = (n, ts, path)
        elif (n, ts) >= best[key][:2]:
            best[key] = (n, ts, path)
    return [best[k][2] for k in order]


def is_subagent(meta):
    if meta.get("thread_source") == "subagent":
        return True
    return isinstance(meta.get("source"), dict) and "subagent" in meta["source"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite existing imported files")
    ap.add_argument("--include-subagents", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", help="only this codex session id")
    ap.add_argument("--out-root", default=OUT_ROOT)
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(CODEX, "sessions", "**", "*.jsonl"), recursive=True)
                   + glob.glob(os.path.join(CODEX, "archived_sessions", "*.jsonl")))
    excluded = load_excluded_thread_ids()
    stats = collections.Counter()
    written = []
    groups = collections.OrderedDict()   # sid -> {"meta":..., "segs":[(ts,path)]}
    for f in files:
        try:
            with open(f, errors="replace") as fh:
                meta = (json.loads(fh.readline()) or {}).get("payload") or {}
        except Exception:
            stats["bad_meta"] += 1
            continue
        sid = meta.get("session_id")
        if not sid:
            stats["bad_meta"] += 1
            continue
        if a.only and a.only != sid:
            continue
        if is_subagent(meta) and not a.include_subagents:
            stats["skipped_subagent_files"] += 1
            continue
        if sid in excluded:
            stats["skipped_claude_origin_files"] += 1
            continue
        g = groups.setdefault(sid, {"meta": meta, "segs": []})
        g["segs"].append((meta.get("timestamp") or "", f))
    for sid, g in groups.items():
        meta = g["meta"]
        out_dir = os.path.join(a.out_root, slug(meta.get("cwd") or HOME))
        out_path = os.path.join(out_dir, sid + ".jsonl")
        if os.path.exists(out_path) and not a.force:
            stats["skipped_exists"] += 1
            continue
        segs = dedupe_segments(g["segs"])
        if not segs:
            stats["skipped_empty"] += 1
            continue
        stats["segments_merged"] += len(segs)
        lines = convert_files(segs)
        if not lines:
            stats["skipped_empty"] += 1
            continue
        stats["converted"] += 1
        if any(r.get("subtype") == "compact_boundary" for r in lines):
            stats["with_compact_boundary"] += 1
        if any(r.get("isCompactSummary") and "Recent messages are preserved verbatim" in r["message"]["content"] for r in lines):
            stats["truncated_to_tail"] += 1
        est = sum(len(json.dumps(r, ensure_ascii=False)) + 1 for r in lines)
        stats["out_bytes_total"] += est
        written.append((out_path, est))
        if not a.dry_run:
            os.makedirs(out_dir, exist_ok=True)
            tmp = out_path + ".tmp"
            with open(tmp, "w") as w:
                for rec in lines:
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, out_path)
        if a.limit and stats["converted"] >= a.limit:
            break
    print(json.dumps(stats, indent=2))
    dirs = collections.Counter(os.path.basename(os.path.dirname(p)) for p, _ in written)
    for d, n in dirs.most_common():
        print(f"{n:4d}  {d}")
    big = sorted(written, key=lambda x: -x[1])[:8]
    print("largest outputs (MB):")
    for p, b in big:
        print(f"  {b/1e6:8.1f}  {os.path.basename(p)}")
    if a.dry_run:
        print("(dry run: nothing written)")


if __name__ == "__main__":
    main()
