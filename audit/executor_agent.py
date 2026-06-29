import os
import json
import time
import subprocess
from typing import Dict, Any


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

CODE_REVIEW_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "code_review", "code_review.json"
)

GENERATED_CODE_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "generated_code", "generated_code.py"
)

EXECUTION_DIR = os.path.join(
    PROJECT_ROOT, "outputs", "execution"
)

EXECUTION_RESULT_PATH = os.path.join(
    EXECUTION_DIR, "execution_result.json"
)


def ensure_output_dir() -> None:
    os.makedirs(EXECUTION_DIR, exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def validate_review(review: Dict[str, Any]) -> None:
    status = review.get("status")
    if status != "approved":
        raise RuntimeError(
            f"Code is not approved for execution. Current review status: {status}"
        )


def run_generated_code(plan_path: str) -> Dict[str, Any]:
    if not os.path.exists(GENERATED_CODE_PATH):
        raise FileNotFoundError(f"Generated code file not found: {GENERATED_CODE_PATH}")

    start_time = time.time()

    result = subprocess.run(
        ["python", GENERATED_CODE_PATH, plan_path],  # <-- pass plan path here
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=600
    )

    end_time = time.time()

    return {
        "command": f"python {GENERATED_CODE_PATH} {plan_path}",
        "return_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_seconds": round(end_time - start_time, 3),
        "status": "success" if result.returncode == 0 else "failed"
    }


def main() -> None:
    ensure_output_dir()

    review = load_json(CODE_REVIEW_PATH)
    validate_review(review)

    plan_path = os.path.join(
        PROJECT_ROOT, "outputs", "planner_report", "cleaning_plan_llm.json"
    )

    try:
        execution_result = run_generated_code(plan_path)
    except subprocess.TimeoutExpired as e:
        execution_result = {
            "command": f"python {GENERATED_CODE_PATH}",
            "return_code": None,
            "stdout": e.stdout if e.stdout else "",
            "stderr": e.stderr if e.stderr else "Execution timed out.",
            "duration_seconds": 600,
            "status": "failed"
        }
    except Exception as e:
        execution_result = {
            "command": f"python {GENERATED_CODE_PATH}",
            "return_code": None,
            "stdout": "",
            "stderr": str(e),
            "duration_seconds": 0,
            "status": "failed"
        }

    save_json(EXECUTION_RESULT_PATH, execution_result)

    print(f"Execution result saved to {EXECUTION_RESULT_PATH}")


if __name__ == "__main__":
    main()