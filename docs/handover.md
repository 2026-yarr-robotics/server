# Cup-Stack 시스템 인수인계 가이드

신규 PC에서 이 시스템을 처음부터 재현하기 위한 절차입니다.  
대상 범위: **FastAPI 서버 스택 · ROS 2 레이어 · Tailscale SSH · Cloudflare Tunnel**

---

## 0. 사전 준비 — 기존 PC에서 복사해야 할 파일

| 파일 | 경로 | 용도 |
|------|------|------|
| Cloudflare Tunnel 자격증명 | `~/.cloudflared/4ffcfc13-173e-468f-8623-cab1fa0813c5.json` | 터널 인증 |
| SSH 개인키 (선택) | `~/.ssh/id_ed25519` | git / 원격 접속 |

> **주의:** `.json` 자격증명은 절대 git에 커밋하지 않습니다.

신규 PC에 안전하게 복사하는 예시 (기존 PC → 신규 PC):

```bash
# Tailscale VPN 경유 (아래 3단계 완료 후 사용 가능)
scp ~/.cloudflared/4ffcfc13-173e-468f-8623-cab1fa0813c5.json \
    <신규PC_tailscale_ip>:~/.cloudflared/
```

---

## 1. 기본 패키지 설치

```bash
sudo apt update && sudo apt install -y \
  git curl wget unzip \
  docker.io docker-compose-plugin \
  python3-pip python3-venv
  
# Docker 비루트 실행 허용
sudo usermod -aG docker $USER
newgrp docker
```

---

## 2. 코드 클론

```bash
git clone <repo-url> ~/development/cup-stack
cd ~/development/cup-stack

# ROS2 레이어의 서브모듈 초기화 (Doosan 드라이버)
cd cup_stack
git submodule update --init --recursive
cd ..
```

---

## 3. Tailscale 설치 및 네트워크 참여

Tailscale은 이 시스템의 내부 접근(SSH 등) 수단입니다.

### 3-1. 설치

```bash
curl -fsSL https://tailscale.com/install.sh | sh
```

### 3-2. 로그인 및 참여

```bash
sudo tailscale up --ssh
# 출력되는 URL을 브라우저에서 열어 인증
# 완료되면 tailc4bc6c.ts.net 네트워크에 합류됨
```

`--ssh` 플래그는 Tailscale SSH를 활성화합니다.  
이후 같은 tailnet의 노드에서 `ssh <user>@<hostname>.tailc4bc6c.ts.net` 으로 접속 가능합니다.

### 3-3. 서비스 등록

```bash
sudo systemctl enable --now tailscaled
tailscale status   # 연결 확인
tailscale ip       # 이 노드에 할당된 Tailscale IP 확인
```

### 3-4. 현재 네트워크 구성

| 노드 | Tailscale IP | 역할 |
|------|-------------|------|
| `ssu-22663-24` (기존 로봇 PC) | `100.88.220.119` | 기존 서버 |
| `macbook-pro-7` | `100.75.7.75` | 개발 MacBook |
| `leo` | `100.104.23.82` | 태그드 디바이스 |
| **(신규 PC)** | *(참여 후 확인)* | 신규 로봇 PC |

### 3-5. SSH 키 배포 (Tailscale SSH 미사용 시)

Tailscale SSH 대신 일반 SSH를 쓰려면:

```bash
# 신규 PC에서 키 생성
ssh-keygen -t ed25519 -C "$(hostname)"

# 접속할 PC의 authorized_keys에 등록
ssh-copy-id <user>@<대상_tailscale_ip>
```

---

## 4. ROS 2 Humble 설치

```bash
# ROS 2 Humble (Ubuntu 22.04 기준)
sudo apt install -y software-properties-common
sudo add-apt-repository universe
curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc | \
  sudo tee /usr/share/keyrings/ros-archive-keyring.gpg > /dev/null

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
  http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | \
  sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null

sudo apt update && sudo apt install -y \
  ros-humble-desktop \
  ros-humble-moveit \
  ros-humble-pilz-industrial-motion-planner \
  ros-humble-rosbridge-server \
  python3-colcon-common-extensions \
  python3-rosdep

sudo rosdep init && rosdep update
```

### ROS 2 워크스페이스 빌드

```bash
cd ~/development/cup-stack/cup_stack/ros2

rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
```

쉘 초기화 파일에 source 추가:

```bash
echo "source ~/development/cup-stack/cup_stack/ros2/install/setup.bash" >> ~/.bashrc
```

### 구문 검사 (ROS 의존성 없이)

```bash
python3 -m compileall ~/development/cup-stack/cup_stack/ros2/src/cup_stack
```

---

## 5. FastAPI 서버 스택 (Docker Compose)

### 5-1. Frontend 빌드

```bash
cd ~/development/cup-stack/frontend
npm install
npm run build
```

### 5-2. Cloudflare 자격증명 배치

기존 PC에서 복사한 파일을 아래 경로에 배치합니다:

```bash
mkdir -p ~/.cloudflared
# scp 또는 직접 복사
cp <복사한파일> ~/.cloudflared/4ffcfc13-173e-468f-8623-cab1fa0813c5.json
chmod 600 ~/.cloudflared/4ffcfc13-173e-468f-8623-cab1fa0813c5.json
```

### 5-3. Docker Compose 기동

```bash
cd ~/development/cup-stack/server
docker compose up --build -d
```

