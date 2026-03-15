# remap_this_dataset.py — run once before training
import os, glob

LABEL_DIR = "F:/FaceNet/galamsey_dataset"

# Old ID → New ID (None = drop)
REMAP = {
    0: 2,     # Dump-Truck-Idle      → mining_truck
    1: 0,     # Excavator-Bucket-Unload → excavator
    2: 0,     # Excavator-Bucket-load   → excavator
    3: 0,     # Excavator-Working       → excavator
    4: None,  # r                       → drop
}

total = 0
for split in ["train", "val"]:
    for fp in glob.glob(f"{LABEL_DIR}/{split}/labels/*.txt"):
        with open(fp) as f:
            lines = f.readlines()
        new_lines = []
        for line in lines:
            parts = line.strip().split()
            if not parts: continue
            new_cls = REMAP.get(int(parts[0]))
            if new_cls is None: continue
            parts[0] = str(new_cls)
            new_lines.append(" ".join(parts))
        with open(fp, "w") as f:
            f.write("\n".join(new_lines))
        total += 1

print(f"Remapped {total} label files")