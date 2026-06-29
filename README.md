# Agentic-Auditing-Cleaning-Databases

> A local multi-agent AI system that audits a raw data lake, designs a cleaning plan, reviews it through a committee (agents + human), generates & validates cleaning code, executes it, and produces a final quality report.

All LLM calls run through **LM Studio** (local). Data is plain files on disk. The pipeline is framework-agnostic and can be implemented in **CrewAI**, **LangGraph**, **LangChain**, or **AutoGen**.

---

## Table of Contents

- [High-Level Goal](#high-level-goal)
- [System Architecture](#system-architecture)
- [Agent Pipeline Flowchart](#agent-pipeline-flowchart)
- [Agent Descriptions](#agent-descriptions)
- [Project Folder Structure](#project-folder-structure)
- [Key Orchestration Properties](#key-orchestration-properties)

---

## High-Level Goal

Build a local multi-agent system that:

1. **Audits** a `data_lake/` folder of raw, messy CSV/JSON datasets.
2. **Designs** a cleaning & reorganization plan.
3. **Reviews** the plan through a committee of agents + a human approval gate.
4. **Generates Python code** to apply the cleaning plan.
5. **Validates** the code via a dedicated Code Approver (loops until approved).
6. **Executes** the approved code and saves cleaned data to `data_lake_clean/`.
7. **Evaluates** the improvement and produces a final human-readable report.

---

## System Architecture

The project is organized into **4 layers**:

| Layer | Description |
|---|---|
| **Data Layer** | `data_lake/` (raw inputs) → `data_lake_clean/` (cleaned outputs) |
| **Agent Layer** | 8 specialized agents with defined roles, inputs, and outputs |
| **Orchestration Layer** | Framework-specific graph/crew/conversation defining ordering, parallelism, loops, and human checkpoints |
| **Interface & Config Layer** | CLI (`python run_pipeline.py --path ./data_lake/`) + `llm_config.yaml` for LM Studio settings |

---

##  Agent Pipeline Flowchart

```
┌─────────────────────────────────────────────────────────┐
│                        START                            │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│             Data Explorer & Auditor Agent               │
│  • Lists files & metadata                               │
│  • Samples rows, infers schema & types                  │
│  • Flags: nulls, duplicates, inconsistent formats       │
│  OUTPUT → audit_report.json                             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   Planner Agent                         │
│  • Proposes cleaning actions per file                   │
│  • Suggests schema alignment across files               │
│  OUTPUT → cleaning_plan.json                            │
└──────────────┬──────────────────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌─────────────┐  ┌─────────────┐
│  Reviewer   │  │  Reviewer   │   (run in PARALLEL)
│   Agent 1   │  │   Agent 2   │
│  Scores &   │  │  Scores &   │
│  comments   │  │  comments   │
│  on plan    │  │  on plan    │
└──────┬──────┘  └──────┬──────┘
       └───────┬─────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│              Human Approval Gate                        │
│  • Reviews plan + both agent reviews                    │
│  • Decides: APPROVE_PLAN or REQUEST_REVISION            │
│  OUTPUT → approval_decision.json                        │
└───────────────┬────────────────────────┬────────────────┘
                │ APPROVED               │ REVISION REQUESTED
                ▼                        └──────────► back to Planner
┌─────────────────────────────────────────────────────────┐
│                   Coder Agent                           │
│  • Generates clean_data.py using pandas                 │
│  • Reads raw files, applies plan, writes to             │
│    data_lake_clean/                                     │
│  OUTPUT → generated_code.py                             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                Code Approver Agent                      │
│  • Checks syntax, logic, and plan alignment             │
│  OUTPUT → code_review.json                              │
└──────────┬──────────────────────────┬───────────────────┘
           │ APPROVED                 │ CHANGES REQUESTED
           │                          └────────────────────┐
           │                                               │
           │                                               ▼
           │                          ┌─────────────────────────────┐
           │                          │    Coder Agent (retry)      │
           │                          │   Revises code per feedback │
           │                          └──────────┬──────────────────┘
           │                                     │
           │                          ┌──────────┘
           │                          │ (loops back to Code Approver)
           ▼
┌─────────────────────────────────────────────────────────┐
│                Code Executor Agent                      │
│  • Safely runs approved generated_code.py               │
│  • Monitors runtime errors                              │
│  OUTPUT → data_lake_clean/ + execution_log.json         │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│               Final Evaluator Agent                     │
│  • Re-audits cleaned files (same metrics as Explorer)   │
│  • Compares before vs. after quality                    │
│  OUTPUT → final_report.md                               │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                         END                             │
└─────────────────────────────────────────────────────────┘
```

---

## Agent Descriptions

### 1. Data Explorer & Auditor Agent

**Role:** First contact with the raw data lake. Discovers and documents everything.

**Inputs:** Path to `data_lake/`

**Responsibilities:**
- List all files and their basic metadata (names, sizes, row counts).
- For each file: sample rows, infer column names and types, compute basic stats (null counts, distinct values, value distributions).
- Identify quality issues: missing data, inconsistent formats, potential duplicates, conflicting schemas.

**Output:** `audit_report.json`
```json
{
  "files": [
    {
      "name": "customers_raw.csv",
      "rows": 12000,
      "columns": ["id", "name", "country", "signup_date"],
      "issues": ["country labels inconsistent", "signup_date format varies"]
    }
  ]
}
```

---

### 2. Planner Agent

**Role:** Turns the audit findings into a concrete, actionable cleaning plan.

**Inputs:** `audit_report.json`

**Responsibilities:**
- For each file: propose cleaning actions (drop columns, type conversions, normalization, deduplication).
- Across the whole lake: suggest schema alignment (which files can be joined or merged).
- Explain what to change, why, and how it benefits downstream analytics or AI workflows.

**Output:** `cleaning_plan.json`
```json
{
  "files": [
    {
      "name": "customers_raw.csv",
      "actions": [
        {
          "type": "standardize_categories",
          "column": "country",
          "reason": "multiple spellings of same country",
          "benefit": "simpler group-by and analytics"
        }
      ]
    }
  ]
}
```

---

### 3. Reviewer Agent 1 & Reviewer Agent 2

**Role:** Independent peer reviewers of the cleaning plan. Run in **parallel**.

**Inputs:** `cleaning_plan.json` + `audit_report.json`

**Responsibilities:**
- Independently score the plan on clarity, feasibility, and impact (1–5 scale).
- Highlight missing risks, edge cases, or alternative strategies.

**Output:** `review_1.json`, `review_2.json` - each containing scores and detailed comments.

---

### 4. Human-in-the-Loop Approval Gate

**Role:** Final human decision point before any code is generated or executed.

**Inputs:** `cleaning_plan.json` + both review files

**Responsibilities:**
- Human reviews the proposed actions and the agents' concerns.
- Decides: `APPROVE_PLAN` or `REQUEST_REVISION` (with written feedback).

**Output:** `approval_decision.json`

>  **The pipeline cannot proceed past this point without explicit human approval.**

---

### 5. Coder Agent

**Role:** Translates the approved cleaning plan into runnable Python code.

**Inputs:** `cleaning_plan.json` + `approval_decision.json` (only proceeds if `APPROVED`)

**Responsibilities:**
- Generate `clean_data.py` using pandas (or similar).
- Code must: read each raw file → apply plan actions → write cleaned files to `data_lake_clean/` with new names.

**Output:** `generated_code.py`

>  The Coder Agent re-runs if the Code Approver requests changes, incorporating the provided feedback.

---

### 6. Code Approver Agent

**Role:** Quality gate for the generated code. Prevents bad code from being executed.

**Inputs:** `generated_code.py`

**Responsibilities:**
- Check for: syntax errors, obvious logic mistakes (e.g., wrong column names vs. the plan), deviation from the approved plan.
- If problems found: produce structured feedback describing each error.
- If clean: mark as approved.

**Output:** `code_review.json`
```json
{
  "status": "changes_requested",
  "comments": ["Column 'signup_dt' used but plan specifies 'signup_date'"]
}
```

>  **Loop:** If `changes_requested`, sends feedback to Coder Agent → Coder revises → Code Approver re-reviews. Repeats until `approved`.

---

### 7. Code Executor Agent

**Role:** Safely runs the approved cleaning code and captures results.

**Inputs:** Approved `generated_code.py`

**Responsibilities:**
- Execute the script (via `subprocess` or dynamic import) in a controlled environment.
- Monitor for runtime errors and record full execution logs.

**Output:**
- Cleaned files written to `data_lake_clean/`
- `execution_log.json` (files processed, durations, errors if any)

---

### 8. Final Evaluator Agent

**Role:** Closes the loop - measures how much the pipeline actually improved data quality.

**Inputs:** `audit_report.json` (before), `data_lake_clean/` (after), `execution_log.json`

**Responsibilities:**
- Re-run the same audit metrics on cleaned files.
- Compare before vs. after: missing values, schema consistency, formatting issues, etc.
- Produce a human-friendly report explaining what improved, what remains problematic, and recommendations for future governance.

**Output:** `final_report.md`

---

## Project Folder Structure

```
agentic-data-audit/
├── config/
│   └── llm_config.yaml          # LM Studio endpoint, model name, temperature
│
├── data_lake/                   # Raw messy input files (CSV, JSON)
├── data_lake_clean/             # Cleaned output files (written by Code Executor)
│
├── spec/
│   ├── audit_schema.json
│   ├── plan_schema.json
│   └── code_review_schema.json
│
├── shared/
│   ├── file_utils.py            # List files, sample rows
│   └── metrics.py               # Compute quality metrics
│
├── crewai_impl/                 # CrewAI implementation
├── langgraph_impl/              # LangGraph implementation
├── langchain_impl/              # LangChain implementation
├── autogen_impl/                # AutoGen implementation
│
└── run_pipeline.py              # Entry point: choose framework, run full flow
```

---

## ⚙️ Key Orchestration Properties

| Property | Where it appears |
|---|---|
| **Branching & loops** | Coder ↔ Code Approver retry loop |
| **Parallel agents** | Reviewer 1 and Reviewer 2 run simultaneously |
| **Human-in-the-loop** | Explicit approval gate before any code is generated |
| **State passing** | `audit → plan → reviews → approval → code → logs → final report` |
| **Framework comparison** | Same pipeline logic, 4 different implementations |

---

##  Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run with CrewAI (default)
python run_pipeline.py --path ./data_lake/ --framework crewai

# Run with LangGraph
python run_pipeline.py --path ./data_lake/ --framework langgraph
```

Configure your LM Studio endpoint in `config/llm_config.yaml`:
```yaml
endpoint: http://localhost:1234/v1
model: your-local-model-name
temperature: 0.2
```

---

*This agent chain mirrors patterns used in real agentic data quality systems: **discover → plan → committee → approve → implement → evaluate.***
