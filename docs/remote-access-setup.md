# 원격 접속 환경 설정 가이드

이 서버(`ssu-22663-24`, 공인 IP `203.246.36.225`)에 구성된 원격 접속 스택 설치 절차입니다.  
신규 PC에서 Claude Code로 이 문서를 참조해 동일 환경을 재현할 수 있습니다.

---

## 구성 요소

| 도구 | 역할 | 버전 |
|------|------|------|
| RustDesk (self-hosted) | GUI 원격 데스크톱 (hbbs + hbbr) | hbbs 1.1.15 |
| Cloudflare Tunnel | HTTP/WebSocket 외부 노출 (공인 IP 없이) | cloudflared 2026.3.0 |
| Tailscale | VPN 메쉬 네트워크 (SSH 등 내부 접근) | 1.96.4 |

---

## 1. RustDesk 자체 호스팅 서버

### 개요

RustDesk는 `hbbs` (Signal/Rendezvous 서버)와 `hbbr` (Relay 서버) 두 바이너리로 자체 서버를 구성합니다.  
클라이언트는 이 서버를 통해 NAT를 뚫고 P2P 연결하거나 릴레이를 경유합니다.

### 1-1. 바이너리 설치

```bash
# 최신 릴리즈 확인: https://github.com/rustdesk/rustdesk-server/releases
RUSTDESK_VER=1.1.15

mkdir -p /opt/rustdesk
cd /opt/rustdesk

# Linux x86_64 기준
curl -L "https://github.com/rustdesk/rustdesk-server/releases/download/${RUSTDESK_VER}/rustdesk-server-linux-amd64.zip" \
  -o rustdesk-server.zip
unzip rustdesk-server.zip
chmod +x hbbs hbbr
rm rustdesk-server.zip
```

### 1-2. 로그 디렉토리 생성

```bash
sudo mkdir -p /var/log/rustdesk
sudo chown $USER:$USER /var/log/rustdesk
```

### 1-3. systemd 서비스 등록

Signal 서버 (`hbbs`):

```bash
sudo tee /etc/systemd/system/rustdesksignal.service > /dev/null << 'EOF'
[Unit]
Description=Rustdesk Signal Server

[Service]
Type=simple
LimitNOFILE=1000000
ExecStart=/opt/rustdesk/hbbs
WorkingDirectory=/opt/rustdesk/
User=ssu
Group=ssu
Restart=always
StandardOutput=append:/var/log/rustdesk/signalserver.log
StandardError=append:/var/log/rustdesk/signalserver.error
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

Relay 서버 (`hbbr`):

```bash
sudo tee /etc/systemd/system/rustdeskrelay.service > /dev/null << 'EOF'
[Unit]
Description=Rustdesk Relay Server

[Service]
Type=simple
LimitNOFILE=1000000
ExecStart=/opt/rustdesk/hbbr
WorkingDirectory=/opt/rustdesk/
User=ssu
Group=ssu
Restart=always
StandardOutput=append:/var/log/rustdesk/relayserver.log
StandardError=append:/var/log/rustdesk/relayserver.error
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable rustdesksignal rustdeskrelay
sudo systemctl start rustdesksignal rustdeskrelay

# 상태 확인
systemctl status rustdesksignal rustdeskrelay
```

### 1-4. 방화벽 포트 개방

```bash
# hbbs: TCP 21115-21118, UDP 21116
# hbbr: TCP 21117
sudo ufw allow 21115:21118/tcp
sudo ufw allow 21116/udp
sudo ufw allow 21117/tcp
```

### 1-5. 공개키 확인 (클라이언트 설정용)

```bash
cat /opt/rustdesk/id_ed25519.pub
```

출력된 공개키 문자열을 RustDesk 클라이언트의 **ID Server** 설정에 입력합니다.

### 1-6. RustDesk 클라이언트 설정

RustDesk 클라이언트 → 설정 → 네트워크:

| 항목 | 값 |
|------|-----|
| ID Server | `203.246.36.225` 또는 Tailscale IP `100.88.220.119` |
| Relay Server | (위와 동일, 비워도 자동 감지) |
| API Server | (비움) |
| Key | `/opt/rustdesk/id_ed25519.pub` 내용 |

---

## 2. Cloudflare Tunnel

### 개요

공인 고정 IP가 없거나 방화벽 안쪽 서버를 HTTPS로 외부에 노출할 때 사용합니다.  
현재 구성: `yarr-api.simplyimg.com` → nginx:80 (cup-stack 전체 스택)

```
인터넷 → yarr-api.simplyimg.com (Cloudflare Edge)
  └─ Cloudflare Tunnel (UUID: 4ffcfc13-173e-468f-8623-cab1fa0813c5)
       └─ cloudflared 프로세스 (이 서버)
            └─ nginx:80
```

### 2-1. cloudflared 설치

```bash
# Ubuntu/Debian
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o cloudflared.deb
sudo dpkg -i cloudflared.deb

cloudflared --version
```

### 2-2. Cloudflare 계정 인증

```bash
cloudflared tunnel login
# 브라우저가 열리면 해당 계정 및 도메인 선택
# 완료되면 ~/.cloudflared/cert.pem 생성됨
```

헤드리스(SSH) 환경이면 출력된 URL을 로컬 브라우저에서 열고,  
인증 완료 후 `cert.pem`을 서버에 복사합니다.

### 2-3. 터널 생성

```bash
# 터널 이름은 자유롭게 지정
cloudflared tunnel create yarr-api
# → ~/.cloudflared/<UUID>.json 생성, UUID 출력됨

