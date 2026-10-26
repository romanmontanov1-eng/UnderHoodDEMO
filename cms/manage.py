"""Offline maintenance: run on the CMS service, with its persistent disk mounted."""
import argparse,getpass,io,json,os,re,secrets,zipfile
from pathlib import Path
from werkzeug.security import generate_password_hash
from PIL import Image
from cms.app import create_app,MEDIA,stamp


def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('reset-password')
    restore=sub.add_parser('restore-archive');restore.add_argument('archive');restore.add_argument('--replace',action='store_true',required=True)
    args=parser.parse_args();app=create_app();data=Path(app.config['DATA_DIR'])
    if args.action=='reset-password':
        password=getpass.getpass('New password (12–128 characters): ');confirmation=getpass.getpass('Repeat password: ')
        if password!=confirmation or not 12<=len(password)<=128:raise SystemExit('Passwords must match and contain 12–128 characters.')
        with app.db_connect() as db:
            db.execute('BEGIN IMMEDIATE');db.execute('UPDATE account SET password=?,must_change=0 WHERE id=1',(generate_password_hash(password),));db.execute('DELETE FROM sessions')
        print('Password changed. All existing sessions ended.');return
    # Restore only while the web process is stopped. Never extract arbitrary ZIP paths.
    with zipfile.ZipFile(args.archive) as z:
        if sum(f.file_size for f in z.infolist())>700*1024*1024 or len(z.infolist())>2000:raise SystemExit('Archive exceeds limits.')
        if z.getinfo('content.json').file_size>512*1024 or z.getinfo('backups.json').file_size>16*1024*1024:raise SystemExit('Invalid JSON size.')
        live=json.loads(z.read('content.json'));history=json.loads(z.read('backups.json'))
        if not isinstance(history,list) or len(history)>30:raise SystemExit('At most 30 history entries allowed.')
        copied=[]
        try:
            with app.db_connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for entry in z.infolist():
                    if entry.filename in ['content.json','backups.json','README.txt']:continue
                    if not entry.filename.startswith('media/') or not MEDIA.fullmatch('/api/'+entry.filename):raise ValueError('Invalid media path')
                    if entry.file_size>1200*1024:raise ValueError('Photo exceeds limit')
                    name=entry.filename[6:];blob=z.read(entry)
                    with Image.open(io.BytesIO(blob)) as image:
                        if image.format!='WEBP' or max(image.size)>1920:raise ValueError('Invalid image')
                        image.verify()
                    path=data/'media'/name
                    if path.exists():
                        if path.read_bytes()!=blob:raise ValueError('Media collision')
                    else:
                        with open(path,'xb') as f:f.write(blob);f.flush();os.fsync(f.fileno())
                        copied.append(path)
                    db.execute('INSERT OR IGNORE INTO media VALUES(?,?,?)',(name,len(blob),0))
                normalized=[]
                for c in [live,*history]:
                    value=app.validate_content(c,db)
                    if not isinstance(c.get('revision'),str) or not re.fullmatch(r'[a-f0-9-]{36}',c['revision']):raise ValueError('Invalid revision')
                    value.update(revision=c['revision'],updatedAt=c.get('updatedAt',stamp()));normalized.append(value)
                # Retain the entire previous database using SQLite's consistent backup API.
                import sqlite3
                backup=data/('before-import-'+secrets.token_hex(6)+'.sqlite3')
                source=app.db_connect();destination=sqlite3.connect(backup)
                try:source.backup(destination)
                finally:source.close();destination.close()
                db.execute('UPDATE content SET body=? WHERE id=1',(json.dumps(normalized[0],ensure_ascii=False),));db.execute('DELETE FROM backups')
                for c in normalized[1:]:db.execute('INSERT INTO backups VALUES(?,?,?)',(c['revision'],c['updatedAt'],json.dumps(c,ensure_ascii=False)))
                db.execute('DELETE FROM sessions')
        except:
            for path in copied:path.unlink(missing_ok=True)
            raise
    print('Content, history and photographs restored. Login credentials preserved; sessions ended.')

if __name__=='__main__':main()
