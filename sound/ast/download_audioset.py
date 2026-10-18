"""Stream agkphysics/AudioSet parquet shards, keep only cry/scream clips as wav, delete each shard.

Output: ../data/audioset/<split>/<label>/<video_id>.wav  (split = bal_train | eval)
"""
import io
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import HfApi, hf_hub_download

REPO = "agkphysics/AudioSet"
OUT = Path(__file__).parent.parent / "data" / "audioset"
KAGGLE = Path(__file__).parent.parent / "data" / "kaggle_scream"
# AudioSet human label -> our label
WANT = {"Crying, sobbing": "cry", "Baby cry, infant cry": "cry", "Whimper": "cry", "Screaming": "scream"}

skip = {p.name.rsplit("_out", 1)[0] for p in KAGGLE.rglob("*.wav")}
api = HfApi()
files = sorted(f for f in api.list_repo_files(REPO, repo_type="dataset")
               if f.startswith(("data/bal_train/", "data/eval/")))

for n, f in enumerate(files):
    split = f.split("/")[1]
    path = hf_hub_download(REPO, f, repo_type="dataset")
    pf = pq.ParquetFile(path)
    kept = 0
    for batch in pf.iter_batches(batch_size=64):
        for r in batch.to_pylist():
            lab = next((WANT[h] for h in r["human_labels"] if h in WANT), None)
            if lab is None or r["video_id"] in skip:
                continue
            d = OUT / split / lab
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{r['video_id']}.wav").write_bytes(r["audio"]["bytes"])
            kept += 1
    Path(path).resolve().unlink()  # free the shard (symlink target in HF cache)
    print(f"{n + 1}/{len(files)} {f} kept {kept}", flush=True)
print("done")
