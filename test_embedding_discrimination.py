#!/usr/bin/env python3
"""
Test embedding model discrimination for SQL near-duplicate detection.

Samples known duplicate pairs (from hash-matched groups) and known distinct pairs,
then measures how well each embedding model separates them.

Higher separation score = better model for clustering.
"""

import json
import random
import numpy as np
from pathlib import Path
from sklearn.metrics.pairwise import cosine_similarity

# ── Config ──
OUTPUT_DIR = Path("INSIGHT/rationalization")
NORM_DIR = OUTPUT_DIR / "sql" / "normalized"
INDEX_PATH = OUTPUT_DIR / "sql" / "sql_index.json"
N_PAIRS = 50  # pairs per category


def load_pairs():
    """Load known duplicate and distinct SQL pairs from the index."""
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    # Group by normalized hash
    hash_groups = {}
    for entry in index["files"]:
        h = entry["normalizedHash"]
        hash_groups.setdefault(h, []).append(entry)

    # Known duplicates: pairs from the same hash group
    dup_groups = {h: members for h, members in hash_groups.items() if len(members) >= 2}
    singletons = [members[0] for h, members in hash_groups.items() if len(members) == 1]

    print(f"Duplicate groups: {len(dup_groups)}")
    print(f"Singletons: {len(singletons)}")

    # Sample duplicate pairs
    dup_pairs = []
    dup_group_list = list(dup_groups.values())
    random.shuffle(dup_group_list)
    for members in dup_group_list:
        if len(dup_pairs) >= N_PAIRS:
            break
        a, b = members[0], members[1]
        sql_a = (NORM_DIR / a["filename"]).read_text(encoding="utf-8")
        sql_b = (NORM_DIR / b["filename"]).read_text(encoding="utf-8")
        # These are exact hash matches so SQL is identical after normalization.
        # For a better test, use the RAW sql (pre-normalization) so models see surface differences
        raw_dir = OUTPUT_DIR / "sql" / "raw"
        raw_a = (raw_dir / a["filename"]).read_text(encoding="utf-8")
        raw_b = (raw_dir / b["filename"]).read_text(encoding="utf-8")
        if raw_a != raw_b:  # prefer pairs that differ in raw form
            dup_pairs.append((raw_a[:2000], raw_b[:2000]))
        else:
            dup_pairs.append((sql_a[:2000], sql_b[:2000]))

    # Sample distinct pairs: random singletons
    random.shuffle(singletons)
    distinct_pairs = []
    for i in range(0, min(N_PAIRS * 2, len(singletons)) - 1, 2):
        if len(distinct_pairs) >= N_PAIRS:
            break
        a, b = singletons[i], singletons[i + 1]
        sql_a = (NORM_DIR / a["filename"]).read_text(encoding="utf-8")
        sql_b = (NORM_DIR / b["filename"]).read_text(encoding="utf-8")
        distinct_pairs.append((sql_a[:2000], sql_b[:2000]))

    print(f"Duplicate pairs sampled: {len(dup_pairs)}")
    print(f"Distinct pairs sampled: {len(distinct_pairs)}")
    return dup_pairs, distinct_pairs


def embed_openai(texts, model="text-embedding-3-large"):
    from openai import OpenAI
    client = OpenAI()
    resp = client.embeddings.create(input=texts, model=model)
    return [e.embedding for e in resp.data]


def embed_voyage(texts, model="voyage-code-2"):
    import voyageai
    client = voyageai.Client()
    result = client.embed(texts, model=model, input_type="document")
    return result.embeddings


def embed_local(texts, model_name="all-MiniLM-L6-v2"):
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return model.encode(texts, show_progress_bar=False).tolist()


