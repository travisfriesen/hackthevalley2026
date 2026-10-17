
import json
from pathlib import Path
import pandas as pd

json_path = Path(r".\human_events.json")
csv_path = Path(r".\ESC-50-master\ESC-50-master\meta\esc50.csv")

# Inspect human_events.json
print("\n=== HUMAN EVENTS JSON ===")
with json_path.open("r", encoding="utf-8") as f:
    data = json.load(f)

print("Root type:", type(data).__name__)

if isinstance(data, dict):
    print("Root keys:", list(data.keys()))
    for key, value in data.items():
        print(f"\nKey: {key!r}")
        if isinstance(value, list):
            print("List length:", len(value))
            if value:
                print("First item:", repr(value[0])[:1500])
        elif isinstance(value, dict):
            print("Nested keys:", list(value.keys())[:40])
        else:
            print("Value:", repr(value)[:500])

elif isinstance(data, list):
    print("Number of entries:", len(data))
    for item in data[:3]:
        print(repr(item)[:1500])

# Inspect ESC-50 metadata
print("\n=== ESC-50 CSV ===")
df = pd.read_csv(csv_path)
print("Columns:", df.columns.tolist())
print("\nFirst five rows:")
print(df.head().to_string(index=False))
print("\nSound categories:")
print(df["category"].value_counts().to_string())

# Check audio files
audio_dir = csv_path.parent.parent / "audio"
print("\n=== ESC-50 AUDIO FILES ===")
print("Audio directory:", audio_dir)
print("Directory exists:", audio_dir.exists())

if audio_dir.exists():
    print("WAV file count:", len(list(audio_dir.glob("*.wav"))))
