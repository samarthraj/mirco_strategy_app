"""Sanity check: normalize real SQL samples for embedding input.

Usage: python scratch_normalize_sanity.py
"""
from __future__ import annotations
import re
import sqlite3
import sys
from pathlib import Path


# ---- Normalizer ---------------------------------------------------------

_LITERAL_STR = re.compile(r"'(?:''|[^'])*'")
_LITERAL_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# MSTR session-specific temp table suffixes.
# Observed patterns: ZZEA00, ZZMD00, ZZSP00 (short-form), Zzoq00qq00_10FE25_* (long),
# DSS_INT_*, ZZMD<hex>, *_TEMP_*.
_TMP_NAME = re.compile(
    r"\b(?:"
    r"zz[a-z]{2}\d+(?:_\w+)?"            # zzea00, zzmd00, zzoq00qq00_10fe25...
    r"|dss_int\w*"
    r"|tmp_\w+|temp_\w+"
    r"|#?[a-z]*_temp_[a-z0-9_]+"
    r")\b",
    re.IGNORECASE,
)
# Hash-like random suffix: 32+ hex chars or random alnum tokens
_HASH_TOKEN = re.compile(r"\b[a-f0-9]{16,}\b", re.IGNORECASE)
_WS = re.compile(r"\s+")


def normalize_sql_for_embed(sql: str, max_chars: int = 2000) -> str:
    if not sql:
        return ""
    s = sql
    # 1. Strip block comments first (they rarely contain string literals)
    s = _BLOCK_COMMENT.sub(" ", s)
    # 2. Mask string literals BEFORE line comments so that '--' inside a
    #    string doesn't get mis-parsed as a SQL line comment (eating
    #    everything to end-of-line).
    s = _LITERAL_STR.sub("?", s)
    # 3. Now safe to strip line comments
    s = _LINE_COMMENT.sub(" ", s)
    # 4. Lowercase for consistency
    s = s.lower()
    # 5. Mask session temp table names + hash-like random suffixes
    s = _TMP_NAME.sub("tmp_tbl", s)
    s = _HASH_TOKEN.sub("hashtok", s)
    # 6. Mask numeric literals
    s = _LITERAL_NUM.sub("?", s)
    # 7. Collapse whitespace
    s = _WS.sub(" ", s).strip()
    # 8. Size cap
    if len(s) > max_chars:
        s = s[:max_chars]
    return s


# ---- Run on real samples ------------------------------------------------

def main() -> int:
    db = Path("d:/dev/mstr_api/data.db")
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row

    # Pick 5 representative samples: small, medium, comment-heavy, temp-heavy, complex
    # Fixed IDs from earlier survey so runs are reproducible.
    target_ids = [
        ("insight", "3C8EA15462DD60A69DDF6B88DD07F3BD"),              # small ~1700 chars
        ("insight", "305181AAF87DAF8FF0D72CA7F5FD1EB9"),              # comments + DDL
        ("insight", "71BA4B1CB9A3F26D1DE9FAB82DA8C71A"),              # temp-heavy 8.8k
        ("global-operational", "9472FCF9DDA64CE69D47A2411DCCE5BA"),   # DDL 4.3k
        ("insight", "D2205871A3BC3BD41DD3A4FF5F38FD72"),              # clean 4k
    ]
    # Fallback: if a fixed ID isn't in the DB (IDs above were 8-char prefixes),
    # use RANDOM() to pick 5 fresh samples.
    samples = []
    for pid, rid_prefix in target_ids:
        row = c.execute(
            "SELECT project_id, report_id, sql_text FROM report_sql WHERE project_id=? AND report_id LIKE ? AND sql_text != '' LIMIT 1",
            (pid, rid_prefix[:8] + "%"),
        ).fetchone()
        if row:
            samples.append(row)
    if len(samples) < 5:
        needed = 5 - len(samples)
        for row in c.execute(
            "SELECT project_id, report_id, sql_text FROM report_sql WHERE sql_text != '' ORDER BY RANDOM() LIMIT ?",
            (needed,),
        ):
            samples.append(row)

    out_lines = []
    for i, row in enumerate(samples, 1):
        raw = row["sql_text"]
        name_row = c.execute(
            "SELECT name FROM report WHERE project_id=? AND report_id=?",
            (row["project_id"], row["report_id"]),
        ).fetchone()
        name = name_row[0] if name_row else "(unknown)"
        norm = normalize_sql_for_embed(raw)
        out_lines.append(f"=== Sample {i}: {name[:60]} ({row['project_id']}/{row['report_id'][:12]}) ===")
        out_lines.append(f"[raw len]       {len(raw):>6}  [normalized len] {len(norm):>6}  "
                         f"(reduction: {100*(1-len(norm)/max(1,len(raw))):.0f}%)")
        out_lines.append("--- RAW (first 700 chars) ---")
        out_lines.append(raw[:700])
        out_lines.append("--- NORMALIZED (first 700 chars) ---")
        out_lines.append(norm[:700])
        out_lines.append("")

    sys.stdout.reconfigure(encoding="utf-8")
    print("\n".join(out_lines))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
