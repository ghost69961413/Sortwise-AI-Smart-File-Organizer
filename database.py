"""SQLite persistence for uploaded files and AI enrichment."""
from __future__ import annotations
import json, os, sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.getenv("FILE_ORGANIZER_DB", "instance/files.db"))

@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
        db.commit()
    finally:
        db.close()

def init_db():
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY, original_name TEXT NOT NULL, stored_path TEXT NOT NULL UNIQUE,
          category TEXT NOT NULL, content_category TEXT, summary TEXT DEFAULT '', extracted_text TEXT DEFAULT '',
          uploaded_at TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
          tags TEXT NOT NULL DEFAULT '[]', topics TEXT NOT NULL DEFAULT '[]', folder TEXT NOT NULL,
          duplicate_of INTEGER REFERENCES files(id), embedding TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_files_uploaded ON files(uploaded_at);
        CREATE INDEX IF NOT EXISTS idx_files_hash ON files(sha256);
        CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
        """)

def add_file(item):
    with connection() as db:
        cur = db.execute("""INSERT INTO files(original_name,stored_path,category,content_category,summary,extracted_text,uploaded_at,size_bytes,sha256,tags,topics,folder,duplicate_of,embedding)
          VALUES(:original_name,:stored_path,:category,:content_category,:summary,:extracted_text,:uploaded_at,:size_bytes,:sha256,:tags,:topics,:folder,:duplicate_of,:embedding)""", item)
        return cur.lastrowid

def list_files(limit=500):
    with connection() as db: return [dict(r) for r in db.execute("SELECT * FROM files ORDER BY uploaded_at DESC LIMIT ?", (limit,))]

def get_file(file_id):
    with connection() as db:
        row=db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None

def update_stored_path(file_id, stored_path):
    with connection() as db:
        db.execute("UPDATE files SET stored_path=? WHERE id=?", (str(stored_path), file_id))

def dashboard():
    with connection() as db:
        total=db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        size=db.execute("SELECT COALESCE(SUM(size_bytes),0) FROM files").fetchone()[0]
        dup=db.execute("SELECT COUNT(*) FROM files WHERE duplicate_of IS NOT NULL").fetchone()[0]
        cats=[dict(r) for r in db.execute("SELECT category,COUNT(*) count FROM files GROUP BY category ORDER BY count DESC")]
        recent=[dict(r) for r in db.execute("SELECT id,original_name,category,size_bytes,uploaded_at,folder FROM files ORDER BY uploaded_at DESC LIMIT 8")]
        alltags=db.execute("SELECT tags FROM files").fetchall()
    from collections import Counter
    topics=Counter(t for row in alltags for t in json.loads(row[0]))
    return {"total_files":total,"storage_bytes":size,"duplicate_count":dup,"categories":cats,"recent":recent,"topics":[{"topic":k,"count":v} for k,v in topics.most_common(10)]}