# DNS CNAME 등록 (도메인은 Cloudflare 관리 중이어야 함)
cloudflared tunnel route dns yarr-api yarr-api.simplyimg.com
```

### 2-4. 설정 파일 작성

`server/cloudflared/config.yml` 생성:

```yaml
tunnel: <UUID>           # 위에서 발급받은 UUID
credentials-file: /etc/cloudflared/<UUID>.json

ingress:
  - hostname: yarr-api.simplyimg.com
    service: http://nginx:80
  - service: http_status:404
```

> WebSocket(`/ws/*`)은 Cloudflare Tunnel이 기본 지원합니다. 별도 ingress 불필요.

### 2-5. Docker Compose에 cloudflared 서비스 추가

`server/docker-compose.yml`의 `networks:` 블록 위에 추가:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --config /etc/cloudflared/config.yml run
    volumes:
      - /home/<USER>/.cloudflared/<UUID>.json:/etc/cloudflared/<UUID>.json:ro
      - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
    depends_on:
      - nginx
    networks:
      - cup_stack
    restart: unless-stopped
```

`<USER>`와 `<UUID>`를 실제 값으로 교체합니다.

### 2-6. 자격증명 파일 보호

```bash
# .gitignore에 추가 (커밋 방지)
echo "*.json" >> server/.gitignore
```

`config.yml`(UUID만 포함)은 커밋 가능하지만 `.json` 자격증명은 절대 커밋하지 않습니다.

### 2-7. 기동 및 검증

```bash
cd server
docker compose up --build -d

# 헬스체크
curl https://yarr-api.simplyimg.com/health
# 기댓값: ok

# Cloudflare 대시보드에서도 확인
# Zero Trust > Networks > Tunnels → HEALTHY 상태 확인
```

### 2-8. 시스템 서비스로 실행 (Docker 없이)

cloudflared를 systemd로 직접 실행하는 경우:

```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

자격증명은 `/etc/cloudflared/<UUID>.json`에 복사해야 합니다.

---

## 3. Tailscale

### 개요

Tailscale은 WireGuard 기반 메쉬 VPN입니다.  
현재 네트워크: `tailc4bc6c.ts.net`

| 노드 | Tailscale IP | 역할 |
|------|-------------|------|
| `ssu-22663-24` | `100.88.220.119` | 이 서버 (로봇 PC) |
| `macbook-pro-7` | `100.75.7.75` | 개발 MacBook |
| `leo` | `100.104.23.82` | 태그드 디바이스 |

### 3-1. 설치

```bash
# Ubuntu/Debian (공식 스크립트)
curl -fsSL https://tailscale.com/install.sh | sh

# 또는 apt 저장소 직접 추가
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg \
  | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg > /dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list \
  | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt update && sudo apt install tailscale
```

### 3-2. 로그인 및 네트워크 참여

```bash
sudo tailscale up
# 출력되는 URL을 브라우저에서 열어 인증 → 네트워크에 참여됨
```

특정 네트워크(조직)에 참여하려면:

```bash
sudo tailscale up --login-server https://login.tailscale.com
```

### 3-3. 서비스 활성화

```bash
sudo systemctl enable tailscaled
sudo systemctl start tailscaled

# 상태 확인
tailscale status
```

### 3-4. SSH 접근 설정 (선택)

Tailscale SSH를 활성화하면 SSH 키 없이 Tailscale 인증으로 접속할 수 있습니다:

```bash
sudo tailscale up --ssh
```

Tailscale 대시보드 → Access Controls에서 SSH 정책을 설정합니다.

### 3-5. 유용한 명령어

```bash
tailscale status          # 연결된 노드 목록 및 상태
tailscale ip              # 이 노드의 Tailscale IP
tailscale ping <hostname> # 다른 노드 핑
tailscale netcheck        # 연결 품질 진단
tailscale down            # VPN 일시 해제
tailscale up              # VPN 재연결
```

---

## 전체 아키텍처 요약

```
외부 HTTPS 접근
  └─ yarr-api.simplyimg.com
       └─ Cloudflare Tunnel → nginx:80 → FastAPI 서비스

GUI 원격 데스크톱
  └─ RustDesk 클라이언트 (어디서나)
       └─ hbbs (21115-21118) + hbbr (21117) → 이 서버 데스크톱

내부 VPN 접근 (SSH / 포트포워딩)
  └─ Tailscale 100.88.220.119
       └─ 같은 tailnet 노드에서 직접 접근
```

---

## 트러블슈팅

### RustDesk 서버 접속 불가

```bash
# 서비스 로그 확인
tail -f /var/log/rustdesk/signalserver.log
tail -f /var/log/rustdesk/relayserver.error

# 포트 수신 확인
ss -tlnp | grep -E '2111[5-8]'
ss -ulnp | grep 21116
```

### Cloudflare Tunnel UNHEALTHY

```bash
# cloudflared 로그 확인 (Docker)
docker compose logs cloudflared

# 자격증명 파일 경로 확인
ls -la ~/.cloudflared/*.json

# 터널 목록 확인
cloudflared tunnel list
```

### Tailscale DNS 경고

```
Tailscale can't reach the configured DNS servers.
```

`/etc/resolv.conf`에 `100.100.100.100` (MagicDNS)이 추가됐는지 확인합니다.  
네트워크 환경에 따라 실제 DNS 해석에는 영향 없는 경우가 많습니다.

```bash
sudo tailscale up --accept-dns=false   # MagicDNS 비활성화 (필요시)
```
