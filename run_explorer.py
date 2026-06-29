from audit.data_explorer_agent import build_audit_report, save_audit_report
import os

if __name__ == "__main__":
    report = build_audit_report()
    output_path = os.path.join(os.path.dirname(__file__), "audit_report.json")
    save_audit_report(report, output_path)
    print(f"Audit report generated at: {output_path}")