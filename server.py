from contextlib import asynccontextmanager
from typing import Dict, Any
from fastapi import FastAPI, HTTPException, Request
from lean_interact import LeanREPLConfig, LeanServer, Command, LocalProject, ProofStep
from lean_interact.interface import InfoTreeOptions



@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🔄 Lean Interact Server Initializing...")
    try:
        config = LeanREPLConfig(project=LocalProject(directory='./'))
        app.state.lean_server = LeanServer(config)
        print("✅ Lean Interact Server is Ready!")
    except Exception as e:
        print(f"❌ Fail to Initialization: {e}")
        raise e

    yield

    print("🛑 Shut Down...")


app = FastAPI(lifespan=lifespan)

option_map = {"full": InfoTreeOptions.full, "tactics": InfoTreeOptions.tactics,
              "original": InfoTreeOptions.original, "substantive": InfoTreeOptions.substantive}
# server.py의 run_lean_command 함수 수정

@app.post("/run")
def run_lean_command(request: Request, payload: Dict[str, Any]):
    lean_server = getattr(request.app.state, "lean_server", None)
    if not lean_server:
        raise HTTPException(status_code=500, detail="Server not initialized")

    try:
        if payload is not None:
            if "infotree" in payload.keys():
                option = payload["infotree"]
                payload['infotree'] = option_map[option]

        cmd_obj = Command(**payload)
        print("cmd obj", cmd_obj)
        result = lean_server.run(cmd_obj)


        return {
            "result": str(result),
            "env": getattr(result, "env", None)  # result 객체에서 env 값 추출
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution Error: {str(e)}")

@app.post("/proof_step")
def run_proof_step(request: Request, payload: Dict[str, Any]):
    lean_server = getattr(request.app.state, "lean_server", None)
    if not lean_server:
        raise HTTPException(status_code=500, detail="Server not initialized")

    step_obj = ProofStep(**payload)
    print("proof step obj", step_obj)
    result = lean_server.run(step_obj)

    return {
        "result": str(result),
        "proof_state": getattr(result, "proof_state", None)
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=12345)
