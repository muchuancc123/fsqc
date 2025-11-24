# Quchong Admin — Development & Deployment Guide

## Overview
- Backend: Python 3 + Flask (lightweight REST API), SQLite database (single file `quchong_admin.db`).
- Frontend: Vanilla HTML/CSS/JavaScript (no framework/bundling), served as static files by Flask.
- Default port: `5000`, UI entry: `http://<host>:5000/ui/`.

## Structure (key paths)
- `Shared (App)/Resources/admin/server.py`: Flask app and all APIs, with built‑in DB initialization and migrations.
- `Shared (App)/Resources/admin/index.html`: Frontend entry.
- `Shared (App)/Resources/admin/app.css` / `Shared (App)/Resources/admin/app.js`: Styles and logic (no build step; loaded by browser).
- `Shared (App)/Resources/admin/assets/`: Static assets (e.g., login banner `login-banner.png`).
- `Shared (App)/Resources/admin/quchong_admin.db`: SQLite DB file (auto‑created on first run).

## Stack & Dependencies
- Runtime: `Python >= 3.9`
- Required: `Flask`
- Production (pick one):
  - Windows: `waitress` (WSGI server)
  - Linux: `gunicorn` (WSGI server)
- Stdlib used: `sqlite3`, `uuid`, `hashlib`, `datetime`, `os`, `json`, `traceback`
- No Node or frontend build dependencies.

## Install
```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install flask
# Production (one of)
# Windows
pip install waitress
# Linux
pip install gunicorn
```

## Initialize & Run (development)
- Change directory: `cd "Shared (App)/Resources/admin"`
- First run creates and migrates SQLite DB:
```bash
python server.py
# open http://127.0.0.1:5000/ui/
```
- `server.py` `__main__` includes: table creation, required fields/indexes/super admin, historical data normalization & dedup.

> When using WSGI (`gunicorn`/`waitress`), the above init will NOT run automatically. Run `python server.py` once, or call the init functions explicitly (see below).

## Production
### Windows (waitress)
1) `cd "Shared (App)/Resources/admin"`
2) Initialize once:
```bash
python -c "import server; \
server.init_db(); \
server.ensure_channels_name_not_unique(); \
server.ensure_super_admin(); \
server.ensure_sig6_column(); \
server.ensure_migration_normalize_phones(); \
server.migrate_dedup_customers(); \
server.ensure_unique_index_customers()"
```
3) Run WSGI:
```bash
waitress-serve --host=0.0.0.0 --port=5000 server:app
```

### Linux (gunicorn + systemd)
1) `cd "Shared (App)/Resources/admin"`
2) Initialize (same `python -c` as above).
3) Start:
```bash
gunicorn -w 4 -b 0.0.0.0:5000 server:app
```
4) Example `/etc/systemd/system/quchong.service`:
```ini
[Unit]
Description=Quchong Admin (Flask)
After=network.target

[Service]
WorkingDirectory=/opt/quchong/Shared (App)/Resources/admin
Environment="PATH=/opt/quchong/.venv/bin"
ExecStart=/opt/quchong/.venv/bin/gunicorn -w 4 -b 0.0.0.0:5000 server:app
Restart=always

[Install]
WantedBy=multi-user.target
```
Enable & check:
```bash
sudo systemctl daemon-reload
sudo systemctl enable quchong
sudo systemctl start quchong
sudo systemctl status quchong
```

### Optional: Nginx reverse proxy
```nginx
server {
  listen 80;
  server_name example.com;

  location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }
}
```

## Frontend & Static
- UI entry: `/ui/`, static files served by `/ui/<path>` from the same directory.
- Login banner: place image at `Shared (App)/Resources/admin/assets/login-banner.png`. The page auto‑loads it and syncs login card width to the image.

## Configuration
- Frontend API base: `Shared (App)/Resources/admin/app.js:4` `API_BASE` (currently `http://127.0.0.1:5000`). For production:
  ```js
  const API_BASE = location.origin;
  ```
- Port: `5000` (in `server.py` `app.run`), or as specified by WSGI command.

## Database
- Location: `Shared (App)/Resources/admin/quchong_admin.db`
- Backup: copy the file; migrations and indexes are handled during init.

## API brief
- `GET /api/users`
- `POST /api/admins`
- `POST /api/operators`
- `PATCH /api/users/<uid>`
- `DELETE /api/admins/<uid>` (cascade cleanup)
- `GET /api/channels` / `POST /api/channels` / `PATCH /api/channels/<cid>` / `DELETE /api/channels/<cid>`
- `GET /api/customers` / `POST /api/customers`
- `GET /api/duplicates`
- `POST /api/cleanup`
- UI & static: `GET /ui/`, `GET /ui/<path>`

## Security notes
- CORS: currently allows any Origin with credentials; restrict to your domains for production.
- Passwords: sample storage `password + salt`; replace with strong hashing (bcrypt/argon2) for production.
- Server: use `waitress` or `gunicorn` in production; avoid Flask dev server.

## Recent UI tweaks
- Login page centers banner & card; card width syncs to banner.
- System management page lists show two rows with vertical scroll.
- Display uses `display_name` across lists and CSV export.

---
If you need Docker/Compose files or further automation scripts, let me know.