서비스 구성:

| 컨테이너 | 역할 | 포트 |
|----------|------|------|
| `nginx` | 리버스 프록시 + 프론트엔드 서빙 | `80` |
| `robot` | 로봇 도메인 FastAPI | `8001` (내부) |
| `handineye` | 핸드-인-아이 캘리브레이션 | `8002` (내부) |
| `handtoeye` | 핸드-투-아이 캘리브레이션 | `8003` (내부) |
| `cloudflared` | Cloudflare Tunnel 클라이언트 | — |

### 5-4. 헬스체크

```bash
# 로컬 nginx
curl http://localhost/health

# Cloudflare Tunnel 경유 (외부)
curl https://yarr-api.simplyimg.com/health
# 기댓값: ok

# 컨테이너 상태
docker compose ps
docker compose logs cloudflared   # 터널 연결 확인
```

---

## 6. Cloudflare Tunnel (신규 PC에서 재발급이 필요한 경우)

기존 자격증명(`.json`)을 그대로 복사하면 터널 재생성 없이 사용 가능합니다.  
**새 터널이 필요한 경우에만** 아래 절차를 따릅니다.

### 6-1. cloudflared 설치

```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
  -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

### 6-2. 계정 인증

```bash
cloudflared tunnel login
# 헤드리스 환경: 출력 URL을 로컬 브라우저에서 열어 인증
# → ~/.cloudflared/cert.pem 생성
```

### 6-3. 터널 재생성

```bash
cloudflared tunnel create yarr-api
# → 새 UUID 출력됨, ~/.cloudflared/<새UUID>.json 생성

# DNS 업데이트
cloudflared tunnel route dns yarr-api yarr-api.simplyimg.com
```

### 6-4. 설정 파일 수정

`server/cloudflared/config.yml`의 `tunnel:` 및 `credentials-file:` 항목을 새 UUID로 교체합니다.

`server/docker-compose.yml`의 cloudflared 볼륨 경로도 새 UUID로 교체합니다:

```yaml
volumes:
  - ~/.cloudflared/<새UUID>.json:/etc/cloudflared/<새UUID>.json:ro
  - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
```

---

## 7. ROS 2 실행

### Rosbridge (Docker 없이 직접 실행)

```bash
source ~/development/cup-stack/cup_stack/ros2/install/setup.bash
ros2 launch rosbridge_server rosbridge_websocket_launch.xml port:=9090
```

또는 `server/rosbridge.sh` 스크립트 사용:

```bash
cd ~/development/cup-stack/server
bash rosbridge.sh
```

### 시뮬레이션 Bringup

```bash
cd ~/development/cup-stack/cup_stack
bash ros2/src/cup_stack/bringup_sim.sh
```

### 실제 로봇 Bringup (IP: 192.168.1.100)

```bash
bash ros2/src/cup_stack/bringup_real.sh 192.168.1.100
```

### 태스크 실행 (bringup 후 별도 터미널)

```bash
source ~/development/cup-stack/cup_stack/ros2/install/setup.bash

ros2 launch cup_stack cup_pyramid.launch.py nest_inc:=0.0127
ros2 launch cup_stack cup_unstack.launch.py nest_inc:=0.0127
```

---

## 8. 서버 로컬 개발 모드 (Docker 없이)

```bash
cd ~/development/cup-stack/server
pip install -e ".[dev]"

cup-robot      # :8001
cup-handineye  # :8002
cup-handtoeye  # :8003
```

테스트:

```bash
pytest
```

---

## 9. 트러블슈팅

### Tailscale 연결 안 됨

```bash
sudo systemctl status tailscaled
sudo journalctl -u tailscaled -n 50
tailscale netcheck          # 연결 품질 진단
sudo tailscale up --reset   # 재인증
```

### Docker 컨테이너가 rosbridge에 연결 못 함

`robot` 컨테이너는 `host.docker.internal:9090` 으로 rosbridge에 접근합니다.  
rosbridge가 호스트에서 실행 중인지 확인:

```bash
ss -tlnp | grep 9090
```

### Cloudflare Tunnel UNHEALTHY

```bash
docker compose logs cloudflared
ls -la ~/.cloudflared/*.json          # 자격증명 파일 존재 확인
cloudflared tunnel list               # 터널 등록 상태 확인
```

### ROS 2 빌드 실패 (서브모듈 없음)

```bash
cd ~/development/cup-stack/cup_stack
git submodule update --init --recursive
```

---

## 부록 — 주요 엔드포인트 및 포트 요약

| 서비스 | 주소 | 비고 |
|--------|------|------|
| 웹 대시보드 | `http://localhost` / `https://yarr-api.simplyimg.com` | nginx |
| Robot API | `http://localhost/api/robot/` | nginx 프록시 → :8001 |
| Handineye API | `http://localhost/api/handineye/` | nginx 프록시 → :8002 |
| Handtoeye API | `http://localhost/api/handtoeye/` | nginx 프록시 → :8003 |
| ROS WebSocket 상태 | `ws://localhost/ws/robot/state` | 10 Hz |
| 태스크 로그 | `ws://localhost/ws/task/log` | |
| Rosbridge | `ws://localhost:9090` | 호스트 직접 |
| Tailscale SSH | `ssh <user>@<hostname>.tailc4bc6c.ts.net` | VPN 경유 |
