import os
import csv
import json
from typing import Dict, Any, List

import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
DATA_LAKE_PATH = os.path.join(PROJECT_ROOT, "data_lake")
FILE_REPORTS_DIR = os.path.join(PROJECT_ROOT, "outputs","data_explorer_reports")

LMSTUDIO_API_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "meta-llama-3.1-8b-instruct"


def ensure_dirs() -> None:
    os.makedirs(FILE_REPORTS_DIR, exist_ok=True)


def list_csv_files() -> List[str]:
    files = []
    for fname in os.listdir(DATA_LAKE_PATH):
        if fname.lower().endswith(".csv"):
            files.append(os.path.join(DATA_LAKE_PATH, fname))
    files.sort()
    return files


def sample_csv(path: str, max_rows: int = 20) -> List[Dict[str, Any]]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            rows.append(row)
            if i + 1 >= max_rows:
                break
    return rows


def sample_column_values(rows: List[Dict[str, Any]], column: str, max_samples: int = 6) -> List[str]:
    values = []
    for row in rows:
        v = row.get(column, "")
        if v not in ("", None):
            values.append(str(v))
        if len(values) >= max_samples:
            break
    return values


def build_single_file_input(file_path: str) -> Dict[str, Any]:
    rows = sample_csv(file_path, max_rows=20)
    fname = os.path.basename(file_path)
    abs_file_path = os.path.abspath(file_path)
    rel_file_path = os.path.relpath(file_path, DATA_LAKE_PATH)

    if not rows:
        return {
            "data_lake_path": os.path.abspath(DATA_LAKE_PATH),
            "file": {
                "name": fname,
                "relative_path": rel_file_path,
                "absolute_path": abs_file_path,
                "columns": [],
                "sample_rows_count": 0
            }
        }

    columns = list(rows[0].keys())
    columns_summary = []

    for col in columns:
        columns_summary.append({
            "name": col,
            "example_values": sample_column_values(rows, col, max_samples=6)
        })

    return {
        "data_lake_path": os.path.abspath(DATA_LAKE_PATH),
        "file": {
            "name": fname,
            "relative_path": rel_file_path,
            "absolute_path": abs_file_path,
            "columns": columns_summary,
            "sample_rows_count": len(rows)
        }
    }


def strip_code_fences(text: str) -> str:
    if not text:
        return ""

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines[0].startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]

        text = "\n".join(lines).strip()

    return text


def call_llm(
    messages: List[Dict[str, str]],
    temperature: float = 0.0,
    timeout: int = 120,
) -> str:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
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
        timeout=60,
    )


def analyze_single_file(file_input: Dict[str, Any]) -> Dict[str, Any]:
    actual_file_name = file_input["file"]["name"]
    actual_absolute_path = file_input["file"]["absolute_path"]

    system_prompt = (
    "You are a production-grade data quality analyst.\n\n"
    "You are given a summary of ONE CSV file containing:\n"
    "- file name\n"
    "- column names\n"
    "- example values per column\n"
    "- sample row count\n\n"
    "Your job is to infer what kind of dataset this is and produce a factual, evidence-based audit report.\n\n"
    "Important rules:\n"
    "1. Only report issues that are directly supported by the provided sample values.\n"
    "2. Do NOT confuse different values with different formats. Different years do not mean inconsistent date formats.\n"
    "3. Only call something a date-format issue if the string patterns differ, such as YYYY-MM-DD vs MM/DD/YYYY.\n"
    "4. Do NOT include placeholder findings like 'None found, but recommended to check anyway'.\n"
    "5. If the sample does not support a claim, do not include that claim.\n"
    "6. Use cautious wording such as 'observed', 'visible in sample', or 'likely' when appropriate.\n"
    "7. Infer the likely business meaning of the file, such as customer dataset, orders dataset, or product catalog.\n"
    "8. Describe the dataset in plain language, including what the file appears to contain and what the columns appear to represent.\n"
    "9. Recommendations must be specific and tied to observed issues.\n"
    "10. Cross-file grouping is NOT your job here. Focus only on this file.\n"
    "11. The 'name' field MUST exactly match the provided file name.\n\n"
    "Return ONLY valid JSON with this exact structure:\n"
    "{\n"
    "  \"name\": string,\n"
    "  \"absolute_path\": string,\n"
    "  \"dataset_type\": string,\n"
    "  \"dataset_description\": string,\n"
    "  \"column_overview\": [\n"
    "    {\n"
    "      \"name\": string,\n"
    "      \"inferred_role\": string,\n"
    "      \"notes\": string\n"
    "    }\n"
    "  ],\n"
    "  \"summary\": string,\n"
    "  \"key_problems\": [string],\n"
    "  \"recommendations\": [string],\n"
    "  \"confidence_notes\": [string]\n"
    "}\n"
    "Do not include markdown fences."
)

    user_prompt = (
    f"Analyze this single file summary.\n"
    f"The output 'name' field must be exactly: {actual_file_name}\n\n"
    "Use only the evidence visible in the sample values and column names.\n"
    "If a finding is uncertain, either omit it or mention it as a confidence note.\n\n"
    f"{json.dumps(file_input, indent=2)}"
)

    content = call_llm(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        timeout=120,
    )

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        fixed = fix_json_with_llm(content)
        try:
            parsed = json.loads(fixed)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"Single-file audit JSON parse failed even after repair: {e}\n\n"
                f"Original content:\n{content}\n\n"
                f"Fixed content:\n{fixed}"
            )

    parsed["name"] = actual_file_name
    parsed["absolute_path"] = file_input["file"]["absolute_path"]
    return {
        "file_metadata": {
            "name": actual_file_name,
            "absolute_path": actual_absolute_path,
        },
        "analysis": parsed
    }

def get_report_path_for_csv(file_path: str) -> str:
    csv_name = os.path.basename(file_path)
    report_name = csv_name.replace(".csv", "_report.json")
    return os.path.join(FILE_REPORTS_DIR, report_name)


def load_existing_report(report_path: str) -> Dict[str, Any]:
    with open(report_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_single_file_report(report: Dict[str, Any]) -> str:
    safe_name = report["file_metadata"]["name"].replace(".csv", "_report.json")
    output_path = os.path.join(FILE_REPORTS_DIR, safe_name)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return output_path


def main() -> None:
    ensure_dirs()
    csv_files = list_csv_files()

    if not csv_files:
        print("No CSV files found in data_lake.")
        return

    all_file_reports = []

    for file_path in csv_files:
        report_path = get_report_path_for_csv(file_path)

        if os.path.exists(report_path):
            print(f"Loading existing report: {report_path}")
            report = load_existing_report(report_path)
            all_file_reports.append(report)
            continue

        file_input = build_single_file_input(file_path)
        print(f"Analyzing: {file_input['file']['name']}")
        report = analyze_single_file(file_input)
        save_path = save_single_file_report(report)
        all_file_reports.append(report)
        print(f"Saved file report: {save_path}")

    print("Per-file exploration reports are ready.")
    print(f"Reports folder: {FILE_REPORTS_DIR}")
    print("Skipping final combined summary. Planner Agent will use the per-file reports directly.")


if __name__ == "__main__":
    main()