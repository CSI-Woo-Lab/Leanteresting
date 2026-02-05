# Lean Interact + DSPy: LLM-Powered Formal Verification Interface

A bridge between Large Language Models (via DSPy ReAct agents) and Lean 4, enabling LLMs to interactively build and verify formal proofs.

**Purpose**: An **interface layer** that connects LLMs to Lean's proof assistant, not a standalone theorem prover.

---

## 📦 Installation

### 1. Install Lean 4 & Mathlib

**Install Lean:**
```bash
# Linux/macOS
curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh

# Windows
curl -O --location https://raw.githubusercontent.com/leanprover/elan/master/elan-init.ps1
powershell -ExecutionPolicy Bypass -f elan-init.ps1
```

**Verify installation:**
```bash
elan --version
lean --version
lake --version
```

---

**Create Lean Project with Mathlib:**
```bash
lake new my_lean_project math
cd my_lean_project
lake exe cache get  # Download precompiled Mathlib (saves hours!)
lake build
```

---

### 2. Install Python Dependencies

```bash
pip install fastapi uvicorn lean-interact requests dspy-ai google-generativeai
```

---

## 🚀 Quick Start

### Step 1: Start Lean Server

Place `server.py` in your Lean project directory:

```bash
cd my_lean_project
python server.py  # Runs at http://localhost:12345
```

**Expected output:**
```
🔄 Lean Interact Server Initializing...
✅ Lean Interact Server is Ready!
INFO:     Uvicorn running on http://0.0.0.0:12345
```

---

### Step 2: Configure DSPy with Gemini (Recommended)

```python
import dspy

# Configure Gemini (Recommended)
lm = dspy.Google(model='gemini/gemini-3-flash-preview', max_tokens=3000)

dspy.settings.configure(lm=lm)
```

**Note**: This system is optimized for Gemini models.

---

## 🔧 Core Components

### 1. Lean Client

**File**: `lean_client.py`

```python
from lean_client import LeanClientNLP

# Initialize client
client = LeanClientNLP(host="localhost", port=12345)
```

**Available Methods:**

```python
# Execute Lean code
success, response = client.step(code: str)

# Get tactic information
success, response = client.tactics(code: str)

# Get syntax tree (options: "full", "tactics", "original", "substantive")
success, response = client.info_tree(code: str, option: str)

# Apply tactic to proof state
success, response = client.apply_tactic(tactic: str, proof_state: int)

# Reset session
client.reset()
```

---

### 2. Lean ReAct Tools

**Tool Collection for DSPy ReAct Agent:**

```python
class LeanReActTools:
    """
    A collection of tools exposed to the DSPy ReAct Agent.
    Includes capability to add code, run interactive tactics, and reset sessions.
    """

    def __init__(self, client: LeanClientNLP):
        self.client = client

    def add_lean_code(self, code_snippet: str) -> str:
        """
        Tool: Add Lean Code.
        Submit a code snippet (definition, axiom, lemma, theorem) to Lean.
        """
        success, response = self.client.step(code_snippet)

        if success:
            msgs = getattr(response, 'messages', [])
            msg_str = "\n".join([f"[{m.severity}] {m.data}" for m in msgs]) if msgs else "No warnings."
            return f"✅ Code accepted.\nServer Messages: {msg_str}"
        else:
            if response is None:
                return "❌ Critical Error: Server returned no response."

            # Extract error details for the LLM
            msgs = getattr(response, 'messages', [])
            errors = [f"Line {getattr(m.start_pos, 'line', '?')}: {m.data}" 
                     for m in msgs if m.severity == 'error']
            return f"❌ Verification Failed (State rolled back).\nErrors:\n" + "\n".join(errors)

    def submit_tactic(self, tactic: str, proof_state_id: int) -> str:
        """
        Tool: Submit Tactic.
        Apply a tactic to a specific open proof state.

        Args:
            tactic: The tactic string (e.g., "intro h", "simp", "apply lemma_1").
            proof_state_id: The integer ID of the proof state (must be obtained from previous server messages).
        """
 
        success, response = self.client.apply_tactic(tactic, proof_state_id)

        if success:
            # Tactic success might return a new goal state
            return f"✅ Tactic applied.\nResult: {response}"
        else:
            return f"❌ Tactic failed.\nResult: {response}"

    def reset_session(self) -> str:
        """
        Tool: Reset Session.
        Clears all previous definitions and code history.
        """
        self.client.reset()
        return "🔄 Session verified and reset. Ready for new code."
```

---

### 3. DSPy ReAct Agent

**Agent Signature Example:**

