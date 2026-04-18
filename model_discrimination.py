import numpy as np
from openai import OpenAI
import voyageai
from sklearn.metrics.pairwise import cosine_similarity

openai_client = OpenAI()
voyage_client = voyageai.Client()

# Take 50 known duplicate pairs from your telemetry
# (same report rebuilt in INSIGHT and Global Insight)
# And 50 known distinct pairs
# Then measure which model separates them more cleanly

def test_model_discrimination(known_dupes, known_distinct):
    results = {}
    
    for model_name, embed_fn in [
        ("text-embedding-3-large", embed_openai),
        ("voyage-code-2",          embed_voyage)
    ]:
        dupe_scores     = []
        distinct_scores = []
        
        for sql_a, sql_b in known_dupes:
            emb_a = embed_fn(sql_a)
            emb_b = embed_fn(sql_b)
            score = cosine_similarity([emb_a], [emb_b])[0][0]
            dupe_scores.append(score)
        
        for sql_a, sql_b in known_distinct:
            emb_a = embed_fn(sql_a)
            emb_b = embed_fn(sql_b)
            score = cosine_similarity([emb_a], [emb_b])[0][0]
            distinct_scores.append(score)
        
        results[model_name] = {
            "avg_dupe_similarity":     np.mean(dupe_scores),
            "avg_distinct_similarity": np.mean(distinct_scores),
            "separation":              np.mean(dupe_scores) - np.mean(distinct_scores)
            # Higher separation = better discrimination
        }
    
    return results