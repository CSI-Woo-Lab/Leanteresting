import gc
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from lean_interact import LeanREPLConfig, LeanServer, Command, LocalProject, ProofStep
from lean_interact.interface import InfoTreeOptions

# 대기 중인 서버 큐
standby_pool = asyncio.Queue(maxsize=5)


def create_and_init_server():
    """시간이 오래 걸리는 서버 생성 및 Mathlib 초기화 작업"""
    print("⏳ Starting a new Lean Server and loading Mathlib...")
    config = LeanREPLConfig(project=LocalProject(directory='./'))
    server = LeanServer(config)
    init_result = server.run(Command(cmd="-- init"))
    base_env = getattr(init_result, "env", None)
    print(f"✅ New Lean Server is warmed up and ready! (env: {base_env})")
    return { "server": server, "base_env": base_env }


async def fill_standby_pool():
    """백그라운드에서 새 서버를 준비"""
    loop = asyncio.get_event_loop()
    new_server_data = await loop.run_in_executor(None, create_and_init_server)
    await standby_pool.put(new_server_data)


def kill_old_server(old_server_data):
    """메모리가 새는 낡은 서버를 확실하게 죽이고 메모리 회수"""
    if old_server_data and old_server_data.get("server"):
        print("🗑️ Killing old Lean server to free memory...")
        old_server = old_server_data["server"]
        if hasattr(old_server, 'kill'):
            old_server.kill()
        elif hasattr(old_server, 'close'):
            old_server.close()
        del old_server
    gc.collect()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🔄 Initializing Lean Interact System...")
    app.state.active_lean = create_and_init_server()
    asyncio.create_task(fill_standby_pool())
    yield
    print("🛑 Shutting Down...")
    kill_old_server(app.state.active_lean)
    if not standby_pool.empty():
        kill_old_server(standby_pool.get_nowait())


app = FastAPI(lifespan=lifespan)

option_map = {
    "full": InfoTreeOptions.full, "tactics": InfoTreeOptions.tactics,
    "original": InfoTreeOptions.original, "substantive": InfoTreeOptions.substantive
}


@app.post("/run")
async def run_lean_command(request: Request, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    # 클라이언트가 보내는 API 페이로드 스펙은 기존과 동일함
    if payload.pop("is_new_session", False):
        print("🔄 New session requested! Swapping to standby server...")
        old_lean_data = request.app.state.active_lean

        try:
            request.app.state.active_lean = standby_pool.get_nowait()
        except asyncio.QueueEmpty:
            raise HTTPException(status_code=503,
                                detail="Standby server is still warming up. Please wait a few seconds.")

        payload["env"] = request.app.state.active_lean["base_env"]

        background_tasks.add_task(kill_old_server, old_lean_data)
        background_tasks.add_task(fill_standby_pool)

    active_server = request.app.state.active_lean.get("server")
    base_env = request.app.state.active_lean.get("base_env")

    if not active_server:
        raise HTTPException(status_code=500, detail="Server not initialized")

    if "env" not in payload:
        payload["env"] = base_env

    try:
        if "infotree" in payload:
            payload['infotree'] = option_map.get(payload["infotree"], InfoTreeOptions.tactics)

        cmd_obj = Command(**payload)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, active_server.run, cmd_obj)

        return {
            "result": str(result),
            "env": getattr(result, "env", None)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution Error: {str(e)}")


@app.post("/proof_step")
async def run_proof_step(request: Request, payload: Dict[str, Any]):
    # 클라이언트 변경 없음!
    active_server = request.app.state.active_lean.get("server")
    if not active_server:
        raise HTTPException(status_code=500, detail="Server not initialized")

    try:
        step_obj = ProofStep(**payload)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, active_server.run, step_obj)

        return {
            "result": str(result),
            "proof_state": getattr(result, "proof_state", None)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution Error: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=23456)
