import os
import json
import argparse
from typing import Dict, Any, List

import requests


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

PLAN_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "planner_report", "cleaning_plan_llm.json"
)

REVIEW_OUTPUT_DIR = os.path.join(
    PROJECT_ROOT, "outputs", "plan_reviews"
)

LMSTUDIO_API_BASE = "http://127.0.0.1:1234/v1"
LMSTUDIO_MODEL = "meta-llama-3.1-8b-instruct"


def ensure_output_dir() -> None:
    os.makedirs(REVIEW_OUTPUT_DIR, exist_ok=True)


def load_cleaning_plan() -> Dict[str, Any]:
    if not os.path.exists(PLAN_INPUT_PATH):
        raise FileNotFoundError(
            f"Cleaning plan not found at {PLAN_INPUT_PATH}. "
            f"Run planner_agent.py first."
        )

    with open(PLAN_INPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def strip_code_fences(text: str) -> str:
    if not text:
        return ""

    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()

        if lines and lines.startswith("```"):
            lines = lines[1:]

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


def call_llm(messages: List[Dict[str, str]], temperature: float = 0.0, timeout=(10, 300)) -> str:
    payload = {
        "model": LMSTUDIO_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 1200,
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


def build_reviewer_prompts(review_plan: Dict[str, Any], reviewer_id: str) -> List[Dict[str, str]]:
    reviewer_styles = {
        "reviewer_1": (
            "You are Reviewer 1, a conservative data quality auditor. "
            "Focus on risk, unsupported assumptions, weak evidence, and possible downstream failures."
        ),
        "reviewer_2": (
            "You are Reviewer 2, a practical senior data engineer. "
            "Focus on implementation feasibility, action usefulness, and whether the plan is efficient and realistic."
        ),
    }

    if reviewer_id not in reviewer_styles:
        raise ValueError(
            f"Unknown reviewer_id '{reviewer_id}'. Use 'reviewer_1' or 'reviewer_2'."
        )

    system_prompt = (
        f"{reviewer_styles[reviewer_id]}\n\n"
        "You will review a data cleaning plan.\n"
        "Return exactly one valid JSON object and nothing else.\n"
        "Do not use markdown fences.\n"
        "Score the plan on clarity, feasibility, and impact using integers from 1 to 5.\n"
        "Identify missing risks and alternative strategies.\n"
        "Be concise and evidence-based.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "reviewer_id": "string",\n'
        '  "plan_file": "string",\n'
        '  "scores": {\n'
        '    "clarity": 1,\n'
        '    "feasibility": 1,\n'
        '    "impact": 1\n'
        "  },\n"
        '  "overall_recommendation": "approve" | "approve_with_minor_concerns" | "request_revision",\n'
        '  "missing_risks": ["string"],\n'
        '  "alternative_strategies": ["string"],\n'
        '  "comments": ["string"]\n'
        "}\n"
        "Return only the JSON object."
    )

    user_prompt = (
        "Review the following cleaning plan and score it.\n"
        "Base your comments only on what is present in the plan.\n\n"
        f"{json.dumps(review_plan, indent=2)}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def validate_review(review: Dict[str, Any], reviewer_id: str) -> Dict[str, Any]:
    required_top_keys = {
        "reviewer_id",
        "plan_file",
        "scores",
        "overall_recommendation",
        "missing_risks",
        "alternative_strategies",
        "comments",
    }

    missing = required_top_keys - set(review.keys())
    if missing:
        raise ValueError(f"Review JSON missing keys: {sorted(missing)}")

    if review["reviewer_id"] != reviewer_id:
        raise ValueError(
            f"reviewer_id mismatch: expected '{reviewer_id}', got '{review['reviewer_id']}'"
        )

    scores = review["scores"]
    if not isinstance(scores, dict):
        raise ValueError("scores must be an object")

    for key in ["clarity", "feasibility", "impact"]:
        if key not in scores:
            raise ValueError(f"scores missing '{key}'")
        if not isinstance(scores[key], int) or not (1 <= scores[key] <= 5):
            raise ValueError(f"scores['{key}'] must be an integer from 1 to 5")

    valid_recommendations = {
        "approve",
        "approve_with_minor_concerns",
        "request_revision",
    }
    if review["overall_recommendation"] not in valid_recommendations:
        raise ValueError(
            f"Invalid overall_recommendation: {review['overall_recommendation']}"
        )

    for key in ["missing_risks", "alternative_strategies", "comments"]:
        if not isinstance(review[key], list):
            raise ValueError(f"{key} must be a list")
        if not all(isinstance(x, str) for x in review[key]):
            raise ValueError(f"All items in {key} must be strings")

    return review


def generate_review(plan: Dict[str, Any], reviewer_id: str) -> Dict[str, Any]:
    messages = build_reviewer_prompts(plan, reviewer_id)
    content = call_llm(messages, temperature=0.1, timeout=(10, 300))

    raw_path = os.path.join(REVIEW_OUTPUT_DIR, f"{reviewer_id}_raw.txt")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(content)

    parsed = json.loads(extract_json_block(content))
    parsed["reviewer_id"] = reviewer_id
    parsed["plan_file"] = os.path.basename(PLAN_INPUT_PATH)
    validated = validate_review(parsed, reviewer_id)
    return validated


def save_review(review: Dict[str, Any], reviewer_id: str) -> str:
    ensure_output_dir()
    output_path = os.path.join(REVIEW_OUTPUT_DIR, f"{reviewer_id}.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(review, f, indent=2)

    return output_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate an independent review for a cleaning plan.")
    parser.add_argument(
        "--reviewer",
        required=True,
        choices=["reviewer_1", "reviewer_2"],
        help="Reviewer identity to use."
    )
    args = parser.parse_args()

    ensure_output_dir()
    plan = load_cleaning_plan()
    review = generate_review(plan, args.reviewer)
    saved_path = save_review(review, args.reviewer)

    print(f"Review saved to {saved_path}")