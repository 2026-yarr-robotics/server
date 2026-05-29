# 서버 실행 스크립트 사용법

`server/` 디렉터리의 운영 스크립트 사용 가이드입니다. 대상 스크립트:

- [`start.sh`](#startsh--시스템-전체-기동) — 시스템 전체 기동
- [`stop.sh`](#stopsh--시스템-전체-종료) — 시스템 전체 종료
- [`bringup_real_31.sh`](#bringup_real_31sh--실제-로봇-bringup-31-머신) — 실제 로봇 bringup ("31" 머신 전용)

> 보조 스크립트인 `rosbridge.sh`, `stop_bringup.sh`, `bringup_real.sh` 는 위 스크립트가
> 내부적으로 사용하거나 대체합니다. 끝부분 [참고 스크립트](#참고-스크립트)를 보세요.

---

## 사전 요구사항

| 항목 | 비고 |
|------|------|
| ROS 2 Humble | `/opt/ros/humble/setup.bash` 가 존재해야 함 |
| `tmux` | `sudo apt install tmux` |
| Docker + Docker Compose | `docker compose` v2 플러그인 |
| 프로젝트 워크스페이스 빌드 | `ros2-cup-stack/ros2/install/setup.bash` 가 있어야 함 |

프로젝트 워크스페이스가 아직 빌드되지 않았다면 먼저 빌드합니다:

```bash
cd ros2-cup-stack/ros2
colcon build --symlink-install
```

디렉터리 구조 가정 (스크립트는 자기 위치 기준으로 상대 경로를 계산합니다):

```
<repo>/
├── server/                       # 이 스크립트들이 위치
├── ros2-cup-stack/ros2/install/  # 프로젝트 ROS 2 오버레이
└── yarr-robust-speed-stack/ros2-recode-sequence/install/  # 카메라 launch
$HOME/ros2_ws/install/            # Doosan/MoveIt 베이스 워크스페이스
```

---

## `start.sh` — 시스템 전체 기동

컵 스태킹 로봇 시스템 전체를 하나의 `tmux` 세션(`cup-stack`)으로 기동합니다.

```bash
cd server
./start.sh
```

인자 없이 실행하며, 다음 구성요소를 각각 별도의 tmux 창으로 띄웁니다:

| 창 # | 이름 | 역할 |
|------|------|------|
| 1 | `rosbridge` | rosbridge_server (WebSocket, 포트 9090) |
| 2 | `cam-exo` | eye-to-hand 고정 카메라 (serial 242322077444) |
| 3 | `cam-hand` | eye-in-hand 그리퍼 카메라 (serial 140122076335) |
| 4 | `bringup-agent` | bringup 제어 에이전트 (포트 8099) |
| 5 | `gripper` | OnRobot 그리퍼 노드 |
| 6 | `server` | Docker (nginx + FastAPI + cloudflared), `-d` 분리 실행 |

기동 동작:

- 기존 `cup-stack` 세션이 있으면 먼저 종료 후 새로 시작합니다.
- 카메라는 D435i 두 대가 USB 자원 충돌(SIGSEGV)을 일으키지 않도록
  `view:=exo` / `view:=hand` 로 **1대씩 분리** 기동합니다.
- Docker 서버는 `docker compose up -d` 로 분리 실행되므로 tmux 세션을 닫아도
  컨테이너는 계속 동작합니다.
- 마지막에 자동으로 `tmux attach -t cup-stack` 됩니다.

> **bringup(로봇 모션)은 `start.sh` 가 직접 띄우지 않습니다.**
> 웹 대시보드 헤더의 **Bringup 버튼**(또는 [`bringup_real_31.sh`](#bringup_real_31sh--실제-로봇-bringup-31-머신))으로 별도 제어합니다.

### tmux 조작

```bash
tmux attach -t cup-stack        # 세션 연결
# Ctrl+b → 숫자       창 전환
# Ctrl+b → d          세션에서 분리(detach, 프로세스는 유지)
tmux kill-session -t cup-stack  # 세션만 종료
```

### 접속 주소

| 용도 | URL |
|------|-----|
| 대시보드 | https://yarr.simplyimg.com |
| API | https://yarr-api.simplyimg.com/api/robot/status |

---

## `stop.sh` — 시스템 전체 종료

`start.sh` 로 띄운 모든 구성요소를 순서대로 정리합니다.

```bash
cd server
./stop.sh
```

종료 순서 (각 단계 SIGINT 후 필요 시 SIGKILL):

1. bringup (`dsr_bringup2`)
2. `cup_stack` 관련 태스크 프로세스 (move_cartesian 등)
3. RealSense 카메라 (`realsense2_camera`)
4. rosbridge (`rosbridge_websocket`)
5. bringup-agent (`bringup_agent.py`, 포트 8099)
6. Docker Compose 서비스 (`docker compose down`)
7. tmux 세션 `cup-stack`

> bringup만 따로 끄려면 [`stop_bringup.sh`](#참고-스크립트) 를 사용하세요.

---

## `bringup_real_31.sh` — 실제 로봇 Bringup ("31" 머신)

DSR M0609 + MoveIt 를 **실제 로봇 모드(`mode:=real`)** 로 기동합니다.
이 "31" 머신 전용 경로 수정본입니다.

```bash
cd server
./bringup_real_31.sh [로봇IP]

# 예시 (기본값과 동일)
./bringup_real_31.sh 192.168.137.100
```

| 인자 | 기본값 | 설명 |
|------|--------|------|
| `로봇IP` | `192.168.137.100` | Doosan DRFL 호스트 (포트 12345 고정) |

### "31" 머신 네트워크 주의

이 머신은 로봇을 **USB 이더넷**(`enxec9a0c17dc1f`, `192.168.137.50/24`)으로 연결합니다.
로봇은 같은 대역의 `192.168.137.100`(DRFL `:12345`)에 있습니다.

> ⚠️ 기본 문서값인 `192.168.1.100` 은 이 머신에 해당 대역 인터페이스가 없어
> WAN 게이트웨이로 새어나가 **연결 타임아웃**이 납니다. 반드시 `192.168.137.100` 을 쓰세요.

### `bringup_real.sh` 대비 수정 내용

`bringup_real.sh` 는 `$HOME/ws_moveit`, `$HOME/ros2_ws`, `$HOME/install` 만 source 했으나,
이 머신에는 `ws_moveit`·`$HOME/install` 이 없고 **프로젝트 워크스페이스를 source 하지 않아**
프로젝트 버전의 doosan-robot2(`dsr_bringup2`/`dsr_controller2`/`dsr_msgs2`)가 로드되지 않았습니다.
`bringup_real_31.sh` 는 스크립트 위치 기준으로 다음을 순서대로 source 합니다:

1. `/opt/ros/humble/setup.bash`
2. `$HOME/ros2_ws/install/setup.bash` (MoveIt 코어 베이스, 있을 때만)
3. `<repo>/ros2-cup-stack/ros2/install/setup.bash` (**프로젝트 오버레이, 마지막 = 최우선**)

3번이 없으면 에러 메시지와 함께 빌드를 안내하고 종료합니다.

### 기동 동작

- **잔존 노드 정리**: `*.launch.py` 래퍼만 종료하면 `ros2_control_node` /
  `robot_state_publisher` / `spawner` / `rviz2` 가 살아남아 단일 Doosan DRFL 세션을
  두고 충돌(`/dsr01/motion/*` 30초 타임아웃)하므로, `/dsr01` 네임스페이스로 한정해
  잔존 노드까지 정리합니다.
- **`dsr_moveit_controller`(JTC) 스폰**: `dsr_bringup2_rviz.launch.py` 는
  `joint_state_broadcaster` + `dsr_controller2` 만 띄웁니다. MoveIt 에는 JTC 도
  필요하므로 스포너를 백그라운드로 실행합니다(컨트롤러 활성화 후 자동 종료).
- 이후 `dsr_bringup2_rviz.launch.py` 를 `model:=m0609 mode:=real host:=<IP> port:=12345`
  로 실행합니다.

> 평소에는 대시보드의 **Bringup 버튼**(bringup-agent, 포트 8099)으로 제어하지만,
> 터미널에서 직접 실제 로봇을 띄울 때 이 스크립트를 사용합니다.

### 종료

```bash
./stop_bringup.sh   # bringup만 종료 (rviz2, move_group, ros2_control 포함)
# 또는 전체 종료
./stop.sh
```

---

## 참고 스크립트

| 스크립트 | 역할 |
|----------|------|
| `rosbridge.sh` | rosbridge_server(포트 9090) 단독 기동. `start.sh` 가 호출. 미설치 시 `ros-humble-rosbridge-suite` 자동 설치. |
| `stop_bringup.sh` | bringup 관련 프로세스만 종료(tmux bringup 창에 Ctrl+C → SIGINT → SIGKILL 순). 시스템 나머지는 유지. |
| `bringup_real.sh` | 구버전 실제 로봇 bringup. "31" 머신에서는 경로 문제로 동작하지 않으므로 `bringup_real_31.sh` 사용. |
| `bringup_sim.sh` | 시뮬레이션 모드 bringup. |
| `bringup_agent.py` | 대시보드 Bringup 버튼이 호출하는 제어 에이전트(포트 8099). |

## 일반적인 기동 순서

```bash
cd server
./start.sh                          # 1. 시스템 전체 기동 (rosbridge/카메라/agent/server)
./bringup_real_31.sh 192.168.137.100  # 2. (선택) 터미널에서 실제 로봇 직접 bringup
                                    #    또는 대시보드 Bringup 버튼 사용
# ... 작업 ...
./stop.sh                           # 3. 전체 종료
```
