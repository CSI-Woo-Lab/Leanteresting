import requests
from functools import wraps
from typing import Any, Dict, Callable, Tuple, Literal
from typing import Optional
from lean_interact.interface import (InfoTree, TermNode, Syntax,
                                     Range, CommandResponse, Sorry, Tactic, ProofStepResponse, Pos,
                                     Message, TacticNode, CommandNode)
import inspect
import lean_interact.interface as lean_types

eval_context = {
    name: obj
    for name, obj in inspect.getmembers(lean_types, inspect.isclass)
}
eval_context["null"] = None


# --- Low Level Client ---
class LeanClient:
    def __init__(self, host: str = "localhost", port: int = 12345, timeout: int = 60):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout

    def run(self, cmd: str, **kwargs: Any) -> Dict[str, Any]:
        url = f"{self.base_url}/run"
        payload = { "cmd": cmd }
        payload.update(kwargs)
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def proof_step(self, tactic: str, proof_state: int) -> Dict[str, Any]:
        url = f"{self.base_url}/proof_step"
        payload = { "tactic": tactic, "proof_state": proof_state }
        response = requests.post(url, json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json()


def with_rollback(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(self, additional_code: str, *args, **kwargs) -> Tuple[bool, CommandResponse]:
        prev_env = self.last_env
        prev_code = self.current_code

        response_obj, new_env = func(self, additional_code, *args, **kwargs)

        if response_obj is None:
            return False, None

        messages = response_obj.messages or []
        has_error = any(msg.severity == 'error' for msg in messages)

        if not has_error:
            self.last_env = new_env
            self.current_code = f"{prev_code}\n\n{additional_code}" if prev_code else additional_code
            return True, response_obj

        return False, response_obj

    return wrapper


class LeanClientNLP:
    def __init__(self, host: str = "localhost", port: int = 12345):
        self.client = LeanClient(host, port)
        self.current_code: str = ""
        self.last_env: Optional[int] = None
        self.eval_context = eval_context

    def _run(self, code: str, *, env: Optional[int] = None, **kwargs) -> Tuple[Optional[CommandResponse], int]:
        response_dict = self.client.run(cmd=code, env=env, **kwargs)

        if "error" in response_dict:
            return None, -1

        result_str = response_dict.get('result', '')

        parsed_obj = eval(result_str, { }, self.eval_context)
        new_env = getattr(parsed_obj, 'env', -1)
        return parsed_obj, new_env

    def reset(self):
        self.current_code = ""
        self.last_env = None
        print("🔄 Session Reset.")

    @with_rollback
    def step(self, additional_code: str) -> Tuple[Optional[CommandResponse], int]:
        return self._run(code=additional_code, env=self.last_env)

    @with_rollback
    def tactics(self, code: str) -> Tuple[Optional[CommandResponse], int]:
        return self._run(code, env=self.last_env, all_tactics=True)

    @with_rollback
    def info_tree(self, additional_code: str,
                  option: Literal["full", "tactics", "original", "substantive"] = "full"
                  ) -> Tuple[Optional[CommandResponse], int]:
        return self._run(code=additional_code, env=self.last_env, infotree=option)

    def apply_tactic(self, tactic: str, proof_state: int) -> Tuple[bool, Optional[ProofStepResponse]]:
        """sorry 상태에서 tactic 적용"""
        response_dict = self.client.proof_step(tactic=tactic, proof_state=proof_state)
        print(response_dict)

        result_str = response_dict.get('result', '')
        parsed_obj = eval(result_str, { }, self.eval_context)

        messages = getattr(parsed_obj, 'messages', None) or []
        has_error = any(m.severity == 'error' for m in messages)
        return not has_error, parsed_obj
