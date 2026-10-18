"""Label the clips the detector saved when it raised an alert: was it a real fall?

    python review_triggers.py triggers [reviewed.csv]

Plays each clip you haven't labelled yet (afplay on a Mac, aplay on Linux) and asks:
  r = a real fall      f = a false alarm      p = play again      s = skip      q = quit
Verdicts go to reviewed.csv (file,verdict), which build_room_dataset.py reads.
False alarms are the most valuable training clips: they are exactly the sounds of this
room that the model gets wrong.
"""
import csv
import shutil
import subprocess
import sys
from pathlib import Path


def done(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {r["file"]: r["verdict"] for r in csv.DictReader(f)}


def play(clip: Path) -> None:
    player = shutil.which("afplay") or shutil.which("aplay") or shutil.which("play")
    if player:
        subprocess.run([player, str(clip)], check=False)
    else:
        print("  (no audio player found; judge by the file name)")


def main() -> None:
    folder = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else folder.parent / "reviewed.csv"
    known = done(out)
    todo = [p for p in sorted(folder.glob("*.wav")) if p.name not in known]
    print(f"{len(todo)} clips to review ({len(known)} already done) -> {out}")
    new = not out.exists()
    with open(out, "a", newline="") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(["file", "verdict"])
        for i, clip in enumerate(todo, 1):
            print(f"\n[{i}/{len(todo)}] {clip.name}")
            play(clip)
            while True:
                a = input("  r=real fall  f=false alarm  p=again  s=skip  q=quit > ").strip().lower()
                if a == "p":
                    play(clip)
                elif a in ("r", "f"):
                    writer.writerow([clip.name, "real" if a == "r" else "false"])
                    f.flush()
                    break
                elif a in ("s", "q"):
                    break
            if a == "q":
                return


if __name__ == "__main__":
    main()
