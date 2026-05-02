# Cloudflare Tunnel 구현 계획

**목표**: `yarr-api.simplyimg.com` 으로 서버 외부 노출  
**방식**: `cloudflared` 컨테이너를 docker-compose에 추가, Nginx(:80) 앞단에 연결

---

## 아키텍처

```
인터넷
  └─ yarr-api.simplyimg.com (Cloudflare Edge)
        └─ Cloudflare Tunnel
              └─ cloudflared 컨테이너 (cup_stack 네트워크)
                    └─ nginx:80
                          ├─ /api/robot/      → robot:8001
                          ├─ /api/handineye/  → handineye:8002
                          ├─ /api/handtoeye/  → handtoeye:8003
                          ├─ /ws/*            → 각 서비스
                          └─ /                → Frontend SPA
```

---

## Phase 1 — 터널 자격증명 발급 (1회)

호스트에서 실행합니다.

```bash
# cloudflared 설치 (Ubuntu)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# Cloudflare 계정 인증 → ~/.cloudflared/cert.pem 생성
cloudflared tunnel login

# 터널 생성 → ~/.cloudflared/<UUID>.json 생성, UUID 출력됨
cloudflared tunnel create yarr-api

# DNS CNAME 자동 등록
# yarr-api.simplyimg.com → <UUID>.cfargotunnel.com
cloudflared tunnel route dns yarr-api yarr-api.simplyimg.com
```

`tunnel create` 출력에서 UUID를 메모해둡니다.

---

## Phase 2 — 설정 파일 추가

### `server/cloudflared/config.yml` (신규)

```yaml
tunnel: <UUID>
credentials-file: /etc/cloudflared/<UUID>.json

ingress:
  - hostname: yarr-api.simplyimg.com
    service: http://nginx:80
  - service: http_status:404
```

`<UUID>`를 Phase 1에서 발급받은 값으로 교체합니다.

> WebSocket(`/ws/*`)은 Cloudflare Tunnel이 기본 지원하므로 별도 ingress 규칙 불필요합니다.

---

## Phase 3 — docker-compose.yml 수정

기존 `networks:` 블록 위에 `cloudflared` 서비스를 추가합니다.

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    command: tunnel --config /etc/cloudflared/config.yml run
    volumes:
      - ~/.cloudflared/<UUID>.json:/etc/cloudflared/<UUID>.json:ro
      - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
    depends_on:
      - nginx
    networks:
      - cup_stack
    restart: unless-stopped
```

`~/.cloudflared/<UUID>.json` 의 `~` 는 실제 절대경로(`/home/ssu/.cloudflared/...`)로 교체합니다.

---

## Phase 4 — .gitignore 수정

자격증명 파일이 커밋되지 않도록 `server/.gitignore` 에 추가합니다.

```
# Cloudflare Tunnel 자격증명
*.json
```

`cloudflared/config.yml` 은 UUID만 포함하므로 커밋해도 무방합니다.  
`.json` 자격증명 파일은 절대 커밋하지 않습니다.

---

## Phase 5 — 검증

```bash
# 전체 스택 기동
docker compose up --build

# 헬스체크
curl https://yarr-api.simplyimg.com/health
# 기댓값: ok

# WebSocket 확인 (wscat 필요: npm i -g wscat)
wscat -c wss://yarr-api.simplyimg.com/ws/robot/state
```

---

## 변경 파일 요약

| 파일 | 변경 내용 |
|---|---|
| `server/cloudflared/config.yml` | 신규 생성 — 터널 UUID 및 ingress 규칙 |
| `server/docker-compose.yml` | `cloudflared` 서비스 추가 |
| `server/.gitignore` | `*.json` 추가 |

---

## 주의사항

- `cloudflared tunnel login` 은 브라우저 인증이 필요합니다. 헤드리스 환경이면 `--credentials-file` 플래그로 수동 처리합니다.
- Cloudflare 대시보드 > Zero Trust > Networks > Tunnels 에서 터널 상태를 실시간 확인할 수 있습니다.
- 터널이 `HEALTHY` 상태가 되어야 DNS가 활성화됩니다 (보통 30초 이내).
