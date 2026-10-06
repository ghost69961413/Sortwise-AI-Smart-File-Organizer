"""File text extraction, classification, summaries, and optional hosted LLM summary."""
from __future__ import annotations
import os, re, zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree
from content_analyzer import analyze_file_content

CATEGORIES={"academic":"Academic","work":"Project","personal":"Personal","finance":"Financial","legal":"Personal","travel":"Personal"}
def extract_text(path: Path) -> str:
    ext=path.suffix.lower()
    try:
        if ext=='.pdf':
            from pypdf import PdfReader
            return '\n'.join(p.extract_text() or '' for p in PdfReader(str(path)).pages[:20])[:50000]
        if ext=='.docx':
            with zipfile.ZipFile(path) as z: root=ElementTree.fromstring(z.read('word/document.xml'))
            return ' '.join(n.text or '' for n in root.iter() if n.tag.endswith('}t'))[:50000]
        if ext in {'.txt','.md','.csv','.json','.log'}: return path.read_text(encoding='utf-8',errors='replace')[:50000]
    except Exception: return ''
    return ''

def summarize(text: str) -> str:
    """Use configured Gemini/OpenAI endpoint optionally; fallback to sentence scoring."""
    if not text.strip(): return ''
    provider=os.getenv('SUMMARY_PROVIDER','').lower()
    try:
        if provider=='openai' and os.getenv('OPENAI_API_KEY'):
            from openai import OpenAI
            response=OpenAI().responses.create(model=os.getenv('OPENAI_MODEL','gpt-4.1-mini'),input=f"Summarize in 2 concise sentences:\n{text[:12000]}")
            return response.output_text.strip()
        if provider=='gemini' and os.getenv('GEMINI_API_KEY'):
            from google import genai
            result=genai.Client(api_key=os.environ['GEMINI_API_KEY']).models.generate_content(model=os.getenv('GEMINI_MODEL','gemini-2.0-flash'),contents=f"Summarize in 2 concise sentences:\n{text[:12000]}")
            return (result.text or '').strip()
    except Exception:
        pass
    sentences=re.split(r'(?<=[.!?])\s+',re.sub(r'\s+',' ',text.strip()))
    if len(sentences)<=2: return ' '.join(sentences)[:500]
    words=re.findall(r'[a-zA-Z]{3,}',text.lower()); freq=Counter(words)
    ranked=sorted(enumerate(sentences),key=lambda x:sum(freq.get(w,0) for w in re.findall(r'[a-zA-Z]{3,}',x[1].lower()))/max(1,len(x[1].split())),reverse=True)
    chosen=sorted(i for i,_ in ranked[:2]); return ' '.join(sentences[i] for i in chosen)[:500]

def classify(path: Path, text: str):
    result=analyze_file_content(path)
    base=result.base_category
    ext=path.suffix.lower()
    if ext in {'.mp3','.wav','.m4a','.aac','.flac','.ogg'}: category='Audio'
    elif base in {'image','video','audio'}: category=base.title()
    else:
        content=result.content_category
        filename=path.name.lower()
        if 'resume' in filename or 'cv' in filename: category='Resume'
        elif content in CATEGORIES: category=CATEGORIES[content]
        elif content=='academic': category='Academic'
        elif any(x in filename for x in ('project','assignment','code')): category='Project'
        elif not text: category='Other'
        else: category='Other'
    tags=list(dict.fromkeys([*result.tags,*re.findall(r'\b[a-zA-Z]{4,}\b',path.stem.lower().replace('_',' '))]))[:20]
    topics=list(dict.fromkeys(re.findall(r'\b(?:AI|machine learning|java|python|database|finance|resume|project|research)\b',text+' '+path.stem,flags=re.I)))[:10]
    return category,result.content_category,tags,topics,result.confidence

def embed(text: str):
    """Return an OpenAI embedding when configured; otherwise use local retrieval."""
    if not os.getenv('OPENAI_API_KEY') or os.getenv('SEARCH_PROVIDER','').lower()!='openai': return None
    try:
        from openai import OpenAI
        return OpenAI().embeddings.create(model=os.getenv('EMBEDDING_MODEL','text-embedding-3-small'),input=text[:8000]).data[0].embedding
    except Exception:
        return None
