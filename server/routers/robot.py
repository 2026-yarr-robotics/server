"""REST API endpoints for robot + gripper control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.fallen_cup import FallenCupDomain
from ..domains.robot import RobotDomain
from ..ros.launch import ALL_COMMANDS
from ..schemas import (
    BringupRequest,
    EEPositionSchema,
    FallenCupDetectionStartRequest,
    FallenCupRecoveryRequest,
    FallenCupStateResponse,
    GripperRequest,
    GripperResponse,
    MoveRequest,
    MoveResponse,
    OutlierCupRecoveryRequest,
    PickSkillRequest,
    PickSkillResponse,
    PixelToWorldResponse,
    PyramidConfigResponse,
    PyramidConfigUpdate,
    PyramidSkillRequest,
    PyramidSkillResponse,
    RobotStatusResponse,
    ScanSkillResponse,
    ScanSquareSkillResponse,
    StopAllRequest,
    StopAllResponse,
    TaskLogResponse,
    TaskStartedResponse,
    TaskStartRequest,
    TaskStopRequest,
    TaskStoppedResponse,
    UnstackAllSkillRequest,
    UnstackAllSkillResponse,
    UnstackSkillRequest,
    UnstackSkillResponse,
    UserCommandRequest,
    UserCommandResponse,
    WorkspaceLimitsResponse,
)

router = APIRouter(prefix="/api/robot", tags=["robot"])

robot_domain: RobotDomain | None = None
fallen_cup_domain: FallenCupDomain | None = None


def set_robot_domain(domain: RobotDomain) -> None:
    global robot_domain
    robot_domain = domain


def set_fallen_cup_domain(domain: FallenCupDomain) -> None:
    global fallen_cup_domain
    fallen_cup_domain = domain


def _get_domain() -> RobotDomain:
    if robot_domain is None:
        raise HTTPException(status_code=503, detail="Robot domain not initialized")
    return robot_domain


def _get_fallen_cup_domain() -> FallenCupDomain:
    if fallen_cup_domain is None:
        raise HTTPException(status_code=503, detail="Fallen cup domain not initialized")
    return fallen_cup_domain


@router.get("/status", response_model=RobotStatusResponse)
async def get_status() -> dict:
    return _get_domain().get_status()


@router.post("/bringup", response_model=TaskStartedResponse)
async def start_bringup(body: BringupRequest) -> dict:
    mode = body.mode
    command = f"bringup_{mode}"
    if command not in ALL_COMMANDS:
        raise HTTPException(status_code=400, detail=f"Invalid mode: {mode}")

    args: dict = {}
    if mode == "real":
        args["ip"] = body.ip

    domain = _get_domain()
    try:
        return await domain.start_task(command, args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/start", response_model=TaskStartedResponse)
async def start_task(body: TaskStartRequest) -> dict:
    task_name = body.task
    if task_name not in ALL_COMMANDS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid task. Choose from: {sorted(ALL_COMMANDS)}",
        )

    domain = _get_domain()
    try:
        return await domain.start_task(task_name, body.args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/task/stop", response_model=TaskStoppedResponse)
async def stop_task(body: TaskStopRequest) -> dict:
    domain = _get_domain()
    try:
        return await domain.stop_task(body.name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/stop", response_model=StopAllResponse)
async def stop_all(body: StopAllRequest | None = None) -> dict:
    """실행 중인 skill/task 를 즉시 멈추고 팔을 HOME 으로 복귀시킨다.

    통합 정지(패닉/abort) 버튼용. 진행 중인 동기 skill(pyramid/unstack/…)은
    skill_api_node 의 ``/stop`` 으로 인터럽트+HOME 하고, action task(fallen/
    outlier/agent)는 프로세스를 kill 한다. ``task/stop`` 과 달리 정지할 대상
    이름이 필요 없다 — 무엇이 돌고 있든 멈춘다.
    """
    domain = _get_domain()
    home = True if body is None else body.home
    return await domain.stop_all(home=home)


@router.get("/task/log", response_model=TaskLogResponse)
async def get_task_log(name: str = "", tail: int = 50) -> dict:
    if not name:
        raise HTTPException(status_code=400, detail="Missing 'name' query param")
    if not (1 <= tail <= 500):
        raise HTTPException(status_code=400, detail="tail must be between 1 and 500")

    domain = _get_domain()
    try:
        lines = await domain.get_log(name, tail)
        return {"name": name, "log": lines}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/position", response_model=EEPositionSchema)
async def get_ee_position() -> dict:
    """Return the current end-effector position from the robot's /ee_pose.

    No fallback to the last commanded target: if /ee_pose has not been
    received the position is reported as unavailable (404).
    """
    pos = _get_domain().get_ee_position()
    if pos is None:
        raise HTTPException(
            status_code=404,
            detail="End-effector pose unavailable — /ee_pose not received (is bringup running?)",
        )
    return pos


@router.get("/config/workspace", response_model=WorkspaceLimitsResponse)
async def get_workspace_config() -> dict:
    return _get_domain().move_limits


@router.post("/gripper", response_model=GripperResponse)
async def gripper_control(body: GripperRequest) -> dict:
    command = body.command.strip().lower()
    if command not in ("open", "close"):
        raise HTTPException(status_code=400, detail="command must be 'open' or 'close'")
    domain = _get_domain()
    try:
        return await domain.gripper_control(command)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/move", response_model=MoveResponse)
async def move_robot(body: MoveRequest) -> dict:
    domain = _get_domain()
    try:
        return await domain.move_to(body.x, body.y, body.z, body.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/command", response_model=UserCommandResponse)
async def run_agent(body: UserCommandRequest) -> dict:
    """자연어 명령으로 cup_stack_agent LLM 루프를 실행한다.

    프론트엔드 Command 박스에서 ``/`` 접두 없이 입력한 일반 텍스트가 여기로
    들어온다 (``/`` 접두는 직접 로봇 명령용). 텍스트는 agent 의 ``start.sh
    --real-api`` 를 로컬 서브프로세스로 띄우며 ``USER_COMMAND`` 로 전달된다.
    """
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text must not be empty")
    domain = _get_domain()
    try:
        return await domain.run_agent(text)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except (RuntimeError, OSError) as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/pixel-to-world", response_model=PixelToWorldResponse)
async def pixel_to_world(px: int, py: int) -> dict:
    domain = _get_domain()
    try:
        return domain.pixel_to_world(px, py)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/skill/pick", response_model=PickSkillResponse)
async def skill_pick(body: PickSkillRequest) -> dict:
    """Pick one cup at the given **cup top centre** coordinate.

    Proxies to the ROS 2 skill_api_node (PickCupSkill). Supply
    ``cup_top_z`` (converted to gripper Z server-side), ``z`` (raw
    gripper Z), or ``nested_count`` (ROS 2 derives Z from the nested
    stack height).
    """
    domain = _get_domain()
    try:
        return await domain.pick_skill(
            body.x,
            body.y,
            cup_top_z=body.cup_top_z,
            z=body.z,
            nested_count=body.nested_count,
            ori=body.ori,
            nested=body.nested,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


@router.get("/config/pyramid", response_model=PyramidConfigResponse)
async def get_pyramid_config() -> dict:
    """현재 피라미드 설정과 6개 슬롯의 절대 place 좌표 캐시.

    cp(center)가 아직 설정 안 됐으면 config 의 HOME XY 로 lazy 초기화한다.
    """
    domain = _get_domain()
    try:
        return domain.get_pyramid_config()
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/config/pyramid", response_model=PyramidConfigResponse)
async def update_pyramid_config(body: PyramidConfigUpdate) -> dict:
    """피라미드 설정 갱신 (center / degree / pick_z 중 보낸 필드만)."""
    domain = _get_domain()
    try:
        center = body.center.model_dump() if body.center is not None else None
        return domain.set_pyramid_config(
            center=center,
            degree=body.degree,
            pick_z=body.pick_z,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/skill/pyramid", response_model=PyramidSkillResponse)
async def skill_pyramid(body: PyramidSkillRequest) -> dict:
    """단일 컵을 pick 해서 지정한 피라미드 slot 으로 place.

    cp/degree/pick_z 는 서버의 /config/pyramid 에 저장된 값을 사용.
    """
    domain = _get_domain()
    try:
        return await domain.pyramid_skill(body.x, body.y, body.slot, body.nested)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


@router.post("/skill/unstack", response_model=UnstackSkillResponse)
async def skill_unstack(body: UnstackSkillRequest) -> dict:
    """피라미드 slot 의 컵 하나를 집어 목적지 (x,y) 에 nested 컬럼으로 place.

    pyramid skill 의 역동작. pick 좌표(slot 절대 위치)·pick_z 는 서버의
    /config/pyramid 캐시에서, 목적지 place_z 는 ``nested`` 로부터 계산한다.
    피라미드는 위에서부터(3m → 2r/2l → 1r/1m/1l) 해체해야 한다.
    """
    domain = _get_domain()
    try:
        return await domain.unstack_skill(body.slot, body.x, body.y, body.nested)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


@router.post("/skill/unstack_all", response_model=UnstackAllSkillResponse)
async def skill_unstack_all(body: UnstackAllSkillRequest) -> dict:
    """피라미드 6 컵을 위에서부터 모두 해체해 목적지 (x,y) 한 스택으로 모은다.

    ``script/unstack.sh`` 의 서버측 스킬화 — ``/skill/unstack`` 단위 스킬을
    ``3m → 2r → 2l → 1r → 1m → 1l`` 순서로 6 회 호출하며 매 컵 ``nested`` 를
    1→6 으로 올린다. 단계별로 일시적 실패(409/터널 blip)는 재시도하고,
    모든 재시도 후에도 실패하면 ``success=False`` + ``completed`` 로 부분
    진행을 보고한다 (5xx 를 던지지 않음). 잘못된 목적지 좌표만 422.

    참고: 6 컵 pick-place 라 응답까지 ~수 분 걸릴 수 있다 (단일 장기 요청).
    """
    domain = _get_domain()
    try:
        return await domain.unstack_all_skill(
            body.x, body.y,
            max_retry=body.max_retry,
            retry_delay=body.retry_delay,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/skill/scan", response_model=ScanSkillResponse)
async def skill_scan() -> dict:
    """양쪽 두 방향(pos1, pos2) 스캔 후 초기 위치로 복귀.

    인자 없음. ROS 2 skill_api_node 의 ScanSkill 이 scan.launch.py 를
    실행해 PTP 로 pos1 → pos2 → 초기 위치 순으로 이동하며 각 웨이포인트
    도달 후 dwell_sec 만큼 대기한다.
    """
    domain = _get_domain()
    try:
        return await domain.scan_skill()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


@router.post("/skill/scan_square", response_model=ScanSquareSkillResponse)
async def skill_scan_square() -> dict:
    """카메라 하향 고정, base_link XY 사각형 4 꼭짓점 순회 후 시작 위치 복귀.

    인자 없음. ROS 2 skill_api_node 의 ScanSkill 이 scan_square.launch.py 를
    실행해 HOME EE 높이에서 사각형 둘레(꼭짓점1→2→3→4→1)를 그린 뒤 초기
    joint 자세로 복귀한다. 각 꼭짓점 도달 후 dwell_sec 만큼 대기한다.
    """
    domain = _get_domain()
    try:
        return await domain.scan_square_skill()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


# ── Fallen Cup ────────────────────────────────────────────────────────────────

@router.post("/fallen-cup/detection/start", response_model=TaskStartedResponse)
async def start_fallen_cup_detection(body: FallenCupDetectionStartRequest) -> dict:
    """넘어진 컵 YOLO 인식 노드(fallen_cup_detect)를 시작한다.

    장기 실행 서비스(SERVICE_COMMAND)라 다른 액션 태스크와 병행 가능.
    eye-in-hand(/hand) 카메라 토픽을 사용한다.
    """
    domain = _get_fallen_cup_domain()
    args = domain.build_detection_args(
        conf=body.conf,
        imgsz=body.imgsz,
        use_depth=body.use_depth,
        weights_path=body.weights_path,
    )
    try:
        return await domain.start_detection(args)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/fallen-cup/detection/stop", response_model=TaskStoppedResponse)
async def stop_fallen_cup_detection() -> dict:
    """넘어진 컵 인식 노드를 중지한다 (로봇 모션 정지 없음)."""
    domain = _get_fallen_cup_domain()
    return await domain.stop_detection()


@router.get("/fallen-cup/state", response_model=FallenCupStateResponse)
async def get_fallen_cup_state() -> dict:
    """인식 노드 실행 여부 + 최근 인식 결과(2초 내 갱신분)를 반환한다."""
    return _get_fallen_cup_domain().get_state()


@router.post("/fallen-cup/recovery", response_model=TaskStartedResponse)
async def start_fallen_cup_recovery(body: FallenCupRecoveryRequest) -> dict:
    """넘어진 컵 세우기 태스크(fallen_cup_recovery)를 시작한다.

    1회 실행 태스크: 인식 토픽을 수집해 컵을 잡아 세운 뒤 HOME 복귀 후 종료.
    MoveItPy 컨트롤러 경합 방지를 위해 skill_api 서비스가 떠 있으면 먼저
    중지한다 (다음 pick/pyramid 호출 시 자동 재시작).

    진행 상황: ``/ws/task/log`` · ``/ws/robot/state``
    중지: ``POST /api/robot/task/stop`` ``{"name": "fallen_cup_recovery"}``
    """
    domain = _get_domain()
    try:
        return await domain.start_fallen_cup_recovery(
            mode=body.mode,
            multi_cup=body.multi_cup,
            dry_run=body.dry_run,
            sim=body.sim,
            stand_cup_margin_m=body.stand_cup_margin_m,
            place_safe_z_min=body.place_safe_z_min,
            place_cup_tilt_deg=body.place_cup_tilt_deg,
            place_plus_y_cup_tilt_deg=body.place_plus_y_cup_tilt_deg,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── Outlier Cup ───────────────────────────────────────────────────────────────

@router.post("/outlier-cup/recovery", response_model=TaskStartedResponse)
async def start_outlier_cup_recovery(body: OutlierCupRecoveryRequest) -> dict:
    """outlier 컵 복구 오케스트레이터 태스크(outlier_cup_recovery)를 시작한다.

    1회 실행 상위 집합 태스크: fallen cup 을 base_link 최근접 순으로 전부 세운 뒤,
    mouth-up cup 을 전부 뒤집어 내려놓고 HOME 복귀 후 종료. fallen-only 인
    ``fallen_cup_recovery`` 엔드포인트는 그대로 유지된다. MoveItPy 컨트롤러 경합
    방지를 위해 skill_api 서비스가 떠 있으면 먼저 중지한다 (다음 pick/pyramid
    호출 시 자동 재시작).

    진행 상황: ``/ws/task/log`` · ``/ws/robot/state``
    중지: ``POST /api/robot/task/stop`` ``{"name": "outlier_cup_recovery"}``
    """
    domain = _get_domain()
    try:
        return await domain.start_outlier_cup_recovery(
            mode=body.mode,
            dry_run=body.dry_run,
            sim=body.sim,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