```python
import dspy

class LeanProverAgent(dspy.Signature):
    """
    You are an expert Formal Verification Engineer using Lean 4.

    STRICT RULE: DISTINGUISH BETWEEN 'TRUST' AND 'PROOF'.
    1. Axioms (`axiom`): ONLY for external library guarantees (e.g., "scipy is correct").
    2. Lemmas (`lemma`): Intermediate facts about the specific problem that MUST BE PROVED.
       - NEVER use `axiom` for lemmas.
       - You must write a proof script (`by ...`) for every lemma.
    3. Theorem (`theorem`): The final goal, proved using Axioms + Lemmas.

    Workflow:
    1. Define the `context_axioms` (The "Tools").
    2. Define AND PROVE the `lemmas` (The "Material").
       - Example: Prove the matrix is symmetric, or the function is quadratic.
    3. Finally, prove the `goal_theorem` using the axioms applied to the lemmas.
    You may get help from tactic tool.
    Minimize the use of 'sorry'; complete all proofs if feasible.
    """

    context_axioms: str = dspy.InputField(
        desc="Trusted external assumptions (e.g., 'If func is convex, solver finds min'). "
             "Declare these using `axiom`."
    )

    lemmas: str = dspy.InputField(
        desc="Intermediate properties of the SPECIFIC problem code (e.g., 'This cost function J is convex'). "
             "You MUST implementation the proof for these using `:= by ...`. "
             "DO NOT declare these as axioms."
    )

    goal_theorem: str = dspy.InputField(
        desc="The final statement to verify. Prove it by combining the context_axioms (tools) and lemmas (facts)."
    )

    final_verified_code: str = dspy.OutputField(
        desc="The complete Lean 4 code. It must contain actual proofs for lemmas and the theorem."
    )
```

---

**ReAct Module Setup:**

```python
class ReActExample(dspy.Module):

    def __init__(self):
        super().__init__()
        self.lean_tools = LeanReActTools(global_client)
        self.tools_list = [
            self.lean_tools.add_lean_code,
            self.lean_tools.submit_tactic,
            self.lean_tools.reset_session,
        ]
        self.lean_prover = dspy.ReAct(LeanProverAgent, tools=self.tools_list, max_iters=20)
```

---

## 📖 Usage

### Basic Setup

```python
import dspy
from lean_client import LeanClientNLP

# Configure Gemini (Recommended)
lm = dspy.Google(model='gemini/gemini-3-flash-preview', max_tokens=3000)
dspy.settings.configure(lm=lm)

# Initialize Lean client
global_client = LeanClientNLP(host="localhost", port=12345)

# Create ReAct agent
prover = ReActExample()
```

---

## 📊 Server API Reference

### Endpoint: `POST /run`

Execute Lean code and get response.

**Request:**
```python
payload = {
    "cmd": "def add (x y : Nat) : Nat := x + y",
    "env": 0  # optional: environment ID
}
```

**Response:**
```python
{
    "result": "CommandResponse(...)",
    "env": 1
}
```

---

### Endpoint: `POST /proof_step`

Apply tactic to proof state.

**Request:**
```python
payload = {
    "tactic": "intro h",
    "proof_state": 42
}
```

**Response:**
```python
{
    "result": "ProofStepResponse(...)",
    "proof_state": 43  # or None if proof complete
}
```

---

## 🔄 Client API Reference

### LeanClientNLP

**Constructor:**
```python
LeanClientNLP(host="localhost", port=12345)
```

**Methods:**

```python
# Basic execution with rollback
@with_rollback
def step(code: str) -> Tuple[bool, CommandResponse]

# Get tactics information
@with_rollback
def tactics(code: str) -> Tuple[bool, CommandResponse]

# Get info tree
@with_rollback
def info_tree(code: str, option: Literal["full", "tactics", "original", "substantive"]) 
    -> Tuple[bool, CommandResponse]

# Apply tactic (no rollback)
def apply_tactic(tactic: str, proof_state: int) -> Tuple[bool, ProofStepResponse]

# Reset session
def reset() -> None
```

**Return Format:**
- `Tuple[bool, Response]` where `bool` indicates success (no errors)
- On success: `current_code` and `last_env` are updated
- On failure: state is rolled back to previous values

---


**Important**: Server must be run in a directory containing a valid Lean project (`lakefile.lean`).

---

## 📝 Response Structures

### CommandResponse

```python
response.env            # int: Environment ID
response.messages       # List[Message]: Errors/warnings
response.sorries        # List[Sorry]: Sorry locations
response.trees          # List[InfoTree]: Syntax trees (if info_tree used)
response.all_tactics    # List[Tactic]: Tactic info (if tactics used)
```

### ProofStepResponse

```python
response.proof_state    # int or None: Next proof state ID
response.messages       # List[Message]: Errors/warnings
```

### Message

```python
message.severity    # str: 'error', 'warning', 'info'
message.data       # str: Message content
message.start_pos  # Pos: Start position
message.end_pos    # Pos: End position
```

---
 