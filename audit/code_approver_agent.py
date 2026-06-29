import os
import json
import ast
from typing import Dict, Any, List

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

PLAN_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "planner_report", "cleaning_plan_llm.json"
)

GENERATED_CODE_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "generated_code", "generated_code.py"
)

CODE_REVIEW_DIR = os.path.join(
    PROJECT_ROOT, "outputs", "code_review"
)

CODE_REVIEW_PATH = os.path.join(
    CODE_REVIEW_DIR, "code_review.json"
)

RAW_REVIEW_OUTPUT_PATH = os.path.join(
    CODE_REVIEW_DIR, "code_review_raw.txt"
)

LMSTUDIO_API_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "meta-llama-3.1-8b-instruct"


def ensure_output_dir() -> None:
    os.makedirs(CODE_REVIEW_DIR, exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: str) -> str:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


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


def extract_json_block(text: str) -> str:
    text = strip_code_fences(text).strip()

    if text.startswith("{") or text.startswith("["):
        return text

    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [i for i in (start_obj, start_arr) if i != -1]

    if not starts:
        raise ValueError("No JSON object or array found in model output.")

    start = min(starts)
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)

    if end == -1 or end <= start:
        raise ValueError("JSON start found but no valid JSON end found.")

    return text[start:end + 1]


def call_llm(messages: List[Dict[str, str]], temperature: float = 0.0, timeout=(10, 600)) -> str:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1800,
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


def local_static_checks(code_text: str, plan: Dict[str, Any]) -> List[str]:
    issues = []

    try:
        ast.parse(code_text)
    except SyntaxError as e:
        issues.append(f"Python syntax error: {e}")

    suspicious_patterns = [
        ("data[\"choices\"][\"message\"]", "Incorrect choices/message access pattern."),
        ("data['choices']['message']", "Incorrect choices/message access pattern."),
        (".endswith('Date')", "Unsafe inference of date columns from names."),
        (".endswith(\"Date\")", "Unsafe inference of date columns from names."),
        ("cleaning_plan['files'][0]['actions']", "Uses first file's actions for all files."),
        ("customer_id", "Hardcoded cross-file key may not exist in the approved plan."),
        ("name.csv_cleaned.csv", "Incorrect cleaned filename pattern."),
    ]

    for pattern, reason in suspicious_patterns:
        if pattern in code_text:
            issues.append(reason)

    for file_entry in plan.get("files", []):
        if file_entry["name"] not in code_text:
            issues.append(f"Generated code does not clearly reference planned file: {file_entry['name']}")

        for action in file_entry.get("actions", []):
            col = action.get("column")
            if col and col not in code_text:
                issues.append(f"Generated code does not clearly reference planned column: {col}")

    return issues


def build_review_messages(plan: Dict[str, Any], code_text: str, local_issues: List[str]) -> List[Dict[str, str]]:
    system_prompt = (
        "You are a strict senior Python code reviewer.\n"
        "Review generated data-cleaning code against an approved cleaning plan.\n"
        "Return exactly one valid JSON object and nothing else.\n"
        "Do not use markdown fences.\n\n"
        "Required JSON format:\n"
        "{\n"
        '  "status": "approved" | "changes_requested",\n'
        '  "comments": ["string"],\n'
        '  "blocking_issues": ["string"],\n'
        '  "suggested_fixes": ["string"]\n'
        "}\n\n"
        "Mark status as changes_requested if there are syntax issues, wrong file/column assumptions, unsafe cross-file logic, or output-path mistakes.\n"
    )

    user_prompt = (
        "Review the generated Python code against the approved cleaning plan.\n\n"
        "APPROVED CLEANING PLAN:\n"
        f"{json.dumps(plan, indent=2)}\n\n"
        "LOCAL STATIC CHECK ISSUES:\n"
        f"{json.dumps(local_issues, indent=2)}\n\n"
        "GENERATED CODE:\n"
        f"{code_text}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def validate_review_json(review: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = {"status", "comments", "blocking_issues", "suggested_fixes"}
    missing = required_keys - set(review.keys())
    if missing:
        raise ValueError(f"Missing keys in code review output: {sorted(missing)}")

    if review["status"] not in {"approved", "changes_requested"}:
        raise ValueError("status must be 'approved' or 'changes_requested'")

    for key in ["comments", "blocking_issues", "suggested_fixes"]:
        if not isinstance(review[key], list):
            raise ValueError(f"{key} must be a list")
        if not all(isinstance(x, str) for x in review[key]):
            raise ValueError(f"All items in {key} must be strings")

    return review


def save_raw_review_output(raw_text: str) -> None:
    ensure_output_dir()
    with open(RAW_REVIEW_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(raw_text)


def save_review(review: Dict[str, Any]) -> None:
    ensure_output_dir()
    with open(CODE_REVIEW_PATH, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=2)


if __name__ == "__main__":
    ensure_output_dir()

    plan = load_json(PLAN_INPUT_PATH)
    code_text = load_text(GENERATED_CODE_PATH)

    local_issues = local_static_checks(code_text, plan)

    messages = build_review_messages(plan, code_text, local_issues)
    raw_review = call_llm(messages, temperature=0.0, timeout=(10, 600))
    save_raw_review_output(raw_review)

    parsed_review = json.loads(extract_json_block(raw_review))

    if local_issues:
        parsed_review["status"] = "changes_requested"
        merged_blockers = parsed_review.get("blocking_issues", [])
        for issue in local_issues:
            if issue not in merged_blockers:
                merged_blockers.append(issue)
        parsed_review["blocking_issues"] = merged_blockers

        if "comments" not in parsed_review or not isinstance(parsed_review["comments"], list):
            parsed_review["comments"] = []
        parsed_review["comments"].append("Local static checks found blocking issues.")

    validated_review = validate_review_json(parsed_review)
    save_review(validated_review)

    print(f"Code review saved to {CODE_REVIEW_PATH}")
    print(f"Raw review output saved to {RAW_REVIEW_OUTPUT_PATH}")