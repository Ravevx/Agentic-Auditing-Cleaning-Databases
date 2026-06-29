import os
import json
from typing import Dict, Any, List

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
FILE_REPORTS_DIR = os.path.join(PROJECT_ROOT, "outputs","data_explorer_reports")
PLAN_OUTPUT_PATH = os.path.join(PROJECT_ROOT, "outputs","planner_report","cleaning_plan_llm.json")
DATA_LAKE_PATH = os.path.join(PROJECT_ROOT, "data_lake")

LMSTUDIO_API_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "meta-llama-3.1-8b-instruct"


def list_file_reports() -> List[str]:
    files = []
    if not os.path.exists(FILE_REPORTS_DIR):
        return files

    for fname in os.listdir(FILE_REPORTS_DIR):
        if fname.lower().endswith(".json"):
            files.append(os.path.join(FILE_REPORTS_DIR, fname))

    files.sort()
    return files

def ensure_output_dir() -> None:
    output_dir = os.path.dirname(PLAN_OUTPUT_PATH)
    os.makedirs(output_dir, exist_ok=True)

def load_file_reports() -> List[Dict[str, Any]]:
    reports = []
    report_files = list_file_reports()

    if not report_files:
        raise FileNotFoundError(
            f"No file reports found in {FILE_REPORTS_DIR}. "
            f"Run data_explorer_agent.py first."
        )

    for path in report_files:
        with open(path, "r", encoding="utf-8") as f:
            reports.append(json.load(f))

    return reports


def build_planner_input(file_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "data_lake_path": os.path.abspath(DATA_LAKE_PATH),
        "file_reports": file_reports
    }


def strip_code_fences(text: str) -> str:
    """
    Removes markdown code fences if the model wraps JSON in ```json ... ```
    """
    if not text:
        return ""

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        # Remove opening fence
        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        # Remove closing fence
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


def call_llm(messages: List[Dict[str, str]], temperature: float = 0.0, timeout: int = 300) -> str:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1024,
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

    return strip_code_fences(content)


def fix_json_with_llm(bad_json: str) -> str:
    system_prompt = (
        "You are a JSON repair assistant. "
        "You will receive broken or incomplete JSON. "
        "Return the same content as valid JSON.\n"
        "Rules:\n"
        "- Do not add explanations.\n"
        "- Do not use markdown fences.\n"
        "- Preserve the same keys and values when possible.\n"
        "- Return only valid JSON."
    )

    user_prompt = f"Fix this JSON:\n\n{bad_json}"

    return call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        timeout=600,
    )

def extract_json_block(text: str) -> str:
    text = strip_code_fences(text).strip()

    if text.startswith("{") or text.startswith("["):
        return text

    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [i for i in (start_obj, start_arr) if i != -1]
    if not starts:
        raise ValueError("No JSON start found")

    start = min(starts)
    end_obj = text.rfind("}")
    end_arr = text.rfind("]")
    end = max(end_obj, end_arr)
    if end == -1 or end <= start:
        raise ValueError("Found JSON start but not a valid end")

    return text[start:end+1]

def call_llm_for_plan(planner_input: Dict[str, Any]) -> Dict[str, Any]:
    planner_json_str = json.dumps(planner_input, indent=2)

    system_prompt = (
        "You are a senior data engineer.\n"
        "You receive per-file audit reports for several CSV files in a data lake.\n"
        "Your job is to design a cleaning and organization plan.\n\n"
        "Requirements:\n"
        "1. Output MUST be valid JSON.\n"
        "2. Top-level keys: data_lake_path, files, cross_file_plan.\n"
        "3. files: list of objects with 'name' and 'actions'.\n"
        "4. Each action must have: type, column, reason, benefit.\n"
        "6. cross_file_plan: list of objects with keys: type, files_involved, reason, benefit.\n"
        "7. Use only evidence from the file reports. Do not invent issues that are not supported.\n"
        "8. Do not create placeholder actions like 'check missing values' unless the report clearly supports that need.\n"
        "9. Do not propose a date-format issue unless the reports show actual format differences.\n"
        "10. Do not propose schema changes, new columns, feature engineering, or business logic additions.\n"
        "11. Only propose cleaning, normalization, validation, standardization, deduplication, or schema-alignment actions.\n"
        "12. If a column name is known, use it instead of null.\n"
        "13. Cross-file plans should group only files with directly related schemas or entities.\n"
        "14. Keep benefits short and outcome-focused.\n"
        "15. Do NOT include explanations outside JSON.\n"
    )

    user_prompt = (
        "Here are the per-file audit reports in JSON format.\n"
        "Analyze them and produce a cleaning and organization plan.\n"
        "Base every action on the evidence in the reports.\n"
        "Return only cleaning and standardization actions, not schema changes.\n\n"
        f"{planner_json_str}"
    )

    content = call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        timeout=(10, 600),  # connect=10, read=180
    )

    # Save raw output for debugging
    ensure_output_dir()
    raw_path = os.path.join(os.path.dirname(PLAN_OUTPUT_PATH), "planner_raw_output.txt")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Try to extract JSON only once; no repair call
    try:
        json_text = extract_json_block(content)
        return json.loads(json_text)
    except Exception as e:
        raise RuntimeError(
            f"Planner returned non-JSON output: {e}\n"
            f"Raw output saved to {raw_path}"
        )

    return plan


def save_cleaning_plan(plan: Dict[str, Any], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2)


if __name__ == "__main__":
    file_reports = load_file_reports()
    planner_input = build_planner_input(file_reports)
    plan = call_llm_for_plan(planner_input)
    ensure_output_dir()
    save_cleaning_plan(plan, PLAN_OUTPUT_PATH)
    print(f"LLM-generated cleaning plan saved to {PLAN_OUTPUT_PATH}")