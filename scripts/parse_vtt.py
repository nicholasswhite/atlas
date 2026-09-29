#!/usr/bin/env python3
"""Atlas P2 — WebVTT transcript reducer.

Teams meeting transcripts come as WebVTT: cue ids, timestamps, `<v Speaker>...</v>`
voice spans, and a lot of conversational noise. This script reduces a `.vtt` file to a
clean `Speaker: text` transcript suitable for feature-claim extraction, with optional
windowing to just the segments that mention given feature terms.

This is the deterministic half of the meeting-transcripts harvester (H4). It does NOT
fetch transcripts — supply an authorized local `.vtt` file or a fictional fixture. Output is meant to be wrapped by wrap_untrusted.py before
it enters a dossier:

    python parse_vtt.py --vtt meeting.vtt --terms "Aurora Sync,folder selection" \
      | python wrap_untrusted.py --source "H4:transcripts"

Design choices:
  - Fidelity over editorializing. We merge consecutive same-speaker cues and drop
    structural noise (cue ids, timestamps, tags), but we do NOT rewrite words or strip
    filler by default — claim extraction needs the PM's actual phrasing.
  - Speakerless VTTs (no `<v>` spans) still parse; the speaker is "Unknown".
  - `--terms` windowing keeps each matching turn plus N turns of context on each side,
    so a claim isn't ripped out of the exchange that qualifies it.

CLI:
    parse_vtt.py --vtt FILE [--terms "a,b,c"] [--context N] [--json] [--stats]
    cat FILE.vtt | parse_vtt.py            # read from stdin
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# `<v Speaker Name>text</v>`  (Teams voice span; text may omit the closing tag)
_VOICE_RE = re.compile(r"<v\s+([^>]+?)>(.*?)(?:</v>|$)", re.IGNORECASE | re.DOTALL)
# Any remaining angle-bracket tag (e.g. <c>, <i>, stray spans)
_TAG_RE = re.compile(r"<[^>]+>")
_TIMESTAMP_RE = re.compile(r"-->")
# A VTT timestamp line: 00:00:00.000 --> 00:00:03.000 (optionally with cue settings)
_TS_LINE_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}[.,]\d{3}\s*-->")


def _strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def parse_vtt(raw: str) -> list[dict]:
    """Parse WebVTT text into a list of {speaker, text} turns (consecutive same-speaker
    cues merged)."""
    # Normalize newlines, split into blocks on blank lines.
    blocks = re.split(r"\r?\n\r?\n", raw.replace("\r\n", "\n").replace("\r", "\n"))
    cues: list[tuple[str, str]] = []  # (speaker, text)

    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        # Skip the WEBVTT header block and NOTE/STYLE/REGION blocks.
        if lines[0].strip().upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue

        # Find the timestamp line; text is everything after it.
        ts_idx = next((i for i, ln in enumerate(lines) if _TIMESTAMP_RE.search(ln)), None)
        text_lines = lines[ts_idx + 1:] if ts_idx is not None else lines
        # If there was no timestamp line, this block might be stray header text — but a
        # cue id alone (no timestamp, no text) carries nothing, so guard for empties.
        text_block = "\n".join(text_lines).strip()
        if not text_block:
            continue

        # Extract speaker(s) from voice spans. A cue can contain more than one.
        voice_matches = _VOICE_RE.findall(text_block)
        if voice_matches:
            for speaker, said in voice_matches:
                clean = _strip_tags(said)
                if clean:
                    cues.append((speaker.strip(), clean))
        else:
            clean = _strip_tags(text_block)
            if clean:
                cues.append(("Unknown", clean))

    # Merge consecutive cues by the same speaker into one turn.
    turns: list[dict] = []
    for speaker, text in cues:
        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["text"] = (turns[-1]["text"] + " " + text).strip()
        else:
            turns.append({"speaker": speaker, "text": text})
    return turns


def window_by_terms(turns: list[dict], terms: list[str], context: int) -> list[dict]:
    """Keep only turns that mention any term, plus `context` turns on each side.
    Marks matching turns with `match=True` so callers can highlight them."""
    if not terms:
        return turns
    lowered = [t.lower() for t in terms if t.strip()]
    keep: set[int] = set()
    matched: set[int] = set()
    for i, turn in enumerate(turns):
        hay = turn["text"].lower()
        if any(term in hay for term in lowered):
            matched.add(i)
            for j in range(max(0, i - context), min(len(turns), i + context + 1)):
                keep.add(j)
    out: list[dict] = []
    for i in sorted(keep):
        t = dict(turns[i])
        t["match"] = i in matched
        out.append(t)
    return out


def render_text(turns: list[dict], mark_matches: bool = False) -> str:
    lines: list[str] = []
    for t in turns:
        prefix = ">> " if (mark_matches and t.get("match")) else ""
        lines.append(f"{prefix}{t['speaker']}: {t['text']}")
    return "\n".join(lines)


def stats(turns: list[dict]) -> dict:
    speakers: dict[str, int] = {}
    words = 0
    for t in turns:
        speakers[t["speaker"]] = speakers.get(t["speaker"], 0) + 1
        words += len(t["text"].split())
    return {"turns": len(turns), "speakers": speakers, "words": words}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reduce a WebVTT transcript to clean text.")
    parser.add_argument("--vtt", default=None, help="Path to a .vtt file. If omitted, read stdin.")
    parser.add_argument("--terms", default=None,
                        help="Comma-separated feature terms; keep only matching turns + context.")
    parser.add_argument("--context", type=int, default=2,
                        help="Turns of context to keep on each side of a match (default 2).")
    parser.add_argument("--json", action="store_true", help="Emit JSON turns instead of text.")
    parser.add_argument("--stats", action="store_true", help="Print stats to stderr.")
    args = parser.parse_args(argv)

    if args.vtt:
        raw = Path(args.vtt).read_text(encoding="utf-8-sig")
    else:
        raw = sys.stdin.read()

    turns = parse_vtt(raw)
    terms = [t.strip() for t in args.terms.split(",")] if args.terms else []
    windowed = window_by_terms(turns, terms, args.context) if terms else turns

    if args.stats:
        s = stats(windowed)
        print(
            f"turns={s['turns']} words={s['words']} speakers={len(s['speakers'])} "
            f"({', '.join(f'{k}:{v}' for k, v in s['speakers'].items())})",
            file=sys.stderr,
        )

    if args.json:
        sys.stdout.write(json.dumps(windowed, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render_text(windowed, mark_matches=bool(terms)))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
