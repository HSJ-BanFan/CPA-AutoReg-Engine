<div align="center">

# CPA-AutoReg-Engine
---
<img src="https://github.com/user-attachments/assets/4106fb61-5359-4d05-b666-9aa3e6e7a0f3" width="200" />

一款专为 OpenAI 账号池设计的高性能管理面板，集成全自动批量注册、CLIProxyAPI 平台账号池实时监控与智能维护系统。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)

</div>

---

## 核心特性

- **高级指纹伪装与风控对抗**：
  - **Camoufox 真实浏览器隔离引擎**：采用底层级浏览器指纹伪装（涵盖 WebGL、Canvas、WebRTC），直接通过 Web UI 模拟人类自动化注册，完全绕过仅基于 HTTP 协议的 API 风控检测。
  - **子进程沙盒**：浏览器注册任务隔离在独立子进程中运行，彻底解决浏览器核心崩溃导致主服务假死的问题。
- **多模式并发注册**：
  - **并行模式**：支持最高 50 线程同时发起 1000 条注册任务，极速扩充账号规模。
  - **流水线模式**：支持设置随机启动间隔，模拟真实用户行为，规避风控。
- **无缝对接下游号池 (webchat2api)**：
  - 注册成功的账号及其完整 Session/Token 能够自动推送到下游的 `webchat2api` 或对应的账号管理网关，实现打码、注册、入池的全自动闭环。
- **CLIProxyAPI 账号自动巡检**：
  - 支持 **401 认证失效检测** 与 **Quota 额度耗尽检测**。
  - 自动根据配置执行 **物理删除** 异常账号，保持账号池可用性。
- **智能自动补货系统**：
  - **实时号池监控**：当 CPA 在线账号低于阈值时，自动触发补货。
  - **自动任务挂载**：补货任务自动在首页控制台展示进度，无需人工干预。
  - **详细补货日志**：在检测历史中清晰标注触发补货的具体方式、邮箱服务及补货数量。
- **全栈监控面板**：
  - **实时日志流**：基于 WebSocket 的逐行日志推送，随时监控注册细节。
  - **进度可视化**：直接显示成功、失败、剩余数与进度百分比。
- **多邮箱生态支持**：集成 Outlook、TempMail、CloudMail 邮箱服务。
- **紧急防御与异常熔断**：
  - **动态阈值保护**：巡检时发现就绪账号比例低于设定值（如 50%，可配置）时，自动触发紧急防御，随机清理半量账号。
  - **自定义冷却重试**：紧急防御触发后，系统将进入预设的冷却期（如 5 分钟，可配置）后重新开始检测。
  - **异常账号全自动清理**：自动移除检测过程中产生 Network Error 或 API 报错的“僵尸”账号。

