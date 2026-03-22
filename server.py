import gc
import asyncio
import psutil
import os
from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from lean_interact import LeanREPLConfig, LeanServer, Command, LocalProject, ProofStep
from lean_interact.interface import InfoTreeOptions

MEMORY_LIMIT_MB = 4000
REQUEST_LIMIT = 200

standby_pool = asyncio.Queue(maxsize=5)
swap_lock = asyncio.Lock()
request_count = 0


def get_memory_mb():
    return psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024


def create_and_init_server():
    print("⏳ Starting a new Lean Server...")
    config = LeanREPLConfig(project=LocalProject(directory='./'))
    server = LeanServer(config)
    init_result = server.run(Command(cmd="-- init"))
    base_env = getattr(init_result, "env", None)
    del init_result
    gc.collect()
    print(f"✅ New Lean Server ready! (env: {base_env})")
    return { "server": server, "base_env": base_env }


async def fill_standby_pool():
    if standby_pool.full():
        return
    loop = asyncio.get_event_loop()
    try:
        new_server_data = await loop.run_in_executor(None, create_and_init_server)
        await standby_pool.put(new_server_data)
    except Exception as e:
        print(f"❌ Failed to create standby server: {e}")


def kill_old_server(old_server_data):
    if not old_server_data:
        return
    print("🗑️ Killing old Lean server...")
    server = old_server_data.get("server")
    if server:
        if hasattr(server, 'proc') and server.proc:
            server.proc.kill()
        if hasattr(server, 'kill'):
            server.kill()
        elif hasattr(server, 'close'):
            server.close()
    del old_server_data
    gc.collect()
    print(f"✅ Old server killed. Memory: {get_memory_mb():.1f}MB")


def should_swap():
    mem = get_memory_mb()
    if mem > MEMORY_LIMIT_MB:
        print(f"⚠️ Memory limit exceeded: {mem:.1f}MB > {MEMORY_LIMIT_MB}MB")
        return True
    if request_count > 0 and request_count % REQUEST_LIMIT == 0:
        print(f"⚠️ Request limit reached: {request_count}")
        return True
    return False


async def force_swap_server(app, background_tasks):
    """에러 발생 시 강제 교체"""
    async with swap_lock:
        if standby_pool.empty():
            print("⚠️ No standby server for error recovery")
            return False

        print("🔄 Force swapping due to error...")
        old_lean_data = app.state.active_lean
        app.state.active_lean = await standby_pool.get()

        background_tasks.add_task(kill_old_server, old_lean_data)
        background_tasks.add_task(fill_standby_pool)
        return True


async def maybe_swap_server(app, background_tasks):
    async with swap_lock:
        if not should_swap():
            return

        if standby_pool.empty():
            print("⚠️ Need swap but no standby server available")
            gc.collect()
            return

        print("🔄 Auto-swapping server...")
        old_lean_data = app.state.active_lean
        app.state.active_lean = await standby_pool.get()

        background_tasks.add_task(kill_old_server, old_lean_data)
        background_tasks.add_task(fill_standby_pool)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🔄 Initializing...")
    app.state.active_lean = create_and_init_server()

    async def init_pool_sequentially():
        for i in range(5):
            await fill_standby_pool()
            print(f"📦 Standby pool: {i + 1}/5")

    asyncio.create_task(init_pool_sequentially())
    yield

    print("🛑 Shutting Down...")
    kill_old_server(app.state.active_lean)
    while not standby_pool.empty():
        kill_old_server(standby_pool.get_nowait())


app = FastAPI(lifespan=lifespan)

option_map = {
    "full": InfoTreeOptions.full,
    "tactics": InfoTreeOptions.tactics,
    "original": InfoTreeOptions.original,
    "substantive": InfoTreeOptions.substantive
}


@app.post("/run")
async def run_lean_command(request: Request, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    global request_count
    request_count += 1

    await maybe_swap_server(request.app, background_tasks)

    if payload.pop("is_new_session", False):
        async with swap_lock:
            if standby_pool.empty():
                payload["env"] = request.app.state.active_lean["base_env"]
            else:
                old_lean_data = request.app.state.active_lean
                request.app.state.active_lean = await standby_pool.get()
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

        response = {
            "result": str(result),
            "env": getattr(result, "env", None)
        }
        del result
        return response

    except Exception as e:
        print(f"❌ Error occurred: {e}")
        swapped = await force_swap_server(request.app, background_tasks)
        detail = f"Execution Error: {str(e)}"
        if swapped:
            detail += " (server swapped, retry may work)"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/proof_step")
async def run_proof_step(request: Request, payload: Dict[str, Any], background_tasks: BackgroundTasks):
    global request_count
    request_count += 1

    await maybe_swap_server(request.app, background_tasks)

    active_server = request.app.state.active_lean.get("server")
    if not active_server:
        raise HTTPException(status_code=500, detail="Server not initialized")

    try:
        step_obj = ProofStep(**payload)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, active_server.run, step_obj)

        response = {
            "result": str(result),
            "proof_state": getattr(result, "proof_state", None)
        }
        del result
        return response

    except Exception as e:
        print(f"❌ Error occurred: {e}")
        swapped = await force_swap_server(request.app, background_tasks)
        detail = f"Execution Error: {str(e)}"
        if swapped:
            detail += " (server swapped, retry may work)"
        raise HTTPException(status_code=500, detail=detail)


@app.get("/health")
async def health():
    return {
        "memory_mb": round(get_memory_mb(), 1),
        "memory_limit_mb": MEMORY_LIMIT_MB,
        "request_count": request_count,
        "standby_servers": standby_pool.qsize()
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=23456)