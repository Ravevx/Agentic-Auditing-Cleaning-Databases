import os
import json
import ast
from typing import Dict, Any, List, Optional
import re 
import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

PLAN_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "planner_report", "cleaning_plan_llm.json"
)

APPROVAL_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "approval_gate", "approval_decision.json"
)

CODE_REVIEW_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "code_review", "code_review.json"
)

GENERATED_CODE_DIR = os.path.join(
    PROJECT_ROOT, "outputs", "generated_code"
)

GENERATED_CODE_PATH = os.path.join(
    GENERATED_CODE_DIR, "generated_code.py"
)

RAW_LLM_OUTPUT_PATH = os.path.join(
    GENERATED_CODE_DIR, "generated_code_raw.txt"
)

CODER_METADATA_PATH = os.path.join(
    GENERATED_CODE_DIR, "coder_metadata.json"
)

LMSTUDIO_API_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "meta-llama-3.1-8b-instruct"


def ensure_output_dir() -> None:
    os.makedirs(GENERATED_CODE_DIR, exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_optional_json(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def strip_code_fences(text: str) -> str:
    """Remove all markdown code fences, wherever they appear in the text."""
    text = text.strip()
    
    # Remove opening fence on its own line (```python, ```py, or just ```)
    text = re.sub(r'^```[a-zA-Z]*\s*\n', '', text)
    
    # Remove closing fence on its own line
    text = re.sub(r'\n```\s*$', '', text)
    
    # If the LLM still snuck fences in the middle (like line 137 suggests),
    # strip any remaining ``` lines entirely
    lines = text.splitlines()
    lines = [line for line in lines if not re.match(r'^\s*```', line)]
    text = '\n'.join(lines)
    
    return text.strip()


def call_llm(messages: List[Dict[str, str]], temperature: float = 0.0, timeout=(10, 600)) -> str:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 3200,
    }

    response = requests.post(
        f"{LMSTUDIO_API_BASE}/chat/completions",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "Unexpected response from LM Studio:\n" + json.dumps(data, indent=2)
        )

    if content is None or not isinstance(content, str) or not content.strip():
        raise RuntimeError("LM Studio returned empty or invalid content.")

    return content.strip()


def validate_approval(approval: Dict[str, Any]) -> None:
    status = approval.get("status")
    if status != "APPROVE_PLAN":
        raise RuntimeError(
            f"Plan is not approved. Current status: {status}. "
            "Coder Agent will not proceed."
        )


def build_coder_messages(
    plan: Dict[str, Any],
    approval: Dict[str, Any],
    prior_review: Optional[Dict[str, Any]] = None
) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a senior Python data engineer.\n"
        "Generate a complete Python script that executes the approved cleaning plan.\n\n"
        "Output rules:\n"
        "1. Output ONLY valid Python code.\n"
        "2. Do not include markdown fences.\n"
        "3. Do not include explanation text before or after the code.\n"
        "4. Use pandas.\n"
        "5. Use only deterministic rule-based logic.\n"
        "6. Do not use any LLM calls in the generated script.\n\n"
        "Hard requirements:\n"
        "7. Use the exact file names and exact column names from the plan.\n"
        "8. Process each file using only that file's own actions.\n"
        "9. If a file has no actions, copy it unchanged to data_lake_clean with '_cleaned' inserted before .csv.\n"
        "10. Save cleaned files as name_cleaned.csv, not name.csv_cleaned.csv.\n"
        "11. Do not infer date columns from names like 'Date'. Use the plan actions only.\n"
        "12. Do not invent keys like customer_id unless explicitly present in the plan.\n"
        "13. If a cross-file action is ambiguous, log it as skipped with a clear reason.\n"
        "14. execution_log.json must be one valid JSON object.\n"
        "15. Preserve null values and avoid converting nulls to strings.\n"
        "16. Use helper functions for date standardization, text normalization, boolean normalization, and file copying.\n"
        "17. For date standardization, use errors='coerce' and format valid dates as YYYY-MM-DD.\n"
        "18. For boolean normalization, support yes/no, true/false, 1/0, y/n, t/f.\n"
        "19. If a required column is missing, do not crash; log the skipped action.\n"
        "20. Cross-file actions must never overwrite original files.\n"
        "21. Read the plan file path from sys.argv[1], not from a hardcoded string. Use: import sys; plan_path = sys.argv[1] at the top of main().\n"
        "22. You MUST define a function called exactly 'main' with no arguments: def main(): — not execute_plan, not run, not process. Call it at the bottom with if __name__ == '__main__': main()\n"
        "23. Always reference each file by its literal filename string (e.g. customers_legacy.csv), not dynamically through the plan dict. Do not wrap output in markdown code fences."
        "24. import sys at the TOP of the file with all other imports, never inside if __name__ == '__main__'.\n"
        "25. Use plan_path = sys.argv[1] exactly once, as the first line inside main(). Never repeat it.\n"
    )

    user_prompt = (
        "Generate Python code for the approved cleaning plan below.\n"
        "Respect the human feedback and reviewer concerns where relevant.\n\n"
        "APPROVAL DECISION:\n"
        f"{json.dumps(approval, indent=2)}\n\n"
        "CLEANING PLAN:\n"
        f"{json.dumps(plan, indent=2)}\n\n"
    )

    if prior_review:
        user_prompt += (
            "PRIOR CODE REVIEW FEEDBACK:\n"
            f"{json.dumps(prior_review, indent=2)}\n\n"
            "Revise the code to fix all issues listed in the prior code review.\n"
        )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def basic_code_validation(code_text: str) -> None:
    if not code_text.strip():
        raise ValueError("Generated code is empty.")

    required_snippets = [
        "import pandas as pd",
        "__name__ == '__main__'",
    ]

    for snippet in required_snippets:
        if snippet not in code_text:
            raise ValueError(f"Generated code is missing required snippet: {snippet}")

    # Accept either main() or execute_plan() as the entry point
    if "def main(" not in code_text and "def execute_plan(" not in code_text:
        raise ValueError("Generated code is missing an entry point function (main or execute_plan).")

    try:
        ast.parse(code_text)
    except SyntaxError as e:
        raise ValueError(f"Generated code has invalid Python syntax: {e}")
    if "sys.argv[1]" not in code_text:
        raise ValueError("Generated code does not use sys.argv[1] for the plan path.")