## 集成 CLIProxyAPI 管理
- **[CLI Proxy API Management Center](https://github.com/router-for-me/CLIProxyAPI)**


## 技术栈

- **后端**: `Python 3.10+`, `FastAPI`, `SQLAlchemy`
- **前端**: `Vanilla JS`, `WebSocket`
- **数据**: `SQLite` / `PostgreSQL`
- **并发**: `asyncio` + `ThreadPoolExecutor`

## 快速开始

### 1. 环境准备
确保已安装 Python 3.10 或更高版本。

```bash
# 使用 uv（推荐）
uv sync

# 或使用 pip
pip install -r requirements.txt
```

### 2. 配置环境
复制 `.env.example` 为 `.env` 后按需修改:

```bash
cp .env.example .env
```

### 3. 运行项目
```bash
python webui.py
```

访问 `http://localhost:8000` 即可进入管理面板。

进入系统设置页添加 CPA 服务，即可使用。

## 快速上手：从 0 跑通一次注册

推荐先跑通一次单账号注册，再开启批量任务。

### 第一步：启动 Web UI

推荐首次启动时显式指定端口和登录密码：

```bash
python webui.py --host 127.0.0.1 --port 8001 --access-password your_password
```

浏览器打开：

```text
http://127.0.0.1:8001/login
```

用启动命令里的 `your_password` 登录。登录不上时，先确认当前启动命令里的端口和密码是否一致；如果页面显示 `ERR_CONNECTION_REFUSED`，说明 Web UI 没启动或端口不对。

### 第二步：配置代理

OpenAI 注册通常需要可访问 OpenAI/Auth 域名的代理。进入「设置」页面填写：

```text
启用代理：开启
代理类型：http
代理主机：127.0.0.1
代理端口：你的本地代理端口，例如 7897
```

保存后先跑单账号测试。代理不通时，注册日志常见 `Operation timed out`、`host 主机名无法解析` 或网络连接失败。

### 第三步：配置邮箱服务

推荐使用以下两种方式之一：

- **IMAP 模式（首选）**：有域名但不想自建临时邮箱：用 Cloudflare Email Routing 转发到 Gmail，再配置 `imap_mail`。此方式稳定性高，适合批量注册。
- **Cloudflare Temp Email**：已部署自建 Cloudflare Temp Email，并绑定了未被风控的高质量独立域名：用 `cloudflare_temp_email`。

如果选择 Gmail 转发方案，流程是：

```text
你的域名邮箱地址 → Cloudflare Email Routing → Gmail 收件箱 → 本项目通过 Gmail IMAP 读取验证码
```

Gmail 侧需要开启两步验证并创建「应用专用密码」，本项目里填写这个应用专用密码，不要填写 Google 登录密码。

如果选择 Cloudflare Temp Email，先部署开源项目：

```text
https://github.com/dreamhunter2333/cloudflare_temp_email
```

部署完成后，在「邮箱服务」页面新增服务：

```json
{
  "base_url": "https://your-mail-service.example.com",
  "domain": "example.com",
  "timeout": 120,
  "poll_interval": 3,
  "mail_limit": 10
}
```

这里只填部署后的服务地址和收信域名，不需要填写 JWT、邮箱密码或 admin token。

### 第四步：跑一次单账号注册

进入「注册」页面：

1. 选择刚添加的邮箱服务。
2. 选择单账号注册。
3. 启动任务。
4. 观察首页日志。

成功时通常能看到：

```text
成功创建邮箱
成功获取验证码
身份核验完成
账户配置完成
注册主流程已完成
注册: 流程执行成功
```

### 第五步：确认成功后再批量注册

单账号跑通后，再改为批量注册。建议先用小数量测试，例如 2-5 个账号；确认代理和邮箱稳定后再提高并发。

## OpenAI 注册配置

### Web UI 启动

默认启动：

```bash
python webui.py
```

指定本地端口和登录密码：

```bash
python webui.py --host 127.0.0.1 --port 8001 --access-password your_password
```

访问：

```text
http://127.0.0.1:8001/login
```

如果页面提示 `ERR_CONNECTION_REFUSED`，说明 Web UI 没有启动、端口不一致，或旧进程已退出。重新运行上面的启动命令；如端口被占用，换端口或结束占用进程后重启。

### 代理配置

OpenAI 注册链路通常需要可访问 OpenAI/Auth 域名的代理。进入「设置」页面配置：

- 启用代理：开启
- 代理类型：`http` 或 `socks5`
- 代理主机：例如 `127.0.0.1`
- 代理端口：例如本地代理端口 `7897`

等价环境变量/配置项：

```text
proxy.enabled=true
proxy.type=http
proxy.host=127.0.0.1
proxy.port=7897
```

代理连通但 OpenAI 返回 `403` 通常表示网络可达；注册失败要继续看注册日志中的阶段错误。

### 邮箱配置：Cloudflare Email Routing + Gmail IMAP

适合已有域名、但不想自建临时邮箱服务的场景。整体链路如下：

```text
tmpxxxx@example.com
→ Cloudflare Email Routing
→ your-gmail@gmail.com
→ Gmail IMAP
→ CPA-AutoReg-Engine 读取验证码
```

#### 1. Cloudflare 侧配置

1. 域名必须已托管在 Cloudflare。
2. 打开 Cloudflare 控制台中的 Email Routing。
3. 添加目标地址：`your-gmail@gmail.com`，并按邮件提示完成验证。
4. 添加路由规则：
   - 推荐开启 Catch-all，把 `*@example.com` 转发到 Gmail。
   - 如果不想开启 Catch-all，也可以添加具体地址规则，但批量注册不方便。
5. 确认 Cloudflare 自动添加的 MX 记录已生效。

#### 2. Gmail 侧配置

1. Gmail 开启两步验证。
2. 创建「应用专用密码」。
3. 确认 Gmail 已启用 IMAP。
4. 本项目里填写应用专用密码，不要填写 Google 登录密码。

#### 3. CPA-AutoReg-Engine 侧配置

在「邮箱服务」中新增服务：

- 类型：`imap_mail`
- IMAP 主机：`imap.gmail.com`
- IMAP 端口：`993`
- SSL：开启
- 用户名：接收转发邮件的 Gmail 地址
- 密码：Gmail 应用专用密码
- 域名：Cloudflare Email Routing 收信域名，例如 `example.com`
- 文件夹：`INBOX`
- 超时：建议 `120` 秒

等价 JSON 示例：

```json
{
  "host": "imap.gmail.com",
  "port": 993,
  "use_ssl": true,
  "email": "your-gmail@gmail.com",
  "password": "your_google_app_password",
  "domain": "example.com",
  "folder": "INBOX",
  "timeout": 120,
  "poll_interval": 5,
  "mail_limit": 20
}
```

运行时系统会生成类似 `tmpxxxx@example.com` 的临时地址。Cloudflare 把邮件转发到 Gmail 后，系统通过 Gmail IMAP 轮询 OpenAI 验证码。

#### 4. 常见配置错误

- Gmail 目标地址未验证：Cloudflare 会拒绝或暂停转发。
- Cloudflare MX 记录未生效：域名收不到任何邮件。
- 未开启 Catch-all：随机生成的 `tmpxxxx@example.com` 收不到邮件。
- 使用 Google 登录密码：Gmail IMAP 会认证失败，必须用应用专用密码。
- Gmail 未启用 IMAP：系统无法读取收件箱。
- 邮件进了垃圾箱或其他标签：先确认 Gmail 能看到 OpenAI 验证邮件。

### 邮箱配置：IMAP Catch-all

如果你已有其他支持 catch-all 的域名邮箱，也可以直接使用 `imap_mail`，把所有临时地址邮件收进同一个 IMAP 邮箱。

在「邮箱服务」中添加 IMAP 服务：

- 类型：`imap_mail`
- IMAP 主机：邮箱服务商提供的 IMAP host
- IMAP 端口：通常 `993`
- SSL：开启
- 用户名：接收 catch-all 邮件的邮箱账号
- 密码：邮箱应用专用密码，不要使用网页登录密码
- 域名：catch-all 域名，例如 `example.com`
- 超时：建议 `120` 秒

运行时系统会生成类似 `tmpxxxx@domain` 的临时地址，并从 IMAP 邮箱中轮询 OpenAI 验证码。

### 邮箱配置：Cloudflare Temp Email

> **📌 提示**
> 使用 Cloudflare Temp Email 时，建议绑定高质量的独立域名。如果使用免费或泛滥后缀的域名，可能会收不到 OpenAI 发送的验证码。

Cloudflare Temp Email 指自建的 Cloudflare 临时邮箱服务，不是 Cloudflare Email Routing 原生面板功能。该服务需要先部署好，并能通过 HTTP API 创建邮箱和读取邮件。

本项目当前适配的自建服务 API 来自开源项目：

- 项目地址：https://github.com/dreamhunter2333/cloudflare_temp_email

请先按该开源项目文档完成部署、域名/MX/Cloudflare 配置；CPA-AutoReg-Engine 侧只需要填写部署后的 `base_url` 和收信 `domain`。

#### 完整配置步骤

1. 确认临时邮箱服务已部署并可访问。

   浏览器或命令行能访问服务地址，例如：

   ```text
   https://your-mail-service.example.com
   ```

2. 确认服务已绑定收信域名。

   这里的域名必须是临时邮箱服务已经配置好的域名，例如：

   ```text
   example.com
   ```

   系统创建邮箱时会生成类似：

   ```text
   tmpxxxx@example.com
   ```

3. 在 Web UI 打开「邮箱服务」页面，新增邮箱服务。

   - 类型：`cloudflare_temp_email`
   - 名称：自定义，例如 `Cloudflare Temp Email`
   - 配置：填写下面 JSON

   ```json
   {
     "base_url": "https://your-mail-service.example.com",
     "domain": "example.com",
     "timeout": 120,
     "poll_interval": 3,
     "mail_limit": 10
   }
   ```

4. 字段说明。

   - `base_url`：临时邮箱服务地址，不要带末尾 `/`
   - `domain`：临时邮箱服务已配置的收信域名
   - `timeout`：等待验证码最长秒数，建议 `120`
   - `poll_interval`：轮询邮件间隔秒数，建议 `3`
   - `mail_limit`：每次拉取邮件列表数量，建议 `10`

5. 不要填写这些字段。

   - 不需要 admin token
   - 不需要手动填写 JWT
   - 不需要邮箱密码

   每个邮箱创建后，服务会返回该邮箱自己的运行时 JWT，系统只在内存里用它读取邮件，不应写入配置文件或日志。

6. 保存服务后，在注册页面选择这个邮箱服务运行一次单账号注册。

   成功时日志应能看到：

   ```text
   成功创建邮箱: tmpxxxx@example.com
   正在等待邮箱 tmpxxxx@example.com 的验证码 (120s)...
   成功获取验证码
   ```

#### API 要求

当前适配器使用以下接口：

```text
POST /api/new_address
GET /api/parsed_mails?limit=10&offset=0
GET /api/parsed_mail/:id
```

`POST /api/new_address` 请求体：

```json
{
  "name": "tmpxxxx",
  "domain": "example.com"
}
```

响应至少需要包含：

```json
{
  "address": "tmpxxxx@example.com",
  "jwt": "runtime-mailbox-jwt"
}
```

`GET /api/parsed_mails` 和 `GET /api/parsed_mail/:id` 需要使用邮箱创建时返回的 JWT：

```text
Authorization: Bearer <runtime-mailbox-jwt>
```

#### 常见配置错误

- `base_url` 填错：创建邮箱失败，或连接失败。
- `domain` 不是服务已绑定域名：创建邮箱失败，或收不到验证码。
- 服务能创建邮箱但收不到信：检查域名 MX/路由配置和临时邮箱服务日志。
- 手动把 JWT 写进配置：不需要，也不安全；JWT 是每个邮箱运行时返回值。

## OpenAI 注册链路排障

### 浏览器注册失败与风控

当前使用最新的 **Camoufox 真实浏览器** 自动化引擎来绕过协议层检测。如果出现失败，常见原因为：

#### 1. `未打开注册入口` 或 `未找到邮箱输入框`

原因：
- OpenAI 的注册页面结构发生变化。
- 代理 IP 被列入高风险，导致页面直接弹 Cloudflare 验证码且无法通过，没有展示"Sign up"。
- Camoufox 实例初始化慢，导致页面元素加载超时。

处理：
- 检查代理 IP 纯净度（更换节点）。
- 检查服务器资源（Camoufox 需较多内存，推荐至少 2GB 可用内存）。

#### 2. `未收到验证码` 或 `验证码被拒绝`

原因：
- 邮箱被风控：公共临时邮箱（如 TempMail）及部分免费/泛滥的 Cloudflare Temp Email 自建域名被拦截，OpenAI 显示发了邮件但后台不会真实下发（静默拦截）。
- IMAP 邮箱：发件人可能被归类到垃圾邮件。
- 网络延迟导致邮件还没到达。

处理：
- 强烈建议换用优质独立域名结合 Cloudflare Email Routing 转发至 Gmail，并使用 `imap_mail`。
- 如果用 IMAP，检查 Gmail 的垃圾邮件箱。

### 旧版 HTTP 协议注册失败参考（API 模式）

#### 1. `HTTP 409 invalid_state`

原因：OpenAI Auth 注册状态链不一致，常见于客户端没有按预期流程持久化会话。

处理：重试，或检查代理 IP。

#### 3. `发送验证码响应缺少客户端认证会话`

原因：`/email-otp/resend` 可能只触发邮件发送，不一定返回客户端会话。

处理：验证码发送接口返回 `200` 即可继续等待邮件；只有响应里包含会话且会话无效时才应失败。

#### 4. `客户端认证会话无效`

原因：会话响应结构和代码预期不一致，或缺少 `session_id`。

处理：查看安全诊断日志：

```text
客户端认证会话转储字段: [...]
客户端认证会话存在: True/False
客户端认证会话包含 session_id: True/False
```

如果字段为 `['checksum', 'client_auth_session', 'session_id']`，这是当前已支持的正常结构。

#### 5. `未支持的注册状态: page=api_accounts_client_auth_session_dump`

原因：把 `client_auth_session_dump` 接口 URL 当成了下一步页面。

处理：OTP 验证成功后应进入 `https://auth.openai.com/about-you`，再执行账号信息创建。

#### 6. `Operation timed out after 30001 milliseconds`

原因：OpenAI Auth 请求网络超时，通常是代理或远端网络波动。

处理：确认代理仍可访问 OpenAI/Auth；重试当前注册任务。若频繁出现，检查代理稳定性和 DNS。

#### 7. `host 主机名无法解析`

原因：DNS 或代理解析失败。

处理：检查本机 DNS、代理规则和代理进程；恢复后重试。

### 安全日志原则

排障日志只记录 HTTP 状态、字段名和布尔值，不记录验证码、JWT、session、cookie 或邮箱密码。

## Webchat2API 自动推送与闭环

注册成功的账号，其所有的 Session Token、Access Token 及其设备信息均会自动推送到后端的 [webchat2api](https://github.com/zqbxdev/webchat2api)（或其他兼容网关）进行持久化，完成自动入池。

在 `.env` 或「系统设置」中可配置：
- `WEBCHAT2API_ENABLED`：是否开启推送（默认 `True`）
- `WEBCHAT2API_BASE_URL`：推送目标服务地址（默认 `http://127.0.0.1:19000`）
- `WEBCHAT2API_API_TOKEN`：推送鉴权管理 Token（默认 `admin`）

推送成功后会在控制台或日志中打印 `成功上传账号到 webchat2api`。如果目标服务没有响应，系统将产生 Timeout 异常并在日志中打印（不影响已注册账号写入 SQLite 本地库）。

## 部署配置

### 本地运行

适合开发调试或首次配置：

```bash
python webui.py --host 127.0.0.1 --port 8001 --access-password your_password
```

访问：

```text
http://127.0.0.1:8001/login
```

本地运行建议先完成代理和邮箱配置，再跑单账号注册测试。

### Docker 运行

适合长期运行。先准备持久化目录：

```bash
mkdir -p ~/CPA-AutoReg-Engine
cd ~/CPA-AutoReg-Engine
mkdir -p data logs
```

#### 从 GitHub 直接拉取 compose 示例

```bash
curl -O https://raw.githubusercontent.com/Maoleio/CPA-AutoReg-Engine/main/docker-compose.yml
```

也可以直接创建 `docker-compose.yml`：

```yaml
services:
  cpa-codex-manager:
    image: HSJ-BanFan/CPA-AutoReg-Engine:latest
    container_name: cpa-codex-manager
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      WEBUI_HOST: 0.0.0.0
      WEBUI_PORT: 8000
      WEBUI_ACCESS_PASSWORD: your_secret_password
      APP_DATABASE_URL: data/database.db
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
```

字段说明：

- `WEBUI_HOST`：Web 服务监听地址。Docker 内通常保持 `0.0.0.0`。
- `WEBUI_PORT`：容器内 Web 服务端口，默认 `8000`。
- `WEBUI_ACCESS_PASSWORD`：Web 管理后台登录密码，首次部署必须改成强密码。
- `APP_DATABASE_URL`：数据库连接地址。SQLite 可用 `data/database.db`。
- `./data:/app/data`：持久化数据库。
- `./logs:/app/logs`：持久化运行日志。

如果使用 PostgreSQL，把 `APP_DATABASE_URL` 改成数据库连接串：

```yaml
environment:
  APP_DATABASE_URL: postgresql://user:password@host:5432/dbname
```

启动：

```bash
docker compose up -d
```

访问：

```text
http://服务器IP:8000/login
```

查看日志：

```bash
docker compose logs -f
```

更新镜像：

```bash
docker compose pull
docker compose up -d
```

### 部署后必须配置

首次启动后进入 Web UI，至少完成三项配置：

1. 设置代理：确认 OpenAI/Auth 域名可访问。
2. 添加邮箱服务：推荐 `cloudflare_temp_email` 或 `imap_mail`。
3. 单账号注册测试：确认能创建邮箱、收到验证码、完成注册。

不要把 Gmail 应用专用密码、邮箱 JWT、Bearer token 写进 README、日志或公开 issue。

### 桌面版运行

如果你想以桌面窗口方式运行，而不是手动打开浏览器：

```bash
pip install pywebview
python desktop.py
```

桌面模式会：
- 后台自动启动本地 FastAPI 服务
- 使用 `pywebview` 打开内嵌窗口
- 默认仅监听 `127.0.0.1`
- 默认使用本地 SQLite，无需配置 `.env`

## 桌面版打包

### macOS 桌面版打包

请在 **macOS** 上执行：

```bash
chmod +x scripts/build_macos_dmg.sh
./scripts/build_macos_dmg.sh
```

打包完成后产物位于：

- `dist/CPA-AutoReg-Engine.app`
- `dist/CPA-AutoReg-Engine.dmg`


### Windows 桌面版打包

请在 **Windows 系统** 上执行：

```bat
scripts\build_windows.bat
```

打包完成后产物通常位于：

- `dist\CPA-AutoReg-Engine\CPA-AutoReg-Engine.exe`



## 页面展示
<img width="1064" height="511" alt="截屏2026-03-25 22 39 46" src="https://github.com/user-attachments/assets/4a019320-6a86-44d6-b465-c53e74f97ac1" />
<img width="1795" height="877" alt="截屏2026-03-25 22 35 12" src="https://github.com/user-attachments/assets/ed34f98e-3b39-44f8-9ac5-19bce792ded4" />
<img width="1791" height="881" alt="截屏2026-03-25 22 39 10" src="https://github.com/user-attachments/assets/f4388533-43a1-4d27-a626-83ecc582dfcd" />
<img width="1801" height="887" alt="截屏2026-03-25 22 34 33" src="https://github.com/user-attachments/assets/81d998df-e109-4836-9482-95d2659948d6" />
<img width="1792" height="877" alt="截屏2026-03-25 22 39 29" src="https://github.com/user-attachments/assets/21c7d6a6-e367-410c-a180-84cfe1ef74c6" />

## 更新日志

### v1.1.0

- 注册主流程升级为新的状态机会话链路，整体兼容性与稳定性提升。
- 批量注册启动流程优化，降低大批量任务在启动阶段的阻塞时间。
- 批量监控逻辑优化。
- 注册日志阶段划分重新整理，阶段编号和提示文案更加清晰统一。
- CPA 上传链路优化，支持更直接的投递流程与更清晰的上传日志。

## 巡检与补货配置建议

1. **巡检频率**：建议设置为 60 分钟一次，配合账户状态（401/Quota）清理。
2. **补货方案**：
   - 建议在 CPA 检测页面开启“自动补货”。
   - 当就绪账号少于指定数量时，触发一次补货。
   - 补货模式推荐使用“并行模式”以提高效率。

## 免责声明

本项目仅供学习、研究和技术交流使用，请遵守 OpenAI 相关服务条款。

因使用本项目产生的任何风险和后果，由使用者自行承担。

## Star History

<p align="center">
  <a href="https://www.star-history.com/#maoleio/CPA-AutoReg-Engine&Date">
    <img src="https://api.star-history.com/svg?repos=maoleio/CPA-AutoReg-Engine&type=Date" alt="Star History Chart" />
  </a>
</p>

## 致谢 / Acknowledgements

本项目基于 [CPA-Codex-Manager](https://github.com/Maoleio/CPA-Codex-Manager) 二次开发。感谢原作者 [Maoleio](https://github.com/Maoleio) 的开源贡献。

---
**CPA-AutoReg-Engine** - 让 CLIProxyAPI 号池管理变得优雅而自动化。
