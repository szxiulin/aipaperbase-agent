from __future__ import annotations

import json
import secrets
import sqlite3
from backend.collections import service

SCHEMA = '''CREATE TABLE IF NOT EXISTS research_records (
 record_id TEXT PRIMARY KEY, kind TEXT NOT NULL, data_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);'''
KINDS = {'reading', 'note', 'progress', 'comparison', 'watch', 'chat_scope'}


def list_records(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='research_records'").fetchone():
        return []
    return [dict(record_id=r['record_id'], kind=r['kind'], data=json.loads(r['data_json']),
                 created_at=r['created_at'], updated_at=r['updated_at'])
            for r in conn.execute('SELECT * FROM research_records ORDER BY updated_at DESC, rowid DESC')]


def save(conn, kind, data, record_id=''):
    if kind not in KINDS or not isinstance(data, dict):
        raise ValueError('研究资料类型或内容无效')
    if len(json.dumps(data, ensure_ascii=False)) > 200000:
        raise ValueError('单份研究资料过大，请拆分保存')
    if kind == 'reading':
        if data.get('status') not in ('unread', 'reading', 'read') or not data.get('entity_id'):
            raise ValueError('请选择论文及阅读进度')
        record_id = 'reading:' + data['entity_id']
    if kind == 'progress':
        if not data.get('collection_id'): raise ValueError('请先选择一个集合')
        record_id = 'progress:' + data['collection_id']
    if kind == 'chat_scope':
        if not data.get('conversation_id') or not isinstance(data.get('entity_ids'), list):
            raise ValueError('请选择对话和论文范围')
        record_id = 'scope:' + data['conversation_id']
    record_id = record_id or 'research_' + secrets.token_hex(8)
    now = service.utc_now()
    conn.execute(SCHEMA)
    with conn:
        existing = conn.execute('SELECT kind FROM research_records WHERE record_id=?',(record_id,)).fetchone()
        if existing and existing['kind'] != kind: raise ValueError('记录类型不匹配')
        conn.execute('''INSERT INTO research_records VALUES (?,?,?,?,?)
        ON CONFLICT(record_id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at''',
                     (record_id, kind, json.dumps(data, ensure_ascii=False), now, now))
    return {'record_id': record_id, 'kind':kind, 'data':data}


def chat_scope(conn, conversation_id):
    for r in list_records(conn):
        if r['kind'] == 'chat_scope' and r['data']['conversation_id'] == conversation_id:
            return r['data']
    return None


# Only portable personal metadata. PDFs, vectors and credentials are deliberately absent.
TABLES = {'main': ['collections','collection_members','collection_organization_runs','research_records'],
          'chat_backup': ['conversations','messages','message_snapshots','ingest_tasks']}


def export_backup(conn, chat_path):
    tables = {}
    if chat_path.exists(): conn.execute('ATTACH DATABASE ? AS chat_backup', (str(chat_path),))
    try:
        for db, names in TABLES.items():
            if db == 'chat_backup' and not chat_path.exists(): continue
            for name in names:
                if conn.execute(f"SELECT 1 FROM {db}.sqlite_master WHERE name=?", (name,)).fetchone():
                    tables[db+'.'+name] = [dict(r) for r in conn.execute(f'SELECT * FROM {db}.{name}')]
        return {'format':'apex-personal-v1','created_at':service.utc_now(),'tables':tables}
    finally:
        if chat_path.exists(): conn.execute('DETACH DATABASE chat_backup')


def restore_backup(conn, payload, chat_path, *, confirm=False):
    if not isinstance(payload, dict) or payload.get('format') != 'apex-personal-v1':
        raise ValueError('不是支持的个人资料备份')
    tables = payload.get('tables', {})
    allowed = {db+'.'+name for db,names in TABLES.items() for name in names}
    if not isinstance(tables, dict) or set(tables)-allowed: raise ValueError('备份包含不支持的数据表')
    for key, rows in tables.items():
        if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows): raise ValueError('备份格式错误')
    if not confirm: return {'counts':{k:len(v) for k,v in tables.items()},'policy':'仅补充缺失记录，同 ID 的当前记录优先；不含 PDF、索引及密钥'}
    from backend.chats import database as chats_db
    from backend.collections import database as collections_db
    collections_db.initialize(conn)
    conn.execute(SCHEMA);conn.commit()
    chat_path.parent.mkdir(parents=True, exist_ok=True)
    with chats_db.connect(chat_path) as chat: chats_db.initialize(chat)
    conn.execute('ATTACH DATABASE ? AS chat_backup',(str(chat_path),))
    added=0
    try:
        with conn:
            for db,names in TABLES.items():
                for name in names:
                    columns = {r['name'] for r in conn.execute(f'PRAGMA {db}.table_info({name})')}
                    for row in tables.get(db+'.'+name,[]):
                        if not row or set(row)-columns: raise ValueError('备份字段不兼容')
                        if name == 'research_records':
                            if row.get('kind') not in KINDS or not isinstance(json.loads(row.get('data_json','null')),dict):
                                raise ValueError('备份中的研究资料格式无效')
                        keys=list(row)
                        sql=f'INSERT OR IGNORE INTO {db}.{name} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})'
                        added += conn.execute(sql,[row[k] for k in keys]).rowcount
        return {'added':added,'policy':'当前同 ID 记录保留'}
    finally: conn.execute('DETACH DATABASE chat_backup')
