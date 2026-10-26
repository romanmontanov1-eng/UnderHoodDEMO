import copy,io,json,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from PIL import Image
from werkzeug.security import generate_password_hash
from cms.app import create_app

ORIGIN='http://localhost'
EMAIL='owner@example.test'
INITIAL='Temporary-test-password-100'
PASSWORD='Permanent-test-password-200'

@pytest.fixture
def app(tmp_path):
    return create_app(dict(TESTING=True,DATA_DIR=str(tmp_path),REQUIRE_PERSISTENT_DISK=False,COOKIE_SECURE=False,CMS_ORIGIN=ORIGIN,ADMIN_EMAIL=EMAIL,ADMIN_PASSWORD_HASH=generate_password_hash(INITIAL)))

def login(app,password=INITIAL,change=True):
    client=app.test_client()
    r=client.post('/api/auth/login',json={'email':EMAIL,'password':password},headers={'Origin':ORIGIN});assert r.status_code==200
    session=client.get('/api/auth/session').json
    headers={'Origin':ORIGIN,'X-CSRF-Token':session['csrf']}
    if session['mustChange'] and change:
        r=client.post('/api/auth/password',json={'current':password,'password':PASSWORD},headers=headers);assert r.status_code==200
        headers['X-CSRF-Token']=client.get('/api/auth/session').json['csrf']
    return client,headers

def get_content(client):return client.get('/api/content').json['content']
def save(client,headers,content,revision=None):
    return client.put('/api/admin',json={'revision':revision or content['revision'],'content':content},headers=headers)
def photo(client,headers,mark='1',size=(2400,1200)):
    data=io.BytesIO();Image.new('RGB',size,(40,60,80)).save(data,'PNG');data.seek(0)
    return client.post('/api/media',data={'file':(data,'photo.png'),'watermark':mark},headers=headers,content_type='multipart/form-data')

def test_auth_csrf_password_and_logout(app):
    c=app.test_client()
    for path in ['/api/admin','/api/backups','/api/export','/api/auth/session']:assert c.get(path).status_code==401
    assert c.get('/api/content').headers['Access-Control-Allow-Origin']=='*'
    assert c.post('/api/auth/login',json={'email':EMAIL,'password':INITIAL},headers={'Origin':'https://stranger.test'}).status_code==403
    c,h=login(app,change=False)
    assert save(c,h,get_content(c)).status_code==403
    assert c.post('/api/auth/password',json={'current':INITIAL,'password':PASSWORD},headers={**h,'X-CSRF-Token':'bad'}).status_code==403
    assert c.post('/api/auth/password',json={'current':INITIAL,'password':'short'},headers=h).status_code==400
    second,_=login(app,change=False)
    assert c.post('/api/auth/password',json={'current':INITIAL,'password':PASSWORD},headers=h).status_code==200
    assert second.get('/api/admin').status_code==401
    h['X-CSRF-Token']=c.get('/api/auth/session').json['csrf']
    assert save(c,{**h,'Origin':'https://stranger.test'},get_content(c)).status_code==403
    assert save(c,{**h,'X-CSRF-Token':'bad'},get_content(c)).status_code==403
    assert c.post('/api/auth/logout',headers=h).status_code==200
    assert c.get('/api/admin').status_code==401
    with app.db_connect() as db:assert db.execute('SELECT password FROM account').fetchone()[0]!=PASSWORD

def test_login_throttle(app):
    c=app.test_client()
    for _ in range(10):assert c.post('/api/auth/login',json={'email':EMAIL,'password':'wrong'},headers={'Origin':ORIGIN}).status_code==401
    assert c.post('/api/auth/login',json={'email':EMAIL,'password':INITIAL},headers={'Origin':ORIGIN}).status_code==429

def test_atomic_revision_conflict_and_restore(app):
    c,h=login(app);state=get_content(c);initial=copy.deepcopy(state)
    def change(title):
        r=c # Use a separate client per simultaneous request, sharing only the session token.
        x=app.test_client();x.set_cookie('uh_session',c.get_cookie('uh_session').value)
        draft=copy.deepcopy(state);draft['heroTagline']=title
        return save(x,h,draft).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(change,['First','Second']))==[200,409]
    current=get_content(c);assert current['heroTagline'] in ['First','Second'];assert current['revision']!=state['revision']
    versions=c.get('/api/backups').json['backups'];assert len(versions)==1
    assert c.post('/api/backups',json={'key':versions[0]['key'],'revision':state['revision']},headers=h).status_code==409
    r=c.post('/api/backups',json={'key':versions[0]['key'],'revision':current['revision']},headers=h)
    assert r.status_code==200;assert r.json['content']['heroTagline']==initial['heroTagline'];assert len(c.get('/api/backups').json['backups'])==2

