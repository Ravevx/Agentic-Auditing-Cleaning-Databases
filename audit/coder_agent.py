import os
import json
import ast
from typing import Dict, Any, List, Optional

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
    if not text:
        return ""

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        while lines and not lines[-1].strip():
            lines.pop()

        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


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
        "21. The generated code must read the plan from an absolute or script-relative path, not assume the working directory contains cleaning_plan_llm.json.\n"
        "22. Include a main() function and if __name__ == '__main__': main().\n"
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
        "def main(",
        "__name__ == '__main__'",
    ]

    for snippet in required_snippets:
        if snippet not in code_text:
            raise ValueError(f"Generated code is missing required snippet: {snippet}")

    try:
        ast.parse(code_text)
    except SyntaxError as e:
        raise ValueError(f"Generated code has invalid Python syntax: {e}")


def semantic_code_validation(code_text: str, plan: Dict[str, Any]) -> None:
    suspicious_patterns = [
        "data[\"choices\"][\"message\"]",
        "data['choices']['message']",
        ".endswith('Date')",
        ".endswith(\"Date\")",
        "name.csv_cleaned.csv",
        "cleaning_plan['files']['actions']",
    ]

    for pattern in suspicious_patterns:
        if pattern in code_text:
            raise ValueError(f"Generated code contains suspicious pattern: {pattern}")

    for file_entry in plan.get("files", []):
        if file_entry["name"] not in code_text:
            raise ValueError(f"Generated code does not reference planned file: {file_entry['name']}")

        for action in file_entry.get("actions", []):
            col = action.get("column")
            if col and col not in code_text:
                raise ValueError(f"Generated code does not reference planned column: {col}")


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


def generate_code(plan: Dict[str, Any], approval: Dict[str, Any], prior_review: Optional[Dict[str, Any]]) -> str:
    messages = build_coder_messages(plan, approval, prior_review=prior_review)
    raw_output = call_llm(messages, temperature=0.0, timeout=(10, 600))
    save_raw_output(raw_output)

    code_text = strip_code_fences(raw_output)
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