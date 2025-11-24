import os
import json
import uuid
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, send_file, abort, Response
import traceback
import sqlite3
USE_SQLITE = True

def db_params():
    return {
        'path': os.path.join(os.path.dirname(__file__), 'quchong_admin.db')
    }

def conn():
    cn = sqlite3.connect(db_params()['path'])
    cn.row_factory = sqlite3.Row
    return cn

def fmt(sql: str):
    s = sql.replace('%s', '?')
    s = s.replace('TINYINT(1)', 'INTEGER').replace('TIMESTAMP', 'DATETIME')
    s = s.replace('NOW()', 'CURRENT_TIMESTAMP').replace('CURRENT_DATETIME', 'CURRENT_TIMESTAMP')
    s = s.replace("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;", '')
    s = s.replace("ENUM('super_admin','admin','operator')", 'TEXT')
    return s

def init_db():
    cn = conn()
    cur = cn.cursor()
    cur.execute(fmt("""
        CREATE TABLE IF NOT EXISTS users (
          id VARCHAR(64) PRIMARY KEY,
          username VARCHAR(64) UNIQUE,
          display_name VARCHAR(128),
          role ENUM('super_admin','admin','operator'),
          parent_id VARCHAR(64) NULL,
          is_active TINYINT(1) DEFAULT 1,
          salt VARCHAR(16),
          password_hash VARCHAR(255),
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """))
    cur.execute(fmt("""
        CREATE TABLE IF NOT EXISTS channels (
          id VARCHAR(64) PRIMARY KEY,
          name VARCHAR(128),
          created_by VARCHAR(64),
          owner_admin_id VARCHAR(64),
          is_active TINYINT(1) DEFAULT 1,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """))
    cur.execute(fmt("""
        CREATE TABLE IF NOT EXISTS customers (
          id VARCHAR(64) PRIMARY KEY,
          phone_raw VARCHAR(64),
          phone_normalized VARCHAR(32),
          phone_hash VARCHAR(64),
          phone_encrypted TEXT,
          sig6 VARCHAR(16),
          channel_id VARCHAR(64),
          owner_operator_id VARCHAR(64),
          owner_admin_id VARCHAR(64),
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """))
    cur.execute(fmt("""
        CREATE TABLE IF NOT EXISTS duplicates (
          id VARCHAR(64) PRIMARY KEY,
          customer_id VARCHAR(64),
          first_owner_id VARCHAR(64),
          duplicate_operator_id VARCHAR(64),
          duplicate_channel_id VARCHAR(64),
          duplicate_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """))
    cn.commit()
    cur.close()
    cn.close()

def ensure_channels_name_not_unique():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='channels'")
        r = cur.fetchone()
        sql = (dict(r)['sql'] if r else '') if USE_SQLITE else (r['sql'] if r else '')
        if sql and 'UNIQUE' in sql.upper():
            cur.execute("ALTER TABLE channels RENAME TO channels_old")
            cur.execute(fmt("""
                CREATE TABLE channels (
                  id VARCHAR(64) PRIMARY KEY,
                  name VARCHAR(128),
                  created_by VARCHAR(64),
                  owner_admin_id VARCHAR(64),
                  is_active TINYINT(1) DEFAULT 1,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """))
            cur.execute("INSERT INTO channels (id,name,created_by,owner_admin_id,is_active,created_at) SELECT id,name,created_by,owner_admin_id,is_active,created_at FROM channels_old")
            cur.execute("DROP TABLE channels_old")
            cn.commit()
    finally:
        cur.close(); cn.close()

def ensure_super_admin():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute(fmt("SELECT id FROM users WHERE role='super_admin' LIMIT 1"))
        r = cur.fetchone()
        if not r:
            salt = 's1'
            uid = rid()
            cur.execute(fmt("INSERT INTO users (id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES (%s,%s,%s,'super_admin',NULL,1,%s,%s,NOW())"),
                        (uid, 'super', '超级管理员', salt, '123456'+salt))
            cn.commit()
    finally:
        cur.close(); cn.close()