def semantic_code_validation(code_text: str, plan: dict):
    for file_entry in plan.get('files', []):
        name = file_entry['name']
        # Accept either a literal filename OR dynamic plan-driven loading
        loads_from_plan = (
            "plan['files']" in code_text or 
            'plan["files"]' in code_text or
            "file['name']" in code_text or
            'file["name"]' in code_text
        )
        if name not in code_text and not loads_from_plan:
            raise ValueError(f"Generated code does not reference planned file: {name}")



def save_raw_output(raw_text: str) -> None:
    ensure_output_dir()
    with open(RAW_LLM_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(raw_text)


def save_generated_code(code_text: str) -> None:
    ensure_output_dir()
    with open(GENERATED_CODE_PATH, "w", encoding="utf-8") as f:
        f.write(code_text)


def save_metadata(plan: Dict[str, Any], approval: Dict[str, Any], prior_review: Optional[Dict[str, Any]]) -> None:
    metadata = {
        "source_plan_file": os.path.basename(PLAN_INPUT_PATH),
        "source_approval_file": os.path.basename(APPROVAL_INPUT_PATH),
        "approval_status": approval.get("status"),
        "generated_code_file": os.path.basename(GENERATED_CODE_PATH),
        "raw_llm_output_file": os.path.basename(RAW_LLM_OUTPUT_PATH),
        "file_count_in_plan": len(plan.get("files", [])),
        "cross_file_action_count": len(plan.get("cross_file_plan", [])),
        "used_prior_code_review": prior_review is not None,
    }

    with open(CODER_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

def fix_plan_path(code_text: str) -> str:
    """Ensure sys is imported at the top level, not inside __main__ block."""
    lines = code_text.splitlines()
    new_lines = []
    has_top_level_sys = False

    for line in lines:
        # Remove sys import if it's buried inside if __name__ block
        if line.strip() == "import sys":
            if has_top_level_sys:
                continue  # skip duplicates
            # Check if it's indented (i.e. inside a block)
            if line.startswith(" ") or line.startswith("\t"):
                continue  # drop it; we'll add it at the top
            else:
                has_top_level_sys = True
        new_lines.append(line)

    # Inject at top if missing
    if not has_top_level_sys:
        # Insert after the last top-level import
        insert_at = 0
        for i, line in enumerate(new_lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i + 1
        new_lines.insert(insert_at, "import sys")

    # Remove duplicate plan_path = sys.argv[1] (keep only first occurrence)
    seen_argv = False
    deduped = []
    for line in new_lines:
        if "sys.argv[1]" in line:
            if seen_argv:
                continue  # drop duplicate
            seen_argv = True
        deduped.append(line)

    return "\n".join(deduped)

def generate_code(plan, approval, prior_review=None):
    messages = build_coder_messages(plan, approval, prior_review=prior_review)
    raw_output = call_llm(messages, temperature=0.0, timeout=(10, 600))
    save_raw_output(raw_output)

    code_text = strip_code_fences(raw_output)
    code_text = fix_plan_path(code_text)   # <-- add this
    basic_code_validation(code_text)
    semantic_code_validation(code_text, plan)
    return code_text


if __name__ == "__main__":
    ensure_output_dir()

    plan = load_json(PLAN_INPUT_PATH)
    approval = load_json(APPROVAL_INPUT_PATH)
    prior_review = load_optional_json(CODE_REVIEW_INPUT_PATH)

    validate_approval(approval)

    code_text = generate_code(plan, approval, prior_review=prior_review)
    save_generated_code(code_text)
    save_metadata(plan, approval, prior_review)

    print(f"Generated code saved to {GENERATED_CODE_PATH}")
    print(f"Raw LLM output saved to {RAW_LLM_OUTPUT_PATH}")
    print(f"Metadata saved to {CODER_METADATA_PATH}")