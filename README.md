# 重粉管理后台 — 开发与部署说明

## 概述
- 后端：Python 3 + Flask（轻量 REST API），数据库使用 SQLite（单文件 `quchong_admin.db`）。
- 前端：原生 HTML/CSS/JavaScript（无打包，无框架），由 Flask 同目录下静态文件直接提供。
- 运行端口：默认 `5000`，UI 入口为 `http://<host>:5000/ui/`。

## 目录结构（关键路径）
- `Shared (App)/Resources/admin/server.py`：Flask 应用与全部接口定义，内置数据库初始化与迁移逻辑。
- `Shared (App)/Resources/admin/index.html`：前端页面入口。
- `Shared (App)/Resources/admin/app.css` / `Shared (App)/Resources/admin/app.js`：前端样式与交互逻辑（无构建步骤）。
- `Shared (App)/Resources/admin/assets/`：静态资源目录（如登录横幅 `login-banner.png`）。
- `Shared (App)/Resources/admin/quchong_admin.db`：SQLite 数据库文件（启动后自动生成在同目录）。

## 技术栈与依赖
- 语言与运行时：`Python >= 3.9`
- 必需依赖（pip）：`Flask`
- 生产依赖（建议其一）：
  - Windows：`waitress`（WSGI 服务器）
  - Linux：`gunicorn`（WSGI 服务器）
- 标准库：`sqlite3`, `uuid`, `hashlib`, `datetime`, `os`, `json`, `traceback`
- 无 Node/前端构建依赖；浏览器直接加载静态资源。

## 安装
```bash
# 可选：创建虚拟环境
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 安装依赖
pip install flask
# 生产建议（选其一）
# Windows
pip install waitress
# Linux
pip install gunicorn
```

## 初始化与开发运行
- 进入目录：`cd "Shared (App)/Resources/admin"`
- 首次运行会在同目录创建并迁移 SQLite 数据库：
```bash
python server.py
# 打开 http://127.0.0.1:5000/ui/
```
- `server.py` 的 `__main__` 中包含：建表、必要字段/索引/超级管理员、历史数据清洗与去重等。

> 如果用 WSGI 启动（如 gunicorn/waitress），上述初始化不会自动执行。需先运行一次 `python server.py` 或在部署脚本中显式调用初始化（见下方“生产部署”）。

## 生产部署
### Windows（waitress）
1) 目录切换：`cd "Shared (App)/Resources/admin"`
2) 先执行一次初始化：
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
3) 以 WSGI 模式运行：
```bash
waitress-serve --host=0.0.0.0 --port=5000 server:app
```

### Linux（gunicorn + systemd 示例）
1) 目录切换：`cd "Shared (App)/Resources/admin"`
2) 初始化（同上面的 `python -c` 调用）。
3) 运行：
```bash
gunicorn -w 4 -b 0.0.0.0:5000 server:app
```
4) `systemd` 单元示例 `/etc/systemd/system/quchong.service`：
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
启用与查看：
```bash
sudo systemctl daemon-reload
sudo systemctl enable quchong
sudo systemctl start quchong
sudo systemctl status quchong
```

### 可选：Nginx 反向代理
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

## 前端访问与静态资源
- UI 入口：`/ui/`，静态资源通过 `/ui/<path>` 直接读取当前目录文件。
- 登录页横幅：将图片放到 `Shared (App)/Resources/admin/assets/login-banner.png`，页面会自动加载并与登录卡片等宽。

## 配置项
- 前端接口基址：`Shared (App)/Resources/admin/app.js:4` 的 `API_BASE`（当前为 `http://127.0.0.1:5000`）。
  - 生产建议改为 `location.origin` 或你的后端基址，例如：
    ```js
    const API_BASE = location.origin;
    ```
- 端口：`5000`（在 `server.py` 的 `app.run`），WSGI 模式由启动命令指定。

## 数据库
- 文件位置：`Shared (App)/Resources/admin/quchong_admin.db`
- 备份：直接复制该文件；初始化阶段自动处理迁移和索引。

## API 概览（简要）
- `GET /api/users` 获取用户
- `POST /api/admins` 创建管理员
- `POST /api/operators` 创建运营
- `PATCH /api/users/<uid>` 修改启用/密码
- `DELETE /api/admins/<uid>` 删除管理员（级联清理其运营、渠道与客户/重复）
- `GET /api/channels` / `POST /api/channels` / `PATCH /api/channels/<cid>` / `DELETE /api/channels/<cid>`
- `GET /api/customers` / `POST /api/customers`
- `GET /api/duplicates`
- `POST /api/cleanup` 清理孤立重复记录（无需在 UI 暴露）
- UI 与静态：`GET /ui/`、`GET /ui/<path>`

## 安全建议
- CORS：默认允许任意 `Origin`；生产建议收紧到你的域名。
- 密码存储：示例为 `password + salt`；生产建议替换为 `bcrypt`/`argon2`。
- 服务器：生产使用 `waitress` 或 `gunicorn`，不要用 Flask 内置开发服务器。

## 变更提示
- 登录页图片与登录卡片居中排布，卡片宽度自适应图片显示宽度。
- “系统管理”页的列表（渠道与我的运营）两行高度且溢出滚动。
- 显示统一使用昵称 `display_name`（列表与导出 CSV）。

---
更多部署脚本（Docker、Compose）或英文版说明可继续补充。
