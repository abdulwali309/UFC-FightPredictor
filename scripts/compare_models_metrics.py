import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODELS = [
    ("UFC_prefight_pro.py", "UFC_prefight_pro"),
]


def run_model(path):
    env = os.environ.copy()
    env["UFC_INTERACTIVE"] = "0"
    proc = subprocess.run(
        ["python", path],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.stdout + "\n" + proc.stderr


def parse_metrics(output):
    def grab(pattern):
        m = re.search(pattern, output)
        return float(m.group(1)) if m else None

    return {
        "accuracy": grab(r"Accuracy:\s*([0-9.]+)"),
        "roc_auc": grab(r"ROC AUC:\s*([0-9.]+)"),
        "log_loss": grab(r"Log Loss:\s*([0-9.]+)"),
        "brier": grab(r"Brier Score:\s*([0-9.]+)"),
    }


print("Model evaluation (chronological split):")
for path, label in MODELS:
    out = run_model(path)
    metrics = parse_metrics(out)
    print(f"\n{label}:")
    print(f"  Accuracy: {metrics['accuracy']}")
    print(f"  ROC AUC:  {metrics['roc_auc']}")
    print(f"  Log Loss: {metrics['log_loss']}")
    print(f"  Brier:    {metrics['brier']}")
