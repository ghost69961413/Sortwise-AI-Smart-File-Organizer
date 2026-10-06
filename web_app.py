"""Flask application providing upload, search, insights, duplicate review and downloads."""
from __future__ import annotations
import hashlib, json, os, shutil, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, abort, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename
import database
from ai_service import classify, extract_text, summarize, embed
from search_service import search, similar_candidates

ROOT=Path(__file__).resolve().parent
UPLOAD_DIR=Path(os.getenv('UPLOAD_DIR',ROOT/'uploads')).resolve()
MAX_BYTES=int(os.getenv('MAX_UPLOAD_MB','50'))*1024*1024
ALLOWED={'.pdf','.docx','.txt','.md','.csv','.json','.png','.jpg','.jpeg','.gif','.webp','.mp4','.mov','.mp3','.wav','.m4a','.aac','.flac','.ogg','.zip'}
app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=MAX_BYTES
UPLOAD_DIR.mkdir(parents=True,exist_ok=True)
database.init_db()

def _folder_parts(folder):
    """Convert classifier folder labels to safe on-disk path components."""
    parts=[secure_filename(part) for part in str(folder).replace('\\','/').split('/')]
    return [part for part in parts if part and part not in {'.','..'}] or ['Other']

def _unique_target(directory, filename):
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/filename
    index=1
    while target.exists():
        target=directory/f"{Path(filename).stem}_{index}{Path(filename).suffix}"
        index+=1
    return target

def _organize_existing_uploads():
    """Move older flat library uploads into their recorded category folders."""
    for record in database.list_files(limit=100000):
        source=Path(record['stored_path']).resolve()
        if not source.is_file() or not source.is_relative_to(UPLOAD_DIR):
            continue
        directory=UPLOAD_DIR.joinpath(*_folder_parts(record['folder']))
        if source.parent==directory.resolve():
            continue
        target=_unique_target(directory,record['original_name'])
        shutil.move(str(source),str(target))
        database.update_stored_path(record['id'],target)

_organize_existing_uploads()

@app.get('/')
def home(): return render_template('index.html')
@app.get('/api/dashboard')
def dashboard(): return jsonify(database.dashboard())
@app.get('/api/files')
def files():
    return jsonify([
        {key: item[key] for key in ('id','original_name','category','folder','uploaded_at','size_bytes')}
        for item in database.list_files()
    ])
@app.post('/api/upload')
def upload():
    incoming=request.files.getlist('files')
    if not incoming: return jsonify(error='Choose at least one file.'),400
    results=[]
    for item in incoming:
        name=secure_filename(item.filename or '')
        if not name: continue
        if Path(name).suffix.lower() not in ALLOWED: results.append({'name':name,'error':'File type is not allowed.'}); continue
        temp=UPLOAD_DIR/(hashlib.sha256((name+str(datetime.now().timestamp())).encode()).hexdigest()[:16]+'_'+name)
        item.save(temp)
        digest=hashlib.sha256()
        with temp.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
        sha=digest.hexdigest(); existing=next((f for f in database.list_files() if f['sha256']==sha),None)
        text=extract_text(temp); category,content_category,tags,topics,confidence=classify(temp,text)
        summary=summarize(text) if temp.suffix.lower() in {'.pdf','.docx','.txt'} else ''
        folder=f"{category}/"+(topics[0].title() if topics else (content_category or 'General').replace('_',' ').title())
        organized_path=_unique_target(UPLOAD_DIR.joinpath(*_folder_parts(folder)),name)
        shutil.move(str(temp),str(organized_path))
        now=datetime.now(timezone.utc).isoformat()
        vector=embed(name+' '+text[:6000])
        record={'original_name':name,'stored_path':str(organized_path),'category':category,'content_category':content_category,'summary':summary,'extracted_text':text,'uploaded_at':now,'size_bytes':organized_path.stat().st_size,'sha256':sha,'tags':json.dumps(tags),'topics':json.dumps(topics),'folder':folder,'duplicate_of':existing['id'] if existing else None,'embedding':json.dumps(vector) if vector is not None else None}
        fid=database.add_file(record)
        results.append({'id':fid,'name':name,'category':category,'summary':summary,'folder':folder,'duplicate':bool(existing),'confidence':confidence})
    return jsonify(results=results)
@app.get('/api/search')
def api_search():
    q=request.args.get('q','').strip()
    if not q:return jsonify(results=[])
    category=request.args.get('category')
    since=None
    lower=q.lower()
    if 'this month' in lower or 'uploaded this month' in lower:
        now=datetime.now(timezone.utc); since=now.strftime('%Y-%m-01T00:00:00+00:00')
    clean=q.replace('this month','').replace('uploaded','').replace('show','').replace('find','').strip()
    query_vector=embed(clean)
    return jsonify(results=search(clean,database.list_files(),category,since,json.dumps(query_vector) if query_vector is not None else None))
@app.get('/api/duplicates')
def duplicates():
    fs=database.list_files()
    exact=[{'id':f['id'],'name':f['original_name'],'duplicate_of':f['duplicate_of']} for f in fs if f['duplicate_of']]
    return jsonify(exact=exact,similar=similar_candidates(fs))
@app.get('/api/files/<int:file_id>/download')
def download(file_id):
    item=database.get_file(file_id)
    if not item: abort(404)
    path=Path(item['stored_path']).resolve()
    if not path.is_relative_to(UPLOAD_DIR): abort(403)
    return send_file(path,as_attachment=True,download_name=item['original_name'])
@app.get('/api/folders/download')
def download_folder():
    folder=request.args.get('folder','').strip()
    if not folder: abort(400)
    records=[item for item in database.list_files(limit=100000) if item['folder']==folder]
    if not records: abort(404)
    archive=tempfile.SpooledTemporaryFile(max_size=16*1024*1024,mode='w+b')
    added=0
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as zipped:
        for item in records:
            path=Path(item['stored_path']).resolve()
            if path.is_file() and path.is_relative_to(UPLOAD_DIR):
                zipped.write(path,arcname=path.name)
                added+=1
    if not added:
        archive.close()
        abort(404)
    archive.seek(0)
    filename='_'.join(_folder_parts(folder))+'.zip'
    response=send_file(archive,mimetype='application/zip',as_attachment=True,download_name=filename)
    response.call_on_close(archive.close)
    return response
@app.get('/api/recommend')
def recommend():
    name=request.args.get('name',''); ext=Path(name).suffix.lower()
    category='Image' if ext in {'.jpg','.jpeg','.png','.gif'} else 'Video' if ext in {'.mp4','.mov'} else 'Audio' if ext in {'.mp3','.wav'} else 'Academic' if any(w in name.lower() for w in ('lecture','assignment','thesis')) else 'Resume' if any(w in name.lower() for w in ('resume','cv')) else 'Project' if 'project' in name.lower() else 'Other'
    return jsonify(category=category,folder=f"{category}/General")
@app.errorhandler(413)
def too_large(_): return jsonify(error='Upload exceeds configured size limit.'),413
if __name__=='__main__': app.run(host=os.getenv('HOST','127.0.0.1'),port=int(os.getenv('PORT','5000')),debug=os.getenv('FLASK_DEBUG')=='1')
