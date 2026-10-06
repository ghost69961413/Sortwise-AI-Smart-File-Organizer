"""Semantic-ish local search with optional sentence-transformers embeddings."""
from __future__ import annotations
import json, math, re
from collections import Counter

def vectorize(text):
    words=re.findall(r'[a-z0-9]{2,}',text.lower())
    counts=Counter(words); norm=math.sqrt(sum(v*v for v in counts.values())) or 1
    return {k:v/norm for k,v in counts.items()}
def similarity(a,b):
    va,vb=vectorize(a),vectorize(b)
    return sum(v*vb.get(k,0) for k,v in va.items())

def embedding_similarity(a, b):
    """Cosine score for a query/document embedding pair, if both are present."""
    try:
        va, vb = json.loads(a), json.loads(b)
        dot=sum(x*y for x,y in zip(va,vb))
        na=math.sqrt(sum(x*x for x in va)); nb=math.sqrt(sum(x*x for x in vb))
        return dot/(na*nb) if na and nb else 0
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0

def search(query, files, category=None, since=None, query_embedding=None):
    q=query.lower().strip()
    # Extract lightweight constraints from natural language. Date constraints are applied by caller.
    filtered=[]
    for f in files:
        if category and f['category'].lower()!=category.lower(): continue
        if since and f['uploaded_at']<since: continue
        corpus=' '.join([f['original_name'],f['category'],f.get('content_category') or '',f.get('summary') or '',f.get('extracted_text') or '', ' '.join(json.loads(f.get('tags') or '[]')), ' '.join(json.loads(f.get('topics') or '[]'))])
        score=embedding_similarity(query_embedding, f.get('embedding')) if query_embedding and f.get('embedding') else similarity(q,corpus)
        if score>0.01: filtered.append((score,f))
    return [dict(score=round(s,3),**f) for s,f in sorted(filtered,key=lambda x:x[0],reverse=True)[:50]]

def similar_candidates(files, threshold=.72):
    candidates=[]
    for i,a in enumerate(files):
        if a.get('duplicate_of'): continue
        ta=' '.join([a.get('original_name',''),a.get('extracted_text',''),a.get('summary','')])
        for b in files[i+1:]:
            if a['sha256']==b['sha256']: continue
            tb=' '.join([b.get('original_name',''),b.get('extracted_text',''),b.get('summary','')])
            score=similarity(ta,tb)
            if score>=threshold: candidates.append({'file_a':a['original_name'],'file_b':b['original_name'],'similarity':round(score,3)})
    return candidates[:100]
