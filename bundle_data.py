"""Bundle every gitignored file the app needs into one zip — for moving
the project state to another machine. Skips secrets, build artifacts,
and diagnostic dumps.

Output: C:/Users/anilk/mstr_api_bundle_<ts>.zip
"""
from __future__ import annotations
import os
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# -------- What goes in --------------------------------------------------
INCLUDE: list[tuple[Path, str]] = []  # (source, arcname-prefix)

# 1. The database — crown jewel
INCLUDE.append((ROOT / "data.db", "data.db"))

# 2. Playground experiments + active pointer
INCLUDE.append((ROOT / "data" / "_playground", "data/_playground"))

# 3. Emitted UI data (per-project public/data + cross-project shared)
INCLUDE.append((ROOT / "public" / "data", "public/data"))

# 4. Raw input data — needed to rebuild from scratch
for d in ["Global Operational", "Global Insight", "INSIGHT"]:
    p = ROOT / d
    if p.exists():
        INCLUDE.append((p, d))

# 5. Loose top-level data files
for f in [
    "User Activity.xlsx", "UserActivity.csv", "UserActivity_summary.csv",
    "extract_GI_batch1.json", "extract_GI_batch2.json",
    "extracted_sql_gfe085a.json", "go_usage_rationalization.json",
    "inv_report_names.json", "inv_reports_simple.json",
    "parent_reports.csv",
    "mstr_analysis.db",  # older alternate DB, kept for completeness
]:
    p = ROOT / f
    if p.exists():
        INCLUDE.append((p, f))

# 6. .env.example (NOT .env.local — that has secrets!)
INCLUDE.append((ROOT / ".env.example", ".env.example"))


# -------- What gets skipped inside any included directory ---------------
SKIP_DIR_NAMES = {"__pycache__", "node_modules", ".git", "dist"}
SKIP_FILE_SUFFIXES = (".pyc", ".pyo")


def _iter_files(src: Path, arc_prefix: str):
    """Yield (file_on_disk, arcname_in_zip) pairs."""
    if src.is_file():
        yield src, arc_prefix
        return
    if not src.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(src):
        # mutate dirnames in-place so os.walk skips them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        rel_dir = Path(dirpath).relative_to(src)
        for fn in filenames:
            if fn.endswith(SKIP_FILE_SUFFIXES):
                continue
            f = Path(dirpath) / fn
            arc = (Path(arc_prefix) / rel_dir / fn).as_posix()
            yield f, arc


def main() -> int:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path("C:/Users/anilk")
    out_path = out_dir / f"mstr_api_bundle_{ts}.zip"

    # Pre-flight: total uncompressed bytes
    total = 0
    file_count = 0
    for src, arc_prefix in INCLUDE:
        for f, _arc in _iter_files(src, arc_prefix):
            total += f.stat().st_size
            file_count += 1
    print(f"Bundle plan: {file_count:,} files, {total / 1_000_000_000:.1f} GB uncompressed")
    print(f"Output:      {out_path}")
    print()

    t0 = time.time()
    written = 0
    last_print = 0.0
    with zipfile.ZipFile(
        out_path, "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=4,  # 4 is the sweet spot — most savings, fast
        allowZip64=True,
    ) as zf:
        for src, arc_prefix in INCLUDE:
            print(f"  + {arc_prefix}")
            for f, arc in _iter_files(src, arc_prefix):
                try:
                    zf.write(f, arcname=arc)
                except Exception as e:
                    print(f"    !! skip {f}: {e}")
                    continue
                written += f.stat().st_size
                # Progress every ~5s
                now = time.time()
                if now - last_print > 5.0:
                    pct = 100.0 * written / total if total else 0
                    rate = written / (now - t0) / 1_000_000  # MB/s
                    print(f"    [{pct:5.1f}%] {written/1_000_000_000:.2f}/{total/1_000_000_000:.2f} GB · {rate:.1f} MB/s")
                    last_print = now

    elapsed = time.time() - t0
    out_size = out_path.stat().st_size
    ratio = (1 - out_size / total) * 100 if total else 0
    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  Output: {out_path}")
    print(f"  Size:   {out_size / 1_000_000_000:.2f} GB ({ratio:.0f}% reduction)")
    print(f"  Files:  {file_count:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