def test_backup_failure_keeps_live_content(app):
    c,h=login(app);state=get_content(c)
    with app.db_connect() as db:db.execute("CREATE TRIGGER fail_backup BEFORE INSERT ON backups BEGIN SELECT RAISE(ABORT,'storage failure'); END")
    draft=copy.deepcopy(state);draft['heroTagline']='Must never be committed'
    assert save(c,h,draft).status_code==503
    assert get_content(c)==state

def test_photos_limits_backup_export_and_restart(app):
    c,h=login(app)
    bad=c.post('/api/media',data={'file':(io.BytesIO(b'\x89PNG\r\n\x1a\n'),'invalid.png')},headers=h)
    assert bad.status_code==400
    one=photo(c,h,mark='0');two=photo(c,h,mark='1');assert one.status_code==two.status_code==200
    assert one.json['width']==1920 and one.json['height']==960 and one.json['size']<=1200*1024
    a=c.get(one.json['url']);b=c.get(two.json['url']);assert a.data!=b.data
    image=Image.open(io.BytesIO(b.data));assert image.format=='WEBP' and image.size==(1920,960)
    state=get_content(c);state['cases']=[dict(title='Test upload',description='Fixture, never production',image=two.json['url'])]*50
    good=save(c,h,state);assert good.status_code==200
    too_many=copy.deepcopy(good.json['content']);too_many['cases'].append(too_many['cases'][0]);assert save(c,h,too_many).status_code==400
    invalid=copy.deepcopy(good.json['content']);invalid['cases'][0]['image']='https://stranger.test/image.png';assert save(c,h,invalid).status_code==400
    exported=c.get('/api/export');assert exported.status_code==200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as z:
        assert 'content.json' in z.namelist() and 'backups.json' in z.namelist()
        assert any(n.startswith('media/') for n in z.namelist())
        assert all('sqlite' not in n and 'session' not in n for n in z.namelist())
    restarted=create_app(app.config)
    assert restarted.test_client().get('/api/content').json['content']==good.json['content']
    assert restarted.test_client().get(two.json['url']).status_code==200
    restarted.config['MEDIA_QUOTA_BYTES']=1
    x=restarted.test_client();x.set_cookie('uh_session',c.get_cookie('uh_session').value)
    assert photo(x,h).status_code==409

def test_history_retention_validation_and_hidden_files(app):
    c,h=login(app)
    for i in range(33):
        state=get_content(c);state['heroTagline']='History '+str(i);assert save(c,h,state).status_code==200
    assert len(c.get('/api/backups').json['backups'])==30
    for path in ['/cms/app.py','/cms.sqlite3','/.env','/.git/config','/requirements.txt','/build.sh']:
        assert c.get(path).status_code==404,path
    state=get_content(c);state['telegram']='javascript:alert(1)';assert save(c,h,state).status_code==400
    state=get_content(c);state['services'].reverse();assert save(c,h,state).status_code==400
    assert c.get('/admin').status_code==200
    assert b'ChatGPT' not in c.get('/admin').data
    assert 'HttpOnly' in login(app,password=PASSWORD)[0].get('/api/auth/session').headers.get('Set-Cookie','') or c.get_cookie('uh_session').http_only

def test_disk_is_required_before_production_start(tmp_path):
    with pytest.raises(RuntimeError,match='persistent disk'):
        create_app(dict(DATA_DIR=str(tmp_path),REQUIRE_PERSISTENT_DISK=True))

def test_offline_archive_restore_and_path_validation(app,tmp_path):
    import subprocess,sys,os
    c,h=login(app);uploaded=photo(c,h)
    state=get_content(c);state['heroTagline']='Archive original';state['cases']=[dict(title='Archive case',description='Test fixture',image=uploaded.json['url'])]
    saved=save(c,h,state).json['content'];archive=tmp_path/'download.zip';archive.write_bytes(c.get('/api/export').data)
    later=copy.deepcopy(saved);later['heroTagline']='Later edit';assert save(c,h,later).status_code==200
    env={**os.environ,'DATA_DIR':app.config['DATA_DIR'],'REQUIRE_PERSISTENT_DISK':'0'}
    r=subprocess.run([sys.executable,'-m','cms.manage','restore-archive',str(archive),'--replace'],env=env,capture_output=True,text=True,timeout=20)
    assert r.returncode==0,r.stderr
    assert get_content(c)['heroTagline']=='Archive original'
    assert c.get('/api/admin').status_code==401
    assert list(tmp_path.glob('before-import-*.sqlite3'))
    malicious=tmp_path/'malicious.zip'
    with zipfile.ZipFile(malicious,'w') as z:
        z.writestr('content.json',json.dumps(saved));z.writestr('backups.json','[]');z.writestr('media/../../evil','bad')
    r=subprocess.run([sys.executable,'-m','cms.manage','restore-archive',str(malicious),'--replace'],env=env,capture_output=True,text=True,timeout=20)
    assert r.returncode!=0;assert not (tmp_path.parent/'evil').exists();assert get_content(c)['heroTagline']=='Archive original'
