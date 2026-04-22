"""AST hash computation for SQL similarity.

Parses SQL using sqlglot (Redshift dialect), walks each statement's AST, and
returns a sorted list of normalized subtree hashes per report. Two reports
whose hash-sets overlap highly are structurally similar.

MSTR SQL is multi-statement DDL (SET query_group / CREATE TEMPORARY TABLE AS
SELECT / DROP TABLE / etc.) that sqlglot can't parse as a single query.
We extract the SELECT / WITH / INSERT INTO ... SELECT blocks via regex and
parse each independently. If NONE of the fragments are parseable, the report
gets no hashes (instead of degenerate Command-node hashes that collapse
every report into one false cluster).
"""
from __future__ import annotations
import hashlib
import re
from typing import Any


try:
    import sqlglot
    from sqlglot.errors import ErrorLevel, ParseError
    _SQLGLOT_AVAILABLE = True
except ImportError:
    sqlglot = None  # type: ignore
    ErrorLevel = None  # type: ignore
    ParseError = Exception  # type: ignore
    _SQLGLOT_AVAILABLE = False


# Regex to find SELECT / WITH / INSERT statements (greedy until next DDL keyword
# or end of SQL).
_SELECT_BLOCK_RE = re.compile(
    r"""(?ix)
        \b(?:select|with|insert\s+into)\b   # start of a query
        .*?                                 # body
        (?=                                 # stop before:
            \bcreate\s+(?:temporary\s+)?table\b |
            \bdrop\s+table\b |
            \bset\s+\w+\s+to\b |
            \breset\s+\w+\b |
            \binsert\s+into\b |
            \Z                              # end of string
        )
    """,
    re.DOTALL,
)


def _extract_select_blocks(sql: str) -> list[str]:
    """Pull every SELECT / WITH / INSERT..SELECT block out of a multi-statement
    MSTR-style SQL body."""
    blocks = []
    for m in _SELECT_BLOCK_RE.finditer(sql or ""):
        chunk = m.group(0).strip().rstrip(";")
        if len(chunk) > 20:  # skip trivial matches
            blocks.append(chunk)
    return blocks


def _short_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _normalize_node(node: Any) -> str:
    """Produce a deterministic string representation of a sqlglot AST node.

    Normalizes away:
      - Literal values (numbers, strings)
      - Table aliases (uses base table name)
      - Exact column ordering within set-like constructs (SELECT projections
        are left in order; WHERE conjuncts are sorted)
    """
    if node is None:
        return "null"
    if not hasattr(node, "key"):
        return str(type(node).__name__)

    key = node.key
    # Literal — anonymize value so "='foo'" and "='bar'" hash the same
    if key == "literal":
        return "lit"

    # Column reference — keep column name, drop alias
    if key == "column":
        name = node.args.get("this")
        return f"col:{str(name).lower() if name else '?'}"

    # Table reference — keep base name
    if key == "table":
        name = node.args.get("this")
        n = str(name).lower() if name else "?"
        return f"tbl:{n}"

    # Recursive: fold children into a canonical form
    parts: list[str] = [key]
    args = getattr(node, "args", {}) or {}
    # Sort expression-set-like args so e.g. WHERE a AND b == WHERE b AND a
    if key in {"and", "or"}:
        kids = []
        for v in args.values():
            if isinstance(v, list):
                kids.extend(_normalize_node(x) for x in v)
            else:
                kids.append(_normalize_node(v))
        kids.sort()
        parts.extend(kids)
    else:
        for k, v in args.items():
            if v is None:
                continue
            if isinstance(v, list):
                parts.append(k + "=[" + ",".join(_normalize_node(x) for x in v) + "]")
            elif hasattr(v, "key"):
                parts.append(k + "=" + _normalize_node(v))
            else:
                parts.append(k + "=" + str(type(v).__name__))
    return "(" + " ".join(parts) + ")"


def _subtree_hashes(node: Any) -> set[str]:
    """Return the set of subtree hashes for a sqlglot AST node (including
    root and all descendants with a `key`)."""
    hashes: set[str] = set()
    if node is None or not hasattr(node, "key"):
        return hashes
    hashes.add(_short_hash(_normalize_node(node)))
    # Recurse into child expressions
    args = getattr(node, "args", {}) or {}
    for v in args.values():
        if isinstance(v, list):
            for x in v:
                if hasattr(x, "key"):
                    hashes |= _subtree_hashes(x)
        elif hasattr(v, "key"):
            hashes |= _subtree_hashes(v)
    return hashes


def compute_ast_hashes(
    records: list[dict],
    dialect: str = "redshift",
    verbose: bool = False,
) -> tuple[dict[str, list[str]], list[dict]]:
    """Parse each record's SQL and return (hashes_by_id, errors).

    records : [{"id": str, "sql": str}, ...]
    returns : ({id: [hash, ...]}, [{"id": ..., "error": ...}, ...])
    """
    out: dict[str, list[str]] = {}
    errors: list[dict] = []
    if not _SQLGLOT_AVAILABLE:
        if verbose:
            print("  sqlglot not installed — skipping AST compute")
        return out, errors

    for rec in records:
        rid = rec.get("id")
        sql = rec.get("sql")
        if not rid or not sql:
            continue

        # Extract SELECT / WITH / INSERT blocks from the multi-statement SQL
        # and parse each independently. MSTR SQL is full of CREATE TEMPORARY
        # TABLE / DROP TABLE / SET query_group noise that sqlglot can't handle
        # as a whole. The regex approach isolates the query-shaped pieces.
        blocks = _extract_select_blocks(sql)
        if not blocks:
            # Try the entire SQL as a last resort
            blocks = [sql]

        trees = []
        parse_failed = True
        for block in blocks:
            try:
                parsed = sqlglot.parse(block, read=dialect, error_level=ErrorLevel.IGNORE)
            except Exception:
                continue
            for t in parsed or []:
                # Skip degenerate Command nodes — they hash identically across
                # different SQL inputs and pollute clustering with false positives.
                if t is None or (hasattr(t, "key") and t.key == "command"):
                    continue
                trees.append(t)
                parse_failed = False
        if parse_failed:
            errors.append({"id": rid, "error": "no parseable SELECT/WITH/INSERT block"})
            continue

        hs: set[str] = set()
        for t in trees:
            hs |= _subtree_hashes(t)
        if hs:
            out[rid] = sorted(hs)
    return out, errors


def jaccard(a: set | frozenset | list | tuple, b) -> float:
    """Set-based Jaccard similarity. Accepts any iterable of hashes."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
