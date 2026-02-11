import os
import sys
import contextlib
import io

# Ensure repo root and src on path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Run with cutoff + non-interactive
os.environ.setdefault("UFC_INTERACTIVE", "0")
os.environ.setdefault("UFC_CUTOFF_DATE", "2025-12-31")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    import UFC_prefight_pro

fights_to_predict = [
    ("Islam Makhachev", "Kamaru Usman"),
    ("Justin Gaethje", "Paddy Pimblett"),
    ("Rafael Fiziev", "Mauricio Ruffy"),
    ("Alexander Volkanovski", "Diego Lopes"),
    ("Sean O'Malley", "Song Yadong"),
]

print("Cutoff date:", os.environ.get("UFC_CUTOFF_DATE"))
print("\nPredictions (F1 win probability):")
for f1, f2 in fights_to_predict:
    p3, _ = UFC_prefight_pro.predict_match(f1, f2)
    print(f"{f1} vs {f2}: UFC_prefight_pro={p3:.4%}")
