"""Train a classifier head on frozen AST embeddings; report val/test/hf_test."""
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix

HERE = Path(__file__).parent
df = pd.read_csv(HERE / "manifest.csv")
df["label"] = df.label.replace({"cry": "distress", "scream": "distress"})  # one alert class
emb = np.load(HERE / "embeddings.npz")["emb"]

tr = (df.split == "train").values
clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
clf.fit(emb[tr], df.label[tr])

for split in ("val", "test", "hf_test"):
    m = (df.split == split).values
    pred = clf.predict(emb[m])
    print(f"\n=== {split} ({m.sum()} clips) ===")
    print(classification_report(df.label[m], pred, zero_division=0))
    print(confusion_matrix(df.label[m], pred, labels=clf.classes_), clf.classes_.tolist())

joblib.dump(clf, HERE / "head.joblib")
