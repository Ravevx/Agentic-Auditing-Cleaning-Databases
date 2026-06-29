import os
import csv
from typing import List, Dict, Any
from collections import Counter

DATA_LAKE_PATH = os.path.join(os.path.dirname(__file__), "..", "data_lake")

def list_data_files() -> List[str]:
    """
    List all files in the data_lake folder.
    Returns file paths relative to the project root.
    """
    files = []
    for fname in os.listdir(DATA_LAKE_PATH):
        if fname.lower().endswith(".csv"):
            files.append(os.path.join(DATA_LAKE_PATH, fname))
    return files

def read_csv_head(path: str, max_rows: int = 50) -> List[Dict[str, Any]]:
    """
    Read up to max_rows from a CSV file and return as list of dict rows.
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            rows.append(row)
            if i + 1 >= max_rows:
                break
    return rows

def profile_column(values: List[str]) -> Dict[str, Any]:
    """
    Basic profiling of a single column: type guess, null fraction, distinct count.
    """
    total = len(values)
    null_count = sum(1 for v in values if v is None or v == "")

    non_null_values = [v for v in values if v is not None and v != ""]
    distinct_count = len(set(non_null_values))

    # crude type guess
    type_guess = "string"
    if all(is_int(v) for v in non_null_values):
        type_guess = "integer"
    elif all(is_float(v) for v in non_null_values):
        type_guess = "float"

    return {
        "type_guess": type_guess,
        "null_fraction": null_count / total if total > 0 else 0.0,
        "distinct_count": distinct_count
    }

def is_int(v: str) -> bool:
    try:
        int(v)
        return True
    except Exception:
        return False

def is_float(v: str) -> bool:
    try:
        float(v)
        return True
    except Exception:
        return False

def detect_inconsistent_date_formats(values: List[str]) -> bool:
    """
    Very simple heuristic: if the column has multiple non-empty patterns.
    """
    patterns = Counter()
    for v in values:
        if not v:
            continue
        if "-" in v and len(v) >= 8:
            patterns["dash"] += 1
        elif "/" in v:
            patterns["slash"] += 1
        elif any(m in v.lower() for m in ["jan", "feb", "mar", "apr", "may", "jun",
                                          "jul", "aug", "sep", "oct", "nov", "dec"]):
            patterns["month_name"] += 1
        else:
            patterns["other"] += 1
    return len([p for p, c in patterns.items() if c > 0]) > 1

def detect_category_variants(values: List[str], threshold: int = 10) -> bool:
    """
    Simple heuristic: if many distinct values exist and some look similar,
    we flag potential inconsistent categories.
    """
    non_null = [v.strip() for v in values if v and v.strip()]
    if len(non_null) < threshold:
        return False
    # crude: many distinct values and mixed casing suggests messy categories
    distinct = set(non_null)
    if len(distinct) > len(non_null) * 0.3:
        return True
    return False