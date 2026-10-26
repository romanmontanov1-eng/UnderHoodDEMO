"""UnderHood CMS. Only explicitly declared public/UI routes serve files."""
from __future__ import annotations
import hashlib, io, json, os, re, secrets, sqlite3, threading, time, uuid, zipfile, tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory, g, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import HTTPException
from PIL import Image, ImageOps, ImageDraw, ImageFont, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
IDS = ['polishing', 'protection', 'wrapping', 'interior']
MEDIA = re.compile(r'^/api/media/([a-f0-9-]{36}\.webp)$')
IMAGE_LOCK = threading.Lock()

class ManagedConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

def stamp(): return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
def digest(s): return hashlib.sha256(s.encode()).hexdigest()

class Problem(Exception):
    def __init__(self, message, status=400): self.message, self.status = message, status

def create_app(config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(DATA_DIR=os.environ.get('DATA_DIR', str(ROOT / '.cms-data')),
        CMS_ORIGIN=os.environ.get('CMS_ORIGIN', 'http://127.0.0.1:8010'),
        PUBLIC_SITE_URL=os.environ.get('PUBLIC_SITE_URL', 'https://underhood-detailing.onrender.com'),
        ADMIN_EMAIL=os.environ.get('ADMIN_EMAIL', ''), ADMIN_PASSWORD_HASH=os.environ.get('ADMIN_PASSWORD_HASH', ''),
        COOKIE_SECURE=os.environ.get('COOKIE_SECURE', '1') == '1', MAX_CONTENT_LENGTH=21*1024*1024,
        REQUIRE_PERSISTENT_DISK=os.environ.get('REQUIRE_PERSISTENT_DISK', '1') == '1',
        MEDIA_QUOTA_BYTES=600*1024*1024)
    if config: app.config.update(config)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=0)
    data = Path(app.config['DATA_DIR']).resolve()
    # In production the mount must exist before starting. Never initialize on ephemeral storage.
    if app.config['REQUIRE_PERSISTENT_DISK'] and not os.path.ismount(data):
        raise RuntimeError('DATA_DIR must be the persistent disk mount. Attach /var/data before starting CMS.')
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    photos = data / 'media'; photos.mkdir(exist_ok=True, mode=0o700)
    dbpath = data / 'cms.sqlite3'
    def connect():
        db=sqlite3.connect(dbpath, timeout=15, factory=ManagedConnection); db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON'); db.execute('PRAGMA busy_timeout=15000'); return db
    with connect() as db:
        db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY CHECK(id=1), email TEXT NOT NULL, password TEXT NOT NULL, must_change INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS content (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS backups (revision TEXT PRIMARY KEY, saved_at TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL, seen REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS failures (ip TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS failures_at ON failures(at);
        CREATE TABLE IF NOT EXISTS media (name TEXT PRIMARY KEY, size INTEGER NOT NULL, created REAL NOT NULL);
        ''')
        if not db.execute('SELECT 1 FROM account').fetchone():
            email=app.config['ADMIN_EMAIL'].strip().lower(); password=app.config['ADMIN_PASSWORD_HASH']
            if not email or not password.startswith('scrypt:'): raise RuntimeError('Set ADMIN_EMAIL and ADMIN_PASSWORD_HASH before first start.')
            db.execute('INSERT INTO account VALUES(1,?,?,1)', (email,password))
        if not db.execute('SELECT 1 FROM content').fetchone():
            initial=json.loads((ROOT/'content-default.json').read_text())
            initial.update(revision=str(uuid.uuid4()), updatedAt=stamp())
            db.execute('INSERT INTO content VALUES(1,?)',(json.dumps(initial,ensure_ascii=False),))
    app.db_connect=connect
    @contextmanager
    def transaction():
        db=connect()
        try:
            db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
        except: db.rollback(); raise
        finally: db.close()
    def current(db): return json.loads(db.execute('SELECT body FROM content WHERE id=1').fetchone()['body'])
    def secure_origin():
        if request.headers.get('Origin') != app.config['CMS_ORIGIN']: raise Problem('Запрос с другого сайта отклонён.',403)
    def session(db):
        raw=request.cookies.get('uh_session','')
        row=db.execute('SELECT * FROM sessions WHERE token_hash=?',(digest(raw),)).fetchone() if raw else None
        now=time.time()
        if not row or row['expires']<now or row['seen']<now-3600: raise Problem('Сессия закончилась. Войдите снова.',401)
        return row
    def protected(fn):
        @wraps(fn)
        def wrapped(*args,**kwargs):
            with connect() as db:
                row=session(db); g.csrf=row['csrf']
                account=db.execute('SELECT * FROM account WHERE id=1').fetchone(); g.account=dict(account)
                if request.method not in ['GET','HEAD','OPTIONS']:
                    secure_origin()
                    if not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),row['csrf']): raise Problem('Обновите страницу и повторите запрос.',403)
                    if account['must_change'] and request.path not in ['/api/auth/password','/api/auth/logout']: raise Problem('Сначала задайте постоянный пароль.',403)
                db.execute('UPDATE sessions SET seen=? WHERE token_hash=?',(time.time(),row['token_hash']))
            return fn(*args,**kwargs)
        return wrapped
    def issue_session(db, response):
        token=secrets.token_urlsafe(48);csrf=secrets.token_urlsafe(32);now=time.time()
        db.execute('DELETE FROM sessions WHERE expires<? OR seen<?',(now,now-3600))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),csrf,now+43200,now))
        response.set_cookie('uh_session',token,max_age=43200,secure=app.config['COOKIE_SECURE'],httponly=True,samesite='Strict',path='/')
        return csrf
    def payload():
        if not request.is_json or (request.content_length or 0)>512*1024: raise Problem('Нужен JSON размером не более 512 КБ.',400)
        value=request.get_json(silent=True)
        if not isinstance(value,dict):raise Problem('Некорректные данные.')
        return value
    def validate(c,db):
        if not isinstance(c,dict):raise Problem('Некорректный контент.')
        def string(obj,key,maxlen=3000,required=False):
            v=obj.get(key)
            if not isinstance(v,str) or len(v)>maxlen or (required and not v.strip()):raise Problem('Проверьте поле: '+key)
            return v
        clean={key:string(c,key,required=key in ['heroTagline','heroSubtitle','phone','telegram']) for key in ['heroTagline','heroSubtitle','intro','phone','telegram','address']}
        if not re.fullmatch(r'[+\d\s()-]{5,30}',clean['phone']) or not re.fullmatch(r'[a-zA-Z0-9_]{5,32}',clean['telegram']):raise Problem('Проверьте телефон и Telegram.')
        services=c.get('services');faq=c.get('faq');cases=c.get('cases')
        if not isinstance(services,list) or len(services)!=4 or not isinstance(faq,list) or len(faq)>30 or not isinstance(cases,list):raise Problem('Некорректные списки услуг, вопросов или работ.')
        if len(cases)>50:raise Problem('Достигнут лимит: 50 работ.')
        clean['services']=[]
        for i,s in enumerate(services):
            if not isinstance(s,dict) or s.get('id')!=IDS[i]:raise Problem('Порядок услуг изменён.')
            item={'id':IDS[i],**{k:string(s,k,required=True) for k in ['title','tag','description','price']}}
            # Preserve the original informational steps, which the existing editor does not change.
            steps=s.get('steps',[])
            if not isinstance(steps,list) or len(steps)>20 or any(not isinstance(v,str) or len(v)>1000 for v in steps):raise Problem('Некорректные этапы услуги.')
            item['steps']=steps;clean['services'].append(item)
        clean['faq']=[]
        for f in faq:
            if not isinstance(f,dict):raise Problem('Некорректный вопрос.')
            clean['faq'].append({'q':string(f,'q',500,True),'a':string(f,'a',3000,True)})
        clean['cases']=[]
        for work in cases:
            if not isinstance(work,dict):raise Problem('Некорректная работа.')
            image=string(work,'image');match=MEDIA.fullmatch(image)
            if not match or not db.execute('SELECT 1 FROM media WHERE name=?',(match[1],)).fetchone() or not (photos/match[1]).is_file():raise Problem('Фотография работы не найдена. Загрузите её заново.')
            clean['cases'].append({'title':string(work,'title',300,True),'description':string(work,'description',3000,True),'image':image})
        if not isinstance(c.get('watermarkPhotos',True),bool):raise Problem('Некорректная настройка водяного знака.')
        clean['watermarkPhotos']=c.get('watermarkPhotos',True);return clean
    def save(db,c,expected):
        old=current(db)
        if expected!=old['revision']:raise Problem('Контент изменён в другом окне. Скачайте черновик, затем обновите страницу. Ваши изменения не перезаписали сайт.',409)
        clean=validate(c,db)
        db.execute('INSERT INTO backups VALUES(?,?,?)',(old['revision'],old['updatedAt'],json.dumps(old,ensure_ascii=False)))
        clean.update(revision=str(uuid.uuid4()),updatedAt=stamp())
        db.execute('UPDATE content SET body=? WHERE id=1',(json.dumps(clean,ensure_ascii=False),))
        db.execute('DELETE FROM backups WHERE revision NOT IN (SELECT revision FROM backups ORDER BY saved_at DESC, rowid DESC LIMIT 30)')
        return clean
    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY'
        if not request.path.startswith('/api/media/'):
            response.headers['Cache-Control']='no-store'
        if request.path=='/api/content':response.headers['Access-Control-Allow-Origin']='*'
        if not request.path.startswith('/preview/'):
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if app.config['COOKIE_SECURE']:response.headers['Strict-Transport-Security']='max-age=31536000'
        return response
    @app.errorhandler(Problem)
    def problem(e):return jsonify(error=e.message),e.status
    @app.errorhandler(HTTPException)
    def http_error(e):return jsonify(error='Страница не найдена.' if e.code==404 else 'Запрос не принят.'),e.code
    @app.errorhandler(Exception)
    def storage_error(e):
        app.logger.error('CMS operation failed (%s)',type(e).__name__)
        return jsonify(error='Не удалось выполнить операцию. Данные на сайте не изменены; повторите позже.'),503
    @app.get('/api/health')
    def health():
        with connect() as db:db.execute('SELECT 1 FROM content').fetchone()
        return jsonify(status='ok')
    @app.get('/api/content')
    def read_content():
        with connect() as db:return jsonify(content=current(db))
    @app.post('/api/auth/login')
    def login():
        secure_origin();value=payload();email=value.get('email','');password=value.get('password','')
        if not isinstance(email,str) or not isinstance(password,str) or len(email)>254 or len(password)>128:raise Problem('Проверьте данные входа.')
        now=time.time();ip=request.remote_addr or 'unknown';success=False
        with transaction() as db:
            db.execute('DELETE FROM failures WHERE at<?',(now-3600,))
            attempts=db.execute('SELECT COUNT(*) FROM failures WHERE ip=? AND at>?',(ip,now-900)).fetchone()[0]
            total=db.execute('SELECT COUNT(*) FROM failures').fetchone()[0]
            if attempts>=10 or total>=100:raise Problem('Слишком много попыток входа. Повторите через 15 минут.',429)
            account=db.execute('SELECT * FROM account WHERE id=1').fetchone()
            valid=check_password_hash(account['password'],password)
            success=valid and secrets.compare_digest(email.strip().lower().encode(),account['email'].encode())
            if success:
                db.execute('DELETE FROM failures WHERE ip=?',(ip,));response=jsonify(ok=True,mustChange=bool(account['must_change']))
                issue_session(db,response)
            else:db.execute('INSERT INTO failures VALUES(?,?)',(ip,now))
        if not success:raise Problem('Неверный email или пароль.',401)
        return response
    @app.get('/api/auth/session')
    @protected
    def auth_session():return jsonify(email=g.account['email'],csrf=g.csrf,mustChange=bool(g.account['must_change']))
    @app.post('/api/auth/logout')
    @protected
    def logout():
        with connect() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(digest(request.cookies['uh_session']),))
        response=jsonify(ok=True);response.delete_cookie('uh_session',path='/',secure=app.config['COOKIE_SECURE'],httponly=True,samesite='Strict');return response
    @app.post('/api/auth/password')
    @protected
    def password():
        value=payload();old=value.get('current','');new=value.get('password','')
        if not isinstance(old,str) or not isinstance(new,str) or not 12<=len(new)<=128 or new==old:raise Problem('Задайте новый пароль от 12 до 128 символов.')
        if not check_password_hash(g.account['password'],old):raise Problem('Текущий пароль неверен.',403)
        hashed=generate_password_hash(new)
        with transaction() as db:
            # A concurrent password change must not let a stale session replace the new password.
            session(db)
            if db.execute('SELECT password FROM account WHERE id=1').fetchone()[0]!=g.account['password']:raise Problem('Пароль уже изменён в другом окне. Войдите снова.',409)
            db.execute('UPDATE account SET password=?,must_change=0 WHERE id=1',(hashed,));db.execute('DELETE FROM sessions')
            response=jsonify(ok=True);issue_session(db,response)
        return response
    @app.get('/api/admin')
    @protected
    def admin_content():
        with connect() as db:return jsonify(content=current(db))
    @app.put('/api/admin')
    @protected
    def write_content():
        value=payload()
        with transaction() as db:session(db);result=save(db,value.get('content'),value.get('revision'))
        return jsonify(content=result,backupCreated=True)
    @app.get('/api/backups')
    @protected
    def backups():
        with connect() as db:return jsonify(backups=[{'key':r['revision'],'savedAt':r['saved_at']} for r in db.execute('SELECT revision,saved_at FROM backups ORDER BY saved_at DESC,rowid DESC LIMIT 30')])
    @app.post('/api/backups')
    @protected
    def restore():
        value=payload();key=value.get('key')
        if not isinstance(key,str) or not re.fullmatch(r'[a-f0-9-]{36}',key):raise Problem('Некорректная резервная копия.')
        with transaction() as db:
            session(db);row=db.execute('SELECT body FROM backups WHERE revision=?',(key,)).fetchone()
            if not row:raise Problem('Резервная копия не найдена.',404)
            result=save(db,json.loads(row['body']),value.get('revision'))
        return jsonify(content=result,backupCreated=True)
    @app.post('/api/media')
    @protected
    def upload():
        file=request.files.get('file')
        if not file or file.mimetype not in ['image/jpeg','image/png','image/webp']:raise Problem('Выберите JPG, PNG или WebP.')
        raw=file.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise Problem('Исходное фото должно быть не больше 20 МБ.',413)
        mark=request.form.get('watermark')
        if mark not in [None,'0','1']:raise Problem('Некорректная настройка фото.')
        with connect() as db:watermark=current(db).get('watermarkPhotos',True) if mark is None else mark=='1'
        with IMAGE_LOCK:
            try:
                with Image.open(io.BytesIO(raw)) as source:
                    if source.format not in ['JPEG','PNG','WEBP'] or source.width*source.height>50_000_000 or getattr(source,'n_frames',1)!=1:raise Problem('Нужно статичное изображение до 50 мегапикселей.')
                    image=ImageOps.exif_transpose(source);image.thumbnail((1920,1920),Image.Resampling.LANCZOS);image=image.convert('RGB')
                if watermark:
                    size=max(12,round(image.width*.023));pad=max(12,round(image.width*.025))
                    try:font=ImageFont.truetype('DejaVuSans-Bold.ttf',size)
                    except OSError:font=ImageFont.load_default(size=size)
                    overlay=Image.new('RGBA',image.size);draw=ImageDraw.Draw(overlay);draw.text((image.width-pad,image.height-pad),'UNDERHOOD',font=font,anchor='rs',fill=(255,255,255,118),stroke_width=1,stroke_fill=(0,0,0,65))
                    image=Image.alpha_composite(image.convert('RGBA'),overlay).convert('RGB')
                encoded=io.BytesIO();image.save(encoded,'WEBP',quality=82,method=4)
                if encoded.tell()>1200*1024:encoded=io.BytesIO();image.save(encoded,'WEBP',quality=68,method=4)
                if encoded.tell()>1200*1024:raise Problem('Не удалось уменьшить фото до 1,2 МБ. Уменьшите разрешение.')
                blob=encoded.getvalue();width,height=image.size
            except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):raise Problem('Файл не является исправным изображением.')
        name=str(uuid.uuid4())+'.webp';path=photos/name
        try:
            with transaction() as db:
                session(db)
                used=db.execute('SELECT COALESCE(SUM(size),0) FROM media').fetchone()[0]
                if used+len(blob)>app.config['MEDIA_QUOTA_BYTES']:raise Problem('Хранилище фото заполнено. Скачайте архив и обратитесь к администратору.',409)
                with open(path,'xb') as output:output.write(blob);output.flush();os.fsync(output.fileno())
                db.execute('INSERT INTO media VALUES(?,?,?)',(name,len(blob),time.time()))
        except:
            path.unlink(missing_ok=True);raise
        return jsonify(url='/api/media/'+name,width=width,height=height,size=len(blob),watermark=watermark)
    @app.get('/api/media/<name>')
    def media(name):
        if not MEDIA.fullmatch('/api/media/'+name):raise Problem('Фотография не найдена.',404)
        response=send_from_directory(photos,name,mimetype='image/webp',max_age=31536000)
        response.headers['Cache-Control']='public,max-age=31536000,immutable';return response
    @app.get('/api/export')
    @protected
    def export():
        with connect() as db:
            db.execute('BEGIN');live=current(db);history=[json.loads(r['body']) for r in db.execute('SELECT body FROM backups ORDER BY saved_at DESC,rowid DESC')]
            names={MEDIA.fullmatch(w['image'])[1] for c in [live,*history] for w in c.get('cases',[]) if MEDIA.fullmatch(w.get('image',''))}
        archive=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('content.json',json.dumps(live,ensure_ascii=False,indent=2));z.writestr('backups.json',json.dumps(history,ensure_ascii=False,indent=2))
            for name in sorted(names):z.write(photos/name,'media/'+name)
            z.writestr('README.txt','UnderHood: content.json, backups.json, media/. No passwords or sessions are included.\n')
        archive.seek(0)
        response=send_file(archive,mimetype='application/zip',as_attachment=True,download_name='underhood-backup-'+datetime.now().strftime('%Y%m%d')+'.zip')
        response.call_on_close(archive.close);return response
    @app.get('/')
    def root():return redirect('/admin',302)
    @app.get('/admin')
    @app.get('/admin.html')
    def admin():return send_from_directory(ROOT/'cms/admin','admin.html')
    @app.get('/<name>')
    def ui_file(name):
        if name in ['admin.js','admin.css','image-tools.js']:return send_from_directory(ROOT/'cms/admin',name)
        if name=='style.css':return send_from_directory(ROOT,name)
        raise Problem('Страница не найдена.',404)
    @app.get('/assets/logo-transparent.png')
    def logo():return send_from_directory(ROOT/'assets','logo-transparent.png')
    app.validate_content=validate
    return app

if __name__=='__main__':create_app().run(host='0.0.0.0',port=int(os.environ.get('PORT','8010')))
