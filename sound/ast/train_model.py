
from pathlib import Path

import numpy as np
import pandas as pd
import soundfile as sf
import librosa
import torch

from torch.utils.data import Dataset
from transformers import (
    ASTFeatureExtractor,
    ASTForAudioClassification,
    Trainer,
    TrainingArguments,
)
from sklearn.metrics import accuracy_score, f1_score

ROOT = Path(r".\ESC-50-master\ESC-50-master")
CSV = ROOT / "meta" / "esc50.csv"
AUDIO = ROOT / "audio"
OUTPUT = Path("./esc50_ast_model")

CHECKPOINT = "MIT/ast-finetuned-audioset-10-10-0.4593"
SAMPLE_RATE = 16000

if not CSV.exists():
    raise FileNotFoundError(f"Metadata not found: {CSV}")
if not AUDIO.exists():
    raise FileNotFoundError(f"Audio directory not found: {AUDIO}")

df = pd.read_csv(CSV)

# ESC-50's official fold 5 is held out for final testing.
train_df = df[df["fold"].isin([1, 2, 3, 4])].copy()
test_df = df[df["fold"] == 5].copy()

# Split part of the training folds for validation.
# Keep fold 4 separate from training.
val_df = train_df[train_df["fold"] == 4].copy()
train_df = train_df[train_df["fold"].isin([1, 2, 3])].copy()

classes = sorted(df["category"].unique())
label2id = {name: i for i, name in enumerate(classes)}
id2label = {i: name for name, i in label2id.items()}

extractor = ASTFeatureExtractor.from_pretrained(CHECKPOINT)

class ESC50Dataset(Dataset):
    def __init__(self, frame):
        self.frame = frame.reset_index(drop=True)

    def __len__(self):
        return len(self.frame)

    def __getitem__(self, idx):
        row = self.frame.iloc[idx]
        path = AUDIO / row["filename"]

        audio, sr = sf.read(path, dtype="float32")

        if audio.ndim == 2:
            audio = audio.mean(axis=1)

        if sr != SAMPLE_RATE:
            audio = librosa.resample(
                audio,
                orig_sr=sr,
                target_sr=SAMPLE_RATE,
            )

        # ESC-50 clips are normally 5 seconds.
        length = 5 * SAMPLE_RATE
        if len(audio) < length:
            audio = np.pad(audio, (0, length - len(audio)))
        else:
            audio = audio[:length]

        features = extractor(
            audio,
            sampling_rate=SAMPLE_RATE,
            return_tensors="pt",
        )

        return {
            "input_values": features["input_values"].squeeze(0),
            "labels": label2id[row["category"]],
        }

model = ASTForAudioClassification.from_pretrained(
    CHECKPOINT,
    num_labels=len(classes),
    label2id=label2id,
    id2label=id2label,
    ignore_mismatched_sizes=True,
)

def compute_metrics(pred):
    logits, labels = pred
    predicted = np.argmax(logits, axis=1)
    return {
        "accuracy": accuracy_score(labels, predicted),
        "macro_f1": f1_score(
            labels, predicted, average="macro",
            zero_division=0,
        ),
    }

args = TrainingArguments(
    output_dir="./checkpoints",
    learning_rate=1e-5,
    per_device_train_batch_size=8,
    per_device_eval_batch_size=8,
    num_train_epochs=8,
    weight_decay=0.01,
    eval_strategy="epoch",
    save_strategy="epoch",
    load_best_model_at_end=True,
    metric_for_best_model="macro_f1",
    greater_is_better=True,
    logging_steps=20,
    save_total_limit=2,
    fp16=torch.cuda.is_available(),
    report_to="none",
)

trainer = Trainer(
    model=model,
    args=args,
    train_dataset=ESC50Dataset(train_df),
    eval_dataset=ESC50Dataset(val_df),
    compute_metrics=compute_metrics,
)

print("Training examples:", len(train_df))
print("Validation examples:", len(val_df))
print("Test examples:", len(test_df))
print("Classes:", len(classes))
print("GPU available:", torch.cuda.is_available())

trainer.train()

print("\nHeld-out test results:")
print(trainer.evaluate(ESC50Dataset(test_df)))

OUTPUT.mkdir(parents=True, exist_ok=True)
trainer.save_model(str(OUTPUT))
extractor.save_pretrained(str(OUTPUT))

print(f"\nModel saved to: {OUTPUT.resolve()}")