def test_model(name, embed_fn, dup_pairs, distinct_pairs):
    """Test a single embedding model."""
    print(f"\n  Testing {name}...")

    # Collect all unique texts to embed in one batch
    all_texts = []
    text_to_idx = {}
    for a, b in dup_pairs + distinct_pairs:
        for t in (a, b):
            if t not in text_to_idx:
                text_to_idx[t] = len(all_texts)
                all_texts.append(t)

    print(f"    Embedding {len(all_texts)} unique texts...")
    try:
        # Batch in chunks to avoid API limits
        all_embeddings = []
        batch_size = 50
        for i in range(0, len(all_texts), batch_size):
            batch = all_texts[i:i + batch_size]
            embs = embed_fn(batch)
            all_embeddings.extend(embs)
            if i + batch_size < len(all_texts):
                print(f"    Batch {i // batch_size + 1} done...")

        # Compute scores
        dup_scores = []
        for a, b in dup_pairs:
            emb_a = all_embeddings[text_to_idx[a]]
            emb_b = all_embeddings[text_to_idx[b]]
            score = cosine_similarity([emb_a], [emb_b])[0][0]
            dup_scores.append(score)

        distinct_scores = []
        for a, b in distinct_pairs:
            emb_a = all_embeddings[text_to_idx[a]]
            emb_b = all_embeddings[text_to_idx[b]]
            score = cosine_similarity([emb_a], [emb_b])[0][0]
            distinct_scores.append(score)

        result = {
            "avg_dupe_similarity": float(np.mean(dup_scores)),
            "std_dupe_similarity": float(np.std(dup_scores)),
            "min_dupe_similarity": float(np.min(dup_scores)),
            "avg_distinct_similarity": float(np.mean(distinct_scores)),
            "std_distinct_similarity": float(np.std(distinct_scores)),
            "max_distinct_similarity": float(np.max(distinct_scores)),
            "separation": float(np.mean(dup_scores) - np.mean(distinct_scores)),
        }
        return result

    except Exception as e:
        print(f"    ERROR: {e}")
        return {"error": str(e)}


def main():
    import os
    # Requires OPENAI_API_KEY env var; caller must set it
    os.environ.setdefault("VOYAGE_API_KEY", "al-gsVQoICACWgJQEU-Nb1tQeBrpnVQZcwNyBBYpITJuba")

    random.seed(42)
    print("Loading SQL pairs...")
    dup_pairs, distinct_pairs = load_pairs()

    models = [
        ("all-MiniLM-L6-v2 (local)", lambda texts: embed_local(texts, "all-MiniLM-L6-v2")),
        ("text-embedding-3-large (OpenAI)", lambda texts: embed_openai(texts, "text-embedding-3-large")),
        ("voyage-code-2 (Voyage)", lambda texts: embed_voyage(texts, "voyage-code-2")),
    ]

    results = {}
    for name, embed_fn in models:
        results[name] = test_model(name, embed_fn, dup_pairs, distinct_pairs)

    # Print results
    print(f"\n{'='*70}")
    print(f"  EMBEDDING MODEL DISCRIMINATION TEST")
    print(f"{'='*70}")
    print(f"  {'Model':<35s} {'Dup Avg':>8s} {'Dist Avg':>9s} {'Separation':>11s}")
    print(f"  {'-'*35} {'-'*8} {'-'*9} {'-'*11}")

    best_model = None
    best_sep = -1
    for name, r in results.items():
        if "error" in r:
            print(f"  {name:<35s}   ERROR: {r['error'][:40]}")
        else:
            sep = r["separation"]
            print(f"  {name:<35s} {r['avg_dupe_similarity']:>8.4f} {r['avg_distinct_similarity']:>9.4f} {sep:>11.4f}")
            if sep > best_sep:
                best_sep = sep
                best_model = name

    print(f"\n  WINNER: {best_model} (separation: {best_sep:.4f})")
    print(f"{'='*70}")

    # Detailed stats
    for name, r in results.items():
        if "error" not in r:
            print(f"\n  {name}:")
            print(f"    Duplicates:  avg={r['avg_dupe_similarity']:.4f}  std={r['std_dupe_similarity']:.4f}  min={r['min_dupe_similarity']:.4f}")
            print(f"    Distinct:    avg={r['avg_distinct_similarity']:.4f}  std={r['std_distinct_similarity']:.4f}  max={r['max_distinct_similarity']:.4f}")
            print(f"    Separation:  {r['separation']:.4f}")

    # Save results
    out_path = OUTPUT_DIR / "sql" / "model_discrimination_results.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