def ensure_sig6_column():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute("PRAGMA table_info(customers)")
        cols = [dict(r)['name'] for r in cur.fetchall()]
        if 'sig6' not in cols:
            cur.execute(fmt("ALTER TABLE customers ADD COLUMN sig6 VARCHAR(16)"))
        cur.execute(fmt("UPDATE customers SET sig6=SUBSTR(phone_normalized, CASE WHEN LENGTH(phone_normalized)>6 THEN LENGTH(phone_normalized)-5 ELSE 1 END) WHERE sig6 IS NULL OR sig6=''"))
        cn.commit()
    finally:
        cur.close(); cn.close()

def ensure_migration_normalize_phones():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute(fmt("""
            CREATE TABLE IF NOT EXISTS migrations (
              name VARCHAR(128) PRIMARY KEY,
              applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """))
        cur.execute(fmt("SELECT name FROM migrations WHERE name=%s LIMIT 1"), ('normalize_phones_v1',))
        r = cur.fetchone()
        if not r:
            cur.execute(fmt('SELECT id, phone_raw FROM customers'))
            rows = cur.fetchall()
            updated = 0
            for rr in rows:
                row = dict(rr) if USE_SQLITE else rr
                cid = row['id']
                raw = row.get('phone_raw') or ''
                try:
                    normalized = normalize_phone(raw)
                except Exception:
                    try:
                        cur.execute(fmt('SELECT phone_normalized FROM customers WHERE id=%s'), (cid,))
                        r2 = cur.fetchone()
                        prev = (dict(r2)['phone_normalized'] if r2 else '') if USE_SQLITE else (r2['phone_normalized'] if r2 else '')
                        normalized = normalize_phone(prev or '')
                    except Exception:
                        normalized = None
                if normalized:
                    phone_hash = sha256_hex(normalized)
                    cur.execute(fmt('UPDATE customers SET phone_normalized=%s, phone_hash=%s WHERE id=%s'), (normalized, phone_hash, cid))
                    updated += 1
            cur.execute(fmt("INSERT INTO migrations (name, applied_at) VALUES (%s, NOW())"), ('normalize_phones_v1',))
            cn.commit()
    finally:
        cur.close(); cn.close()

def rid():
    return str(uuid.uuid4())

def normalize_phone(p):
    s = (p or '').strip()
    if any(ch.isalpha() for ch in s):
        raise ValueError('invalid')
    blocked_ranges = [('\u4e00', '\u9fff')]  # Chinese range
    try:
        import re
        if re.search(r"[\u4e00-\u9fff]", s):
            raise ValueError('invalid')
    except Exception:
        pass
    for ch in [' ', '+', '(', ')', '-', '－', '—']:
        s = s.replace(ch, '')
    digits = ''.join(c for c in s if c.isdigit())
    if not 4 <= len(digits) <= 11:
        raise ValueError('invalid')
    return digits

def sig6(digits: str):
    s = ''.join(c for c in (digits or '') if c.isdigit())
    return s if len(s) <= 6 else s[-6:]

