"""Export a Claude Code session transcript (JSONL) to a readable Markdown log.

The brief requires the *raw* conversation, so user messages and assistant text are kept verbatim;
only tool calls are condensed to one line each (name + key argument) and tool results to their size.
Anything that looks like an API key is redacted.

    uv run python tools/export_conversation.py <session.jsonl> conversation/transcript.md
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

SECRET = re.compile(r"(AIza[0-9A-Za-z_\-]{30,}|sk-ant-[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z]{32,})")
# Harness-injected context inside user turns (not typed by the user): keep a marker, drop the body.
INJECTED = re.compile(r"<(system-reminder|ide_opened_file|ide_selection|command-message|command-name|skill-format)>.*?</\1>", re.S)


def redact(text: str) -> str:
    return SECRET.sub("[REDACTED-KEY]", text)


def tool_line(block: dict[str, Any]) -> str:
    name, args = block.get("name", "?"), block.get("input") or {}
    key = next((args[k] for k in ("description", "file_path", "pattern", "url", "command", "prompt", "query") if k in args), "")
    key = " ".join(str(key).split())
    return f"- 🔧 **{name}**: {key[:160]}{'…' if len(key) > 160 else ''}"


def _texts(blocks: str | list[Any]) -> list[str]:
    """Text of a message, minus harness-injected context. Accepts a plain string or content blocks."""
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    texts = [INJECTED.sub("", b.get("text", "")).strip() for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return [t for t in texts if t]


def _user_header(ts: str, label: str = "User") -> list[str]:
    return ["---", "", f"## 👤 {label} ({ts[:16].replace('T', ' ')} UTC)", ""]


def render(entries: list[dict[str, Any]]) -> str:
    out = ["# AI conversation log", "",
           "Exported verbatim from the Claude Code session that built this project (`tools/export_conversation.py`).",
           "User messages, the user's answers to multiple-choice questions, and assistant text are unedited.",
           "Tool calls are condensed to one line; tool outputs to their size; harness-injected context to a one-line marker.", ""]  # fmt: skip
    ask_ids: set[str] = set()
    for e in entries:
        kind = e.get("type")
        if e.get("isSidechain") or kind not in ("user", "assistant", "attachment"):
            continue  # api-request-blob duplicates, queue bookkeeping, subagent internals
        ts = e.get("timestamp", "")
        if kind == "attachment":
            att = e.get("attachment") or {}
            if att.get("type") == "queued_command":  # queued while the assistant was working
                texts = _texts(att.get("prompt") or [])
                if texts and texts[0].startswith("<task-notification>"):  # harness event, not the user
                    summary = re.search(r"<summary>(.*?)</summary>", texts[0], re.S)
                    out.append(f"  - 🔔 background task finished: {summary.group(1).strip() if summary else ''}")
                elif texts:
                    out += _user_header(ts, "User (sent mid-turn)") + [redact(t) + "\n" for t in texts]
            continue
        msg = e.get("message") or {}
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        if msg.get("role") == "user":
            origin = (e.get("origin") or {}).get("kind")
            texts = _texts(blocks)
            if e.get("isMeta"):
                if texts:
                    out.append(f"  - ⚙️ harness loaded: {texts[0].splitlines()[0][:100]}")
            elif origin == "task-notification":
                summary = re.search(r"<summary>(.*?)</summary>", " ".join(b.get("text", "") for b in blocks), re.S)
                out.append(f"  - 🔔 background task finished: {summary.group(1).strip() if summary else ''}")
            elif texts:
                out += _user_header(ts) + [redact(t) + "\n" for t in texts]
            for r in (b for b in blocks if b.get("type") == "tool_result"):
                body = r.get("content")
                body = body if isinstance(body, str) else " ".join(x.get("text", "") for x in body or [] if isinstance(x, dict))
                if r.get("tool_use_id") in ask_ids and body.startswith("[Tool call interrupted"):
                    out.append("  - ❓ (no answer recorded: the session ended; the answers arrive in the next user message)")
                elif r.get("tool_use_id") in ask_ids:
                    out += [*_user_header(ts, "User answered the questions"), redact(body.strip()) + "\n"]
                else:
                    out.append(f"  - ↳ tool result ({len(body):,} chars)")
        else:
            for b in blocks:
                if b.get("type") == "text" and b.get("text", "").strip():
                    out += ["", "### 🤖 Assistant", "", redact(b["text"].strip()), ""]
                elif b.get("type") == "tool_use":
                    if b.get("name") == "AskUserQuestion":
                        ask_ids.add(b.get("id"))
                        qs = (b.get("input") or {}).get("questions") or []
                        out.append("- ❓ **AskUserQuestion**")
                        for q in qs:
                            opts = " / ".join(o.get("label", "") for o in q.get("options", []))
                            out.append(f"  - {q.get('question', '')} — options: {opts}")
                    else:
                        out.append(redact(tool_line(b)))
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("out", type=Path)
    a = ap.parse_args()
    entries = [json.loads(line) for line in a.jsonl.read_text(encoding="utf-8").splitlines() if line.strip().startswith("{")]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(entries), encoding="utf-8")
    print(f"wrote {a.out} ({a.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
