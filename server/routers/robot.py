"""REST API endpoints for robot + gripper control."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domains.cup_detection import CupDetectionDomain
from ..domains.robot import RobotDomain
from ..ros.launch import ALL_COMMANDS
from ..schemas import (
    BringupRequest,
    CupDetectionFrame,
    EEPositionSchema,
    GripperRequest,
    GripperResponse,
    MoveRequest,
    MoveResponse,
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
    TaskLogResponse,
    TaskStartedResponse,
    TaskStartRequest,
    TaskStopRequest,
    TaskStoppedResponse,
    WorkspaceLimitsResponse,
)

router = APIRouter(prefix="/api/robot", tags=["robot"])

robot_domain: RobotDomain | None = None
cup_detection_domain: CupDetectionDomain | None = None


def set_robot_domain(domain: RobotDomain) -> None:
    global robot_domain
    robot_domain = domain


def set_cup_detection_domain(domain: CupDetectionDomain) -> None:
    global cup_detection_domain
    cup_detection_domain = domain


def _get_domain() -> RobotDomain:
    if robot_domain is None:
        raise HTTPException(status_code=503, detail="Robot domain not initialized")
    return robot_domain


def _get_cup_domain() -> CupDetectionDomain:
    if cup_detection_domain is None:
        raise HTTPException(status_code=503, detail="Cup detection domain not initialized")
    return cup_detection_domain


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

    cp(center)가 아직 설정 안 됐고 HOME EE 좌표도 수신 못했으면 503.
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
        return await domain.pyramid_skill(body.x, body.y, body.slot)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        msg = str(e)
        status = 409 if msg.startswith("409") else 502
        raise HTTPException(status_code=status, detail=msg)


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


@router.get("/cups", response_model=CupDetectionFrame)
async def get_cups() -> dict:
    return _get_cup_domain().get_cups()
