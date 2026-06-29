import os
import json
import argparse
from typing import Dict, Any, List


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))

PLAN_INPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "planner_report", "cleaning_plan_llm.json"
)

REVIEWS_DIR = os.path.join(
    PROJECT_ROOT, "outputs", "plan_reviews"
)

APPROVAL_OUTPUT_PATH = os.path.join(
    PROJECT_ROOT, "outputs", "approval_gate", "approval_decision.json"
)


def ensure_output_dir() -> None:
    os.makedirs(os.path.dirname(APPROVAL_OUTPUT_PATH), exist_ok=True)


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Required file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs() -> Dict[str, Any]:
    plan = load_json(PLAN_INPUT_PATH)
    review_1 = load_json(os.path.join(REVIEWS_DIR, "reviewer_1.json"))
    review_2 = load_json(os.path.join(REVIEWS_DIR, "reviewer_2.json"))

    return {
        "plan": plan,
        "review_1": review_1,
        "review_2": review_2,
    }


def summarize_reviews(review_1: Dict[str, Any], review_2: Dict[str, Any]) -> Dict[str, Any]:
    combined_risks = []
    combined_alternatives = []
    combined_comments = []

    for item in review_1.get("missing_risks", []):
        if item not in combined_risks:
            combined_risks.append(item)

    for item in review_2.get("missing_risks", []):
        if item not in combined_risks:
            combined_risks.append(item)

    for item in review_1.get("alternative_strategies", []):
        if item not in combined_alternatives:
            combined_alternatives.append(item)

    for item in review_2.get("alternative_strategies", []):
        if item not in combined_alternatives:
            combined_alternatives.append(item)

    for item in review_1.get("comments", []):
        if item not in combined_comments:
            combined_comments.append(item)

    for item in review_2.get("comments", []):
        if item not in combined_comments:
            combined_comments.append(item)

    return {
        "combined_missing_risks": combined_risks,
        "combined_alternative_strategies": combined_alternatives,
        "combined_comments": combined_comments,
    }


def build_approval_decision(
    plan: Dict[str, Any],
    review_1: Dict[str, Any],
    review_2: Dict[str, Any],
    status: str,
    feedback: str
) -> Dict[str, Any]:
    if status not in {"APPROVE_PLAN", "REQUEST_REVISION"}:
        raise ValueError("status must be APPROVE_PLAN or REQUEST_REVISION")

    summary = summarize_reviews(review_1, review_2)

    return {
        "plan_file": os.path.basename(PLAN_INPUT_PATH),
        "review_files": [
            "reviewer_1.json",
            "reviewer_2.json"
        ],
        "status": status,
        "feedback": feedback,
        "summary_of_reviews": summary,
        "reviewer_scores": {
            "reviewer_1": review_1.get("scores", {}),
            "reviewer_2": review_2.get("scores", {})
        }
    }


def save_decision(decision: Dict[str, Any]) -> None:
    ensure_output_dir()
    with open(APPROVAL_OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(decision, f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Human-in-the-loop approval gate for cleaning plans.")
    parser.add_argument(
        "--status",
        required=True,
        choices=["APPROVE_PLAN", "REQUEST_REVISION"],
        help="Final human decision."
    )
    parser.add_argument(
        "--feedback",
        default="",
        help="Optional human feedback to include in the decision file."
    )

    args = parser.parse_args()

    inputs = load_inputs()

    decision = build_approval_decision(
        plan=inputs["plan"],
        review_1=inputs["review_1"],
        review_2=inputs["review_2"],
        status=args.status,
        feedback=args.feedback
    )

    save_decision(decision)
    print(f"Approval decision saved to {APPROVAL_OUTPUT_PATH}")