import os
import sqlite3
import time
import secrets
import hmac
import hashlib
from base64 import b64encode, b64decode
from uuid import uuid4
from fastapi import FastAPI, Request, Response, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import jwt

PORT=int(os.getenv('PORT','8020'))
AES_KEY=os.getenv('AES_KEY')
if AES_KEY is None:
    AES_KEY=b64encode(secrets.token_bytes(32)).decode()
AES_KEY_BYTES=b64decode(AES_KEY)
PEPPER=os.getenv('PEPPER') or b64encode(secrets.token_bytes(32)).decode()
PEPPER_BYTES=b64decode(PEPPER)
JWT_SECRET=os.getenv('JWT_SECRET') or b64encode(secrets.token_bytes(32)).decode()

app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_credentials=True,allow_methods=['*'],allow_headers=['*'])

data_dir=os.path.join(os.path.dirname(__file__),'data')
os.makedirs(data_dir,exist_ok=True)
db_path=os.path.join(data_dir,'app.db')

# 静态资源托管（admin 前端）
static_root=os.path.join(os.path.dirname(__file__),'..','Shared (App)','Resources','admin')
if os.path.isdir(static_root):
    app.mount('/', StaticFiles(directory=static_root, html=True), name='static')

def conn():
    c=sqlite3.connect(db_path)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=conn()
    c.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT UNIQUE, display_name TEXT, role TEXT, parent_id TEXT, is_active INTEGER, salt TEXT, password_hash TEXT, created_at INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS channels (id TEXT PRIMARY KEY, name TEXT UNIQUE, created_by TEXT, is_active INTEGER, created_at INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS customers (id TEXT PRIMARY KEY, phone_hash TEXT, phone_encrypted TEXT, channel_id TEXT, owner_operator_id TEXT, owner_admin_id TEXT, created_at INTEGER)')
    c.execute('CREATE TABLE IF NOT EXISTS duplicates (id TEXT PRIMARY KEY, customer_id TEXT, first_owner_id TEXT, duplicate_operator_id TEXT, duplicate_channel_id TEXT, duplicate_at INTEGER)')
    r=c.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c']
    if r==0:
        super_id=str(uuid4())
        admin_id=str(uuid4())
        op_id=str(uuid4())
        s1=secrets.token_hex(8)
        s2=secrets.token_hex(8)
        s3=secrets.token_hex(8)
        h1=hashlib.pbkdf2_hmac('sha256','123456'.encode(),s1.encode(),100000).hex()
        h2=hashlib.pbkdf2_hmac('sha256','123456'.encode(),s2.encode(),100000).hex()
        h3=hashlib.pbkdf2_hmac('sha256','123456'.encode(),s3.encode(),100000).hex()
        ts=int(time.time()*1000)
        c.execute('INSERT INTO users(id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(super_id,'super','超级管理员','super_admin',None,1,s1,h1,ts))
        c.execute('INSERT INTO users(id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(admin_id,'adminA','管理员A','admin',super_id,1,s2,h2,ts))
        c.execute('INSERT INTO users(id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(op_id,'opA','运营A','operator',admin_id,1,s3,h3,ts))
        ch_id=str(uuid4())
        c.execute('INSERT INTO channels(id,name,created_by,is_active,created_at) VALUES(?,?,?,?,?)',(ch_id,'默认渠道',super_id,1,ts))
    c.commit()
    c.close()

def normalize_phone(s):
    s=str(s or '').strip()
    digits=''.join(ch for ch in s if ch.isdigit())
    if len(digits)<4 or len(digits)>11:
        raise HTTPException(status_code=400,detail='invalid')
    return digits

def phone_hmac(text):
    return hmac.new(PEPPER_BYTES,text.encode(),'sha256').hexdigest()

def phone_encrypt(text):
    iv=secrets.token_bytes(12)
    a=AESGCM(AES_KEY_BYTES)
    ct=a.encrypt(iv,text.encode(),None)
    return b64encode(iv+ct).decode()

def auth_user(req:Request):
    t=req.cookies.get('token')
    if not t:
        raise HTTPException(status_code=401,detail='unauth')
    try:
        p=jwt.decode(t,JWT_SECRET,algorithms=['HS256'])
    except Exception:
        raise HTTPException(status_code=401,detail='unauth')
    c=conn()
    u=c.execute('SELECT id,username,display_name,role,parent_id,is_active,created_at FROM users WHERE id=?',(p['id'],)).fetchone()
    c.close()
    if not u:
        raise HTTPException(status_code=401,detail='unauth')
    return dict(u)

@app.post('/api/login')
def login(body:dict,resp:Response):
    u=body.get('username')
    p=body.get('password')
    if not u or not p:
        raise HTTPException(status_code=400,detail='invalid')
    c=conn()
    row=c.execute('SELECT * FROM users WHERE username=? AND is_active=1',(u,)).fetchone()
    c.close()
    if not row:
        raise HTTPException(status_code=400,detail='invalid')
    h=hashlib.pbkdf2_hmac('sha256',p.encode(),row['salt'].encode(),100000).hex()
    if h!=row['password_hash']:
        raise HTTPException(status_code=400,detail='invalid')
    token=jwt.encode({'id':row['id'],'role':row['role'],'exp':int(time.time())+7200},JWT_SECRET,algorithm='HS256')
    resp.set_cookie('token',token,httponly=True,samesite='lax')
    return {'ok':True,'role':row['role'],'display_name':row['display_name']}

@app.post('/api/logout')
def logout(resp:Response):
    resp.delete_cookie('token')
    return {'ok':True}

@app.get('/api/me')
def me(user:dict=Depends(auth_user)):
    return user

@app.get('/api/channels')
def channels(user:dict=Depends(auth_user)):
    c=conn()
    rows=c.execute('SELECT id,name,is_active,created_at FROM channels WHERE is_active=1 ORDER BY created_at DESC').fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.post('/api/channels')
def create_channel(body:dict,user:dict=Depends(auth_user)):
    if user['role'] not in ['super_admin','admin']:
        raise HTTPException(status_code=403,detail='forbidden')
    name=body.get('name')
    if not name:
        raise HTTPException(status_code=400,detail='invalid')
    c=conn()
    ex=c.execute('SELECT 1 FROM channels WHERE name=?',(name,)).fetchone()
    if ex:
        c.close()
        raise HTTPException(status_code=409,detail='exists')
    id=str(uuid4())
    c.execute('INSERT INTO channels(id,name,created_by,is_active,created_at) VALUES(?,?,?,?,?)',(id,name,user['id'],1,int(time.time()*1000)))
    c.commit()
    c.close()
    return {'id':id,'name':name}

@app.get('/api/users/admins')
def admins(user:dict=Depends(auth_user)):
    if user['role']!='super_admin':
        raise HTTPException(status_code=403,detail='forbidden')
    c=conn()
    rows=c.execute('SELECT id,username,display_name,role,is_active,created_at FROM users WHERE role=? ORDER BY created_at DESC',('admin',)).fetchall()
    c.close()
    return [dict(r) for r in rows]

@app.get('/api/users/operators')
def operators(adminId:Optional[str]=None,user:dict=Depends(auth_user)):
    c=conn()
    if user['role']=='admin':
        rows=c.execute('SELECT id,username,display_name,role,parent_id,is_active,created_at FROM users WHERE role=? AND parent_id=? ORDER BY created_at DESC',('operator',user['id'])).fetchall()
        c.close()
        return [dict(r) for r in rows]
    if user['role']=='super_admin':
        if adminId:
            rows=c.execute('SELECT id,username,display_name,role,parent_id,is_active,created_at FROM users WHERE role=? AND parent_id=? ORDER BY created_at DESC',('operator',adminId)).fetchall()
        else:
            rows=c.execute('SELECT id,username,display_name,role,parent_id,is_active,created_at FROM users WHERE role=? ORDER BY created_at DESC',('operator',)).fetchall()
        c.close()
        return [dict(r) for r in rows]
    c.close()
    raise HTTPException(status_code=403,detail='forbidden')

@app.post('/api/users/admin')
def create_admin(body:dict,user:dict=Depends(auth_user)):
    if user['role']!='super_admin':
        raise HTTPException(status_code=403,detail='forbidden')
    username=body.get('username')
    display_name=body.get('display_name')
    password=body.get('password')
    if not username or not display_name or not password:
        raise HTTPException(status_code=400,detail='invalid')
    c=conn()
    ex=c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone()
    if ex:
        c.close()
        raise HTTPException(status_code=409,detail='exists')
    id=str(uuid4())
    salt=secrets.token_hex(8)
    ph=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),100000).hex()
    c.execute('INSERT INTO users(id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(id,username,display_name,'admin',user['id'],1,salt,ph,int(time.time()*1000)))
    c.commit()
    c.close()
    return {'id':id,'username':username,'display_name':display_name}

@app.post('/api/users/operator')
def create_operator(body:dict,user:dict=Depends(auth_user)):
    if user['role'] not in ['super_admin','admin']:
        raise HTTPException(status_code=403,detail='forbidden')
    username=body.get('username')
    display_name=body.get('display_name')
    password=body.get('password')
    owner_admin_id=body.get('owner_admin_id')
    if user['role']=='admin':
        owner_admin_id=user['id']
    if not username or not display_name or not password or not owner_admin_id:
        raise HTTPException(status_code=400,detail='invalid')
    c=conn()
    ex=c.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone()
    if ex:
        c.close()
        raise HTTPException(status_code=409,detail='exists')
    id=str(uuid4())
    salt=secrets.token_hex(8)
    ph=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),100000).hex()
    c.execute('INSERT INTO users(id,username,display_name,role,parent_id,is_active,salt,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(id,username,display_name,'operator',owner_admin_id,1,salt,ph,int(time.time()*1000)))
    c.commit()
    c.close()
    return {'id':id,'username':username,'display_name':display_name,'parent_id':owner_admin_id}

@app.patch('/api/users/{uid}/password')
def change_password(uid:str,body:dict,user:dict=Depends(auth_user)):
    new_password=body.get('new_password')
    if not new_password or len(new_password)<6:
        raise HTTPException(status_code=400,detail='weak')
    c=conn()
    t=c.execute('SELECT * FROM users WHERE id=? AND is_active=1',(uid,)).fetchone()
    if not t:
        c.close()
        raise HTTPException(status_code=404,detail='notfound')
    if user['role']=='admin':
        if not (t['role']=='operator' and t['parent_id']==user['id']):
            c.close()
            raise HTTPException(status_code=403,detail='forbidden')
    salt=secrets.token_hex(8)
    ph=hashlib.pbkdf2_hmac('sha256',new_password.encode(),salt.encode(),100000).hex()
    c.execute('UPDATE users SET salt=?, password_hash=? WHERE id=?',(salt,ph,uid))
    c.commit()
    c.close()
    return {'ok':True}

@app.post('/api/customers')
def create_customer(body:dict,user:dict=Depends(auth_user)):
    phone_raw=body.get('phone_raw')
    channel_id=body.get('channel_id')
    operator_id=body.get('operator_id')
    if not phone_raw or not channel_id or not operator_id:
        raise HTTPException(status_code=400,detail='invalid')
    c=conn()
    op=c.execute('SELECT * FROM users WHERE id=? AND role=? AND is_active=1',(operator_id,'operator')).fetchone()
    if not op or not op['parent_id']:
        c.close()
        raise HTTPException(status_code=403,detail='auth')
    if user['role']=='operator' and user['id']!=op['id']:
        c.close()
        raise HTTPException(status_code=403,detail='forbidden')
    if user['role']=='admin' and op['parent_id']!=user['id']:
        c.close()
        raise HTTPException(status_code=403,detail='forbidden')
    normalized=normalize_phone(phone_raw)
    phash=phone_hmac(normalized)
    pencrypt=phone_encrypt(normalized)
    admin_id=op['parent_id']
    existing=c.execute('SELECT * FROM customers WHERE phone_hash=? AND owner_admin_id=?',(phash,admin_id)).fetchone()
    if existing:
        dup_id=str(uuid4())
        c.execute('INSERT INTO duplicates(id,customer_id,first_owner_id,duplicate_operator_id,duplicate_channel_id,duplicate_at) VALUES(?,?,?,?,?,?)',(dup_id,existing['id'],existing['owner_operator_id'],op['id'],channel_id,int(time.time()*1000)))
        owner=c.execute('SELECT id,username,display_name FROM users WHERE id=?',(existing['owner_operator_id'],)).fetchone()
        c.commit()
        c.close()
        return {'status':'duplicate','existing_owner':dict(owner) if owner else None,'existing_created_at':existing['created_at']}
    cid=str(uuid4())
    c.execute('INSERT INTO customers(id,phone_hash,phone_encrypted,channel_id,owner_operator_id,owner_admin_id,created_at) VALUES(?,?,?,?,?,?,?)',(cid,phash,pencrypt,channel_id,op['id'],admin_id,int(time.time()*1000)))
    c.commit()
    c.close()
    return {'status':'success'}

@app.get('/api/customers')
def list_customers(q:Optional[str]=None,page:int=1,size:int=20,user:dict=Depends(auth_user)):
    c=conn()
    base='SELECT c.id,c.channel_id,c.owner_operator_id,c.owner_admin_id,c.created_at,u.username AS op_username,a.username AS admin_username,ch.name AS channel_name FROM customers c JOIN users u ON c.owner_operator_id=u.id LEFT JOIN users a ON c.owner_admin_id=a.id LEFT JOIN channels ch ON c.channel_id=ch.id'
    wh=[]
    params=[]
    if user['role']=='admin':
        wh.append('c.owner_admin_id=?')
        params.append(user['id'])
    if user['role']=='operator':
        wh.append('c.owner_operator_id=?')
        params.append(user['id'])
    if q:
        ql='%'+q.lower()+'%'
        wh.append('(LOWER(ch.name) LIKE ? OR LOWER(u.username) LIKE ? OR LOWER(a.username) LIKE ?)')
        params.extend([ql,ql,ql])
    if wh:
        base+=' WHERE '+(' AND '.join(wh))
    base+=' ORDER BY c.created_at DESC'
    off=(page-1)*size
    base+=f' LIMIT {size} OFFSET {off}'
    rows=c.execute(base,params).fetchall()
    c.close()
    return [dict(r) for r in rows]

init_db()

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='0.0.0.0',port=PORT)