def sha256_hex(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

app = Flask(__name__)

@app.after_request
def add_cors(resp):
    origin = request.headers.get('Origin') or '*'
    resp.headers['Access-Control-Allow-Origin'] = origin
    resp.headers['Access-Control-Allow-Credentials'] = 'true'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,DELETE,PATCH,OPTIONS'
    return resp

@app.route('/', methods=['GET'])
def root():
    return jsonify({'status':'ok'})

# Serve frontend UI from the same directory
BASE_DIR = os.path.dirname(__file__)

@app.route('/ui/')
def ui_index():
    try:
        p=os.path.join(BASE_DIR,'index.html')
        with open(p,'rb') as f:
            data=f.read()
        return Response(data, mimetype='text/html')
    except Exception as e:
        return Response('ui error:\n'+traceback.format_exc(), status=500, mimetype='text/plain')

@app.route('/ui')
def ui_index_no_slash():
    try:
        p=os.path.join(BASE_DIR,'index.html')
        with open(p,'rb') as f:
            data=f.read()
        return Response(data, mimetype='text/html')
    except Exception as e:
        return Response('ui error:\n'+traceback.format_exc(), status=500, mimetype='text/plain')

@app.route('/ui/<path:path>')
def ui_static(path: str):
    try:
        p=os.path.join(BASE_DIR, path)
        if not os.path.exists(p):
            return abort(404)
        ext=os.path.splitext(p)[1].lower()
        mt='text/plain'
        if ext in('.html','.htm'): mt='text/html'
        elif ext=='.css': mt='text/css'
        elif ext in('.js','.mjs'): mt='application/javascript'
        elif ext in('.json',): mt='application/json'
        elif ext in('.png','.jpg','.jpeg','.gif','.svg','.ico'): mt='image/'+('svg+xml' if ext=='.svg' else (ext[1:] if ext!='.ico' else 'x-icon'))
        with open(p,'rb') as f:
            data=f.read()
        return Response(data, mimetype=mt)
    except Exception:
        return Response('static error:\n'+traceback.format_exc(), status=500, mimetype='text/plain')

@app.route('/favicon.ico')
def favicon():
    try:
        p=os.path.join(BASE_DIR,'favicon.ico')
        if not os.path.exists(p):
            return abort(404)
        with open(p,'rb') as f:
            data=f.read()
        return Response(data, mimetype='image/x-icon')
    except Exception:
        return Response('favicon error:\n'+traceback.format_exc(), status=500, mimetype='text/plain')

@app.route('/debug')
def debug():
    return jsonify({'base_dir': BASE_DIR, 'index_exists': os.path.exists(os.path.join(BASE_DIR,'index.html'))})

@app.route('/api/users', methods=['GET'])
def get_users():
    cn = conn()
    cur = cn.cursor()
    cur.execute(fmt('SELECT * FROM users'))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    cn.close()
    return jsonify(rows)

@app.route('/api/admins', methods=['POST'])
def create_admin():
    data = request.get_json(force=True)
    username = data.get('username')
    display_name = data.get('display_name')
    password = data.get('password')
    if not username or not display_name or not password:
        return jsonify({'error':'invalid'}), 400
    salt = uuid.uuid4().hex[:4]
    user_id = rid()
    cn = conn()
    cur = cn.cursor()
    cur.execute(fmt("SELECT id FROM users WHERE role='super_admin' LIMIT 1"))
    row = cur.fetchone()
    super_row = dict(row) if row else None
    parent_id = super_row['id'] if super_row else None
    try:
        cur.execute(fmt("INSERT INTO users (id,username,display_name,role,parent_id,is_active,salt,password_hash) VALUES (%s,%s,%s,'admin',%s,1,%s,%s)"),
                    (user_id, username, display_name, parent_id, salt, password + salt))
    except Exception:
        cur.close(); cn.close()
        return jsonify({'error':'exists'}), 409
    cn.commit(); cur.close(); cn.close()
    return jsonify({'id':user_id,'username':username,'display_name':display_name,'role':'admin','parent_id':parent_id,'is_active':1,'salt':salt,'password_hash':password+salt})

@app.route('/api/operators', methods=['POST'])
def create_operator():
    data = request.get_json(force=True)
    username = data.get('username')
    display_name = data.get('display_name')
    password = data.get('password')
    owner_admin_id = data.get('owner_admin_id')
    if not username or not display_name or not password or not owner_admin_id:
        return jsonify({'error':'invalid'}), 400
    salt = uuid.uuid4().hex[:4]
    user_id = rid()
    cn = conn()
    cur = cn.cursor()
    try:
        cur.execute(fmt("INSERT INTO users (id,username,display_name,role,parent_id,is_active,salt,password_hash) VALUES (%s,%s,%s,'operator',%s,1,%s,%s)"),
                    (user_id, username, display_name, owner_admin_id, salt, password + salt))
    except Exception:
        cur.close(); cn.close()
        return jsonify({'error':'exists'}), 409
    cn.commit(); cur.close(); cn.close()
    return jsonify({'id':user_id,'username':username,'display_name':display_name,'role':'operator','parent_id':owner_admin_id,'is_active':1,'salt':salt,'password_hash':password+salt})

@app.route('/api/users/<uid>', methods=['PATCH'])
def patch_user(uid):
    data = request.get_json(force=True)
    is_active = data.get('is_active')
    new_password = data.get('new_password')
    cn = conn()
    cur = cn.cursor()
    if is_active is not None:
        cur.execute(fmt("UPDATE users SET is_active=%s WHERE id=%s"), (1 if is_active else 0, uid))
    if new_password:
        cur.execute(fmt("SELECT salt FROM users WHERE id=%s"), (uid,))
        r = cur.fetchone()
        if not r:
            cur.close(); cn.close()
            return jsonify({'error':'notfound'}), 404
        salt = dict(r)['salt'] if USE_SQLITE else r['salt']
        ph = new_password + salt
        cur.execute(fmt("UPDATE users SET password_hash=%s WHERE id=%s"), (ph, uid))
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok'})

@app.route('/api/admins/<uid>', methods=['DELETE'])
def delete_admin(uid):
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt("SELECT id FROM users WHERE role='operator' AND parent_id=%s"), (uid,))
    ops = [dict(r)['id'] if USE_SQLITE else r['id'] for r in cur.fetchall()]
    cur.execute(fmt("SELECT id FROM channels WHERE owner_admin_id=%s"), (uid,))
    chs = [dict(r)['id'] if USE_SQLITE else r['id'] for r in cur.fetchall()]
    custs = []
    if ops or chs:
        where = "owner_admin_id=%s"
        params = [uid]
        if ops:
            where += " OR owner_operator_id IN ("+ ",".join(["%s"]*len(ops))+")"
            params += ops
        if chs:
            where += " OR channel_id IN ("+ ",".join(["%s"]*len(chs))+")"
            params += chs
        cur.execute(fmt("SELECT id FROM customers WHERE "+where), tuple(params))
        custs = [dict(r)['id'] if USE_SQLITE else r['id'] for r in cur.fetchall()]
    if custs:
        cur.execute(fmt("DELETE FROM duplicates WHERE customer_id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
        cur.execute(fmt("DELETE FROM customers WHERE id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
    if ops:
        cur.execute(fmt("DELETE FROM users WHERE id IN ("+ ",".join(["%s"]*len(ops))+")"), tuple(ops))
    if chs:
        cur.execute(fmt("DELETE FROM channels WHERE id IN ("+ ",".join(["%s"]*len(chs))+")"), tuple(chs))
    cur.execute(fmt("DELETE FROM users WHERE id=%s"), (uid,))
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok'})

@app.route('/api/operators/<uid>', methods=['DELETE'])
def delete_operator(uid):
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt("SELECT id FROM customers WHERE owner_operator_id=%s"), (uid,))
    custs = [dict(r)['id'] if USE_SQLITE else r['id'] for r in cur.fetchall()]
    if custs:
        cur.execute(fmt("DELETE FROM duplicates WHERE customer_id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
        cur.execute(fmt("DELETE FROM customers WHERE id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
    cur.execute(fmt("DELETE FROM users WHERE id=%s"), (uid,))
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok'})

@app.route('/api/channels', methods=['GET'])
def get_channels():
    name = request.args.get('name')
    cn = conn(); cur = cn.cursor()
    if name:
        cur.execute(fmt('SELECT * FROM channels WHERE LOWER(name)=LOWER(%s)'), (name,))
        rows = [dict(r) for r in cur.fetchall()]
    else:
        cur.execute(fmt('SELECT * FROM channels'))
        rows = [dict(r) for r in cur.fetchall()]
    cur.close(); cn.close()
    return jsonify(rows)

@app.route('/api/channels', methods=['POST'])
def create_channel():
    data = request.get_json(force=True)
    name = data.get('name')
    creator_id = data.get('creator_id')
    owner_admin_id = data.get('owner_admin_id')
    if not name:
        return jsonify({'error':'invalid'}), 400
    cid = rid()
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute(fmt("SELECT id,is_active FROM channels WHERE LOWER(name)=LOWER(%s) LIMIT 1"), (name,))
        r = cur.fetchone()
        if r:
            ex = dict(r)
            cur.close(); cn.close()
            return jsonify({'error':'exists','is_active': ex.get('is_active',1)}), 409
        cur.execute(fmt("INSERT INTO channels (id,name,created_by,owner_admin_id,is_active,created_at) VALUES (%s,%s,%s,%s,1,NOW())"),
                    (cid, name, creator_id, owner_admin_id))
        cn.commit()
        return jsonify({'id':cid,'name':name,'created_by':creator_id,'owner_admin_id':owner_admin_id,'is_active':1,'created_at':datetime.now().isoformat()})
    except Exception as e:
        try:
            cn.rollback()
        except Exception:
            pass
        return jsonify({'error':'error','detail':str(e)}), 500
    finally:
        cur.close(); cn.close()

@app.route('/api/channels/<cid>', methods=['PATCH'])
def patch_channel(cid):
    data = request.get_json(force=True)
    is_active = data.get('is_active')
    cn = conn(); cur = cn.cursor()
    if is_active is not None:
        cur.execute(fmt("UPDATE channels SET is_active=%s WHERE id=%s"), (1 if is_active else 0, cid))
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok'})

@app.route('/api/channels/<cid>', methods=['DELETE'])
def delete_channel(cid):
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt("SELECT id FROM customers WHERE channel_id=%s"), (cid,))
    custs = [dict(r)['id'] if USE_SQLITE else r['id'] for r in cur.fetchall()]
    if custs:
        cur.execute(fmt("DELETE FROM duplicates WHERE customer_id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
        cur.execute(fmt("DELETE FROM customers WHERE id IN ("+ ",".join(["%s"]*len(custs))+")"), tuple(custs))
    cur.execute(fmt("DELETE FROM channels WHERE id=%s"), (cid,))
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok'})

@app.route('/api/customers', methods=['GET'])
def get_customers():
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt('SELECT * FROM customers'))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); cn.close()
    return jsonify(rows)

@app.route('/api/duplicates', methods=['GET'])
def get_duplicates():
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt('SELECT * FROM duplicates'))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close(); cn.close()
    return jsonify(rows)

@app.route('/api/cleanup', methods=['POST'])
def cleanup_orphan_duplicates():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute(fmt("DELETE FROM duplicates WHERE customer_id NOT IN (SELECT id FROM customers)"))
        cn.commit()
        return jsonify({'status':'ok'})
    except Exception as e:
        try:
            cn.rollback()
        except Exception:
            pass
        return jsonify({'error':'cleanup_failed','detail':str(e)}), 500
    finally:
        cur.close(); cn.close()

@app.route('/api/customers', methods=['POST'])
def create_customer():
    data = request.get_json(force=True)
    phone_raw = data.get('phone_raw')
    channel_id = data.get('channel_id')
    operator_id = data.get('operator_id')
    if not phone_raw or not channel_id or not operator_id:
        return jsonify({'error':'invalid'}), 400
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt("SELECT parent_id FROM users WHERE id=%s AND role='operator'"), (operator_id,))
    r = cur.fetchone()
    if not r:
        cur.close(); cn.close()
        return jsonify({'error':'auth'}), 403
    admin_id = dict(r)['parent_id'] if USE_SQLITE else r['parent_id']
    try:
        normalized = normalize_phone(phone_raw)
    except Exception:
        cur.close(); cn.close()
        return jsonify({'error':'invalid'}), 400
    phone_hash = sha256_hex(normalized)
    phone_encrypted = normalized.encode('utf-8').hex()
    s6 = sig6(normalized)
    try:
        cust_id = rid()
        cur.execute(fmt("INSERT INTO customers (id,phone_raw,phone_normalized,phone_hash,phone_encrypted,sig6,channel_id,owner_operator_id,owner_admin_id,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())"),
                    (cust_id, phone_raw, normalized, phone_hash, phone_encrypted, s6, channel_id, operator_id, admin_id))
        cn.commit(); cur.close(); cn.close()
        return jsonify({'status':'success'})
    except Exception:
        cur.execute(fmt("SELECT * FROM customers WHERE (phone_hash=%s OR sig6=%s) ORDER BY created_at ASC LIMIT 1"), (phone_hash, s6))
        existing = cur.fetchone()
        if not existing:
            cur.execute(fmt("SELECT * FROM customers WHERE owner_admin_id=%s AND REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(phone_normalized,' ',''),'+',''),'(',''),')',''),'-',''),'－',''),'—','')=%s ORDER BY created_at ASC LIMIT 1"), (admin_id, normalized))
            existing = cur.fetchone()
        if existing:
            dup_id = rid()
            ex = dict(existing)
            ch_name = ''
            try:
                cur.execute(fmt("SELECT name FROM channels WHERE id=%s"), (ex['channel_id'],))
                rch = cur.fetchone(); ch_name = (dict(rch)['name'] if rch else '') if USE_SQLITE else (rch['name'] if rch else '')
            except Exception:
                ch_name = ''
            cur.execute(fmt("INSERT INTO duplicates (id,customer_id,first_owner_id,duplicate_operator_id,duplicate_channel_id,duplicate_at) VALUES (%s,%s,%s,%s,%s,NOW())"),
                        (dup_id, ex['id'], ex['owner_operator_id'], operator_id, channel_id))
            cn.commit(); cur.close(); cn.close()
            return jsonify({'status':'duplicate','existing_owner':ex['owner_operator_id'],'existing_created_at':ex['created_at'],'existing_channel_id':ex['channel_id'],'existing_channel_name': ch_name})
        cn.commit(); cur.close(); cn.close()
        return jsonify({'error':'conflict'}), 409

@app.route('/api/migrate/normalize_phones', methods=['POST'])
def migrate_normalize_phones():
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt('SELECT id, phone_raw FROM customers'))
    rows = cur.fetchall()
    total = len(rows)
    updated = 0
    skipped = 0
    for r in rows:
        row = dict(r) if USE_SQLITE else r
        cid = row['id']
        raw = row.get('phone_raw') or ''
        ok = False
        try:
            normalized = normalize_phone(raw)
            phone_hash = sha256_hex(normalized)
            s6 = sig6(normalized)
            cur.execute(fmt('UPDATE customers SET phone_normalized=%s, phone_hash=%s, sig6=%s WHERE id=%s'), (normalized, phone_hash, s6, cid))
            updated += 1
            ok = True
        except Exception:
            pass
        if not ok:
            try:
                prev = ''
                try:
                    prev = row.get('phone_normalized') or ''
                except Exception:
                    prev = ''
                normalized = normalize_phone(prev)
                phone_hash = sha256_hex(normalized)
                s6 = sig6(normalized)
                cur.execute(fmt('UPDATE customers SET phone_normalized=%s, phone_hash=%s, sig6=%s WHERE id=%s'), (normalized, phone_hash, s6, cid))
                updated += 1
            except Exception:
                skipped += 1
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok','total': total, 'updated': updated, 'skipped': skipped})

@app.route('/api/migrate/dedup_customers', methods=['POST'])
def migrate_dedup_customers():
    cn = conn(); cur = cn.cursor()
    cur.execute(fmt("SELECT sig6 AS s6, COUNT(*) AS cnt FROM customers GROUP BY s6 HAVING COUNT(*)>1"))
    groups = cur.fetchall()
    fixed = 0
    for g in groups:
        s6 = (dict(g)['s6'] if USE_SQLITE else g['s6'])
        cur.execute(fmt("SELECT * FROM customers WHERE sig6=%s ORDER BY created_at ASC"), (s6,))
        rows = cur.fetchall()
        if not rows:
            continue
        first = dict(rows[0]) if USE_SQLITE else rows[0]
        for rr in rows[1:]:
            r = dict(rr) if USE_SQLITE else rr
            dup_id = rid()
            cur.execute(fmt("INSERT INTO duplicates (id,customer_id,first_owner_id,duplicate_operator_id,duplicate_channel_id,duplicate_at) VALUES (%s,%s,%s,%s,%s,%s)"),
                        (dup_id, first['id'], first['owner_operator_id'], r['owner_operator_id'], r['channel_id'], r['created_at']))
            cur.execute(fmt("DELETE FROM customers WHERE id=%s"), (r['id'],))
            fixed += 1
    cn.commit(); cur.close(); cn.close()
    return jsonify({'status':'ok','fixed': fixed})

def ensure_unique_index_customers():
    cn = conn(); cur = cn.cursor()
    try:
        cur.execute(fmt("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_hash ON customers(phone_hash)"))
        try:
            cur.execute(fmt("CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_sig6 ON customers(sig6)"))
        except Exception:
            pass
        cn.commit()
    finally:
        cur.close(); cn.close()

if __name__ == '__main__':
    init_db()
    ensure_channels_name_not_unique()
    ensure_super_admin()
    ensure_sig6_column()
    ensure_migration_normalize_phones()
    try:
        migrate_dedup_customers()
    except Exception:
        pass
    ensure_unique_index_customers()
    app.run(host='127.0.0.1', port=5000)
