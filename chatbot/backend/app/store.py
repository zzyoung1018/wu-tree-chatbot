"""Application state only. Never writes to the source knowledge database."""
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path, max_sessions=5000):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.max_sessions = max_sessions
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, focus TEXT);
            CREATE INDEX IF NOT EXISTS owner_conversations ON conversations(owner,updated);
            CREATE TABLE IF NOT EXISTS messages(
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                request_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, result TEXT, created REAL NOT NULL,
                UNIQUE(conversation_id,request_id,role));
            CREATE TABLE IF NOT EXISTS feedback(
                message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                rating TEXT NOT NULL, reason TEXT NOT NULL, note TEXT NOT NULL, updated REAL NOT NULL);
            ''')
            db.execute("UPDATE messages SET status='error',text='服务重启中断了这次回答，请重新发送问题。' WHERE status='processing'")
        self.path.chmod(0o600)

    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                yield db

    def session(self, token):
        if not token:
            return None
        with self.db() as db:
            row = db.execute('SELECT owner FROM sessions WHERE token_hash=? AND expires>?',
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return row['owner'] if row else None

    def new_session(self):
        token, owner = secrets.token_urlsafe(32), uid()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM sessions WHERE expires <= ?', (time.time(),))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 14 * 86400))
            db.execute('''DELETE FROM sessions WHERE token_hash IN (
                SELECT token_hash FROM sessions ORDER BY expires, rowid
                LIMIT max(0, (SELECT count(*) FROM sessions) - ?))''', (self.max_sessions,))
        return token, owner

    def logout(self, token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def list_conversations(self, owner):
        with self.db() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,title,created,updated FROM conversations WHERE owner=? ORDER BY updated DESC LIMIT 100', (owner,))]

    def create_conversation(self, owner):
        item = dict(id=uid(), owner=owner, title='新的对话', created=time.time(), updated=time.time(), focus=None)
        with self.db() as db:
            db.execute('INSERT INTO conversations VALUES(:id,:owner,:title,:created,:updated,:focus)', item)
        return item

    def conversation(self, owner, cid):
        with self.db() as db:
            row = db.execute('SELECT * FROM conversations WHERE id=? AND owner=?', (cid, owner)).fetchone()
        if not row:
            raise KeyError('conversation')
        value = dict(row)
        value['focus'] = json.loads(value['focus']) if value['focus'] else None
        return value

    def delete(self, owner, cid):
        self.conversation(owner, cid)
        with self.db() as db:
            if db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND status='processing'", (cid,)).fetchone():
                raise ValueError('请等当前回答完成后再删除对话。')
            db.execute('DELETE FROM conversations WHERE id=? AND owner=?', (cid, owner))

    def messages(self, owner, cid):
        self.conversation(owner, cid)
        with self.db() as db:
            rows = db.execute('''SELECT m.*, f.rating, f.reason, f.note FROM messages m
                LEFT JOIN feedback f ON m.id=f.message_id WHERE conversation_id=? ORDER BY m.created,m.rowid''', (cid,)).fetchall()
        return [self.decode(r) for r in rows]

    @staticmethod
    def decode(row):
        d = dict(row)
        d['result'] = json.loads(d['result']) if d.get('result') else None
        return d

    def begin_answer(self, owner, cid, request_id, question):
        self.conversation(owner, cid)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute("SELECT * FROM messages WHERE conversation_id=? AND request_id=? AND role='assistant'", (cid, request_id)).fetchone()
            if existing:
                prior = db.execute("SELECT text FROM messages WHERE conversation_id=? AND request_id=? AND role='user'", (cid, request_id)).fetchone()
                if not prior or prior[0] != question:
                    raise ValueError('请求编号已用于其他问题。')
                return self.decode(existing), False
            if db.execute("SELECT 1 FROM messages WHERE conversation_id=? AND status='processing'", (cid,)).fetchone():
                raise ValueError('这个对话正在回答中，请稍候。')
            now, aid = time.time(), uid()
            db.execute('INSERT INTO messages(id,conversation_id,request_id,role,text,status,created) VALUES(?,?,?,?,?,?,?)',
                       (uid(), cid, request_id, 'user', question, 'complete', now))
            db.execute('INSERT INTO messages(id,conversation_id,request_id,role,status,created) VALUES(?,?,?,?,?,?)',
                       (aid, cid, request_id, 'assistant', 'processing', now + .001))
            db.execute("UPDATE conversations SET updated=?,title=CASE WHEN title='新的对话' THEN ? ELSE title END WHERE id=?",
                       (now, question[:30], cid))
        return self.message(aid), True

    def message(self, mid):
        with self.db() as db:
            row = db.execute('SELECT * FROM messages WHERE id=?', (mid,)).fetchone()
        return self.decode(row) if row else None

    def processing_message(self, owner, cid):
        self.conversation(owner, cid)
        with self.db() as db:
            row = db.execute("SELECT id FROM messages WHERE conversation_id=? AND status='processing'", (cid,)).fetchone()
        return row['id'] if row else None

    def finish(self, mid, result, status='complete'):
        with self.db() as db:
            row = db.execute('SELECT conversation_id FROM messages WHERE id=?', (mid,)).fetchone()
            if not row:
                return
            db.execute('UPDATE messages SET status=?,text=?,result=? WHERE id=?',
                       (status, result.get('text', ''), json.dumps(result, ensure_ascii=False), mid))
            db.execute('UPDATE conversations SET updated=? WHERE id=?', (time.time(), row[0]))
            if result.get('focus'):
                db.execute('UPDATE conversations SET focus=? WHERE id=?', (json.dumps(result['focus'], ensure_ascii=False), row[0]))
            elif result.get('clear_focus'):
                db.execute('UPDATE conversations SET focus=NULL WHERE id=?', (row[0],))

    def feedback(self, owner, mid, rating, reason, note):
        if rating == 'good':
            reason = note = ''
        with self.db() as db:
            row = db.execute('''SELECT 1 FROM messages m JOIN conversations c ON m.conversation_id=c.id
                WHERE m.id=? AND c.owner=? AND m.role='assistant' AND m.status='complete' ''', (mid, owner)).fetchone()
            if not row:
                raise KeyError('message')
            db.execute('INSERT OR REPLACE INTO feedback VALUES(?,?,?,?,?)', (mid, rating, reason, note, time.time()))
