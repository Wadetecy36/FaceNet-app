# ============================================================
# galamsey_train.py (v2)
# Auto-detects dataset classes from Roboflow data.yaml
# CPU-optimized for Intel i5 8th Gen + 12GB RAM
#
# Usage:
#   python galamsey_train.py           → check dataset stats
#   python galamsey_train.py --train   → start training
#   python galamsey_train.py --val     → validate trained model
#   python galamsey_train.py --test    → run on a test image
# ============================================================

import os
import sys
import yaml
import shutil
import glob
import time
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────

DATASET_DIR  = "F:/FaceNet/galamsey_dataset"
PROJECT_DIR  = "F:/FaceNet/galamsey_runs"
MODEL_NAME   = "galamsey_v1"
BASE_MODEL   = "yolov8s.pt"

# Roboflow dataset yaml — auto-detected from download
ROBOFLOW_YAML = "F:/Downloads/EXCAVATOR.v1i.yolov8/data.yaml"

# ─── Training config (tuned for your CPU + 12GB RAM) ─────────────────────────

EPOCHS      = 80      # good balance for ~500 images on CPU
IMG_SIZE    = 416     # smaller than 640 = faster CPU training, still accurate
BATCH_SIZE  = 4       # safe for 12GB RAM on CPU
PATIENCE    = 15      # stop if no improvement for 15 epochs
WORKERS     = 2       # CPU workers for data loading

# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_roboflow_yaml(yaml_path: str) -> dict:
    """Read the Roboflow data.yaml to extract classes automatically."""
    if not os.path.exists(yaml_path):
        print(f"[!] data.yaml not found at: {yaml_path}")
        print("    Update ROBOFLOW_YAML path at the top of this script.")
        sys.exit(1)
    with open(yaml_path) as f:
        return yaml.safe_load(f)


def build_dataset_yaml(roboflow_data: dict) -> str:
    """
    Build our own dataset yaml pointing to the correct local folders,
    but using the classes from the Roboflow yaml automatically.
    """
    os.makedirs(DATASET_DIR, exist_ok=True)

    classes = roboflow_data.get("names", [])
    nc      = roboflow_data.get("nc", len(classes))

    config = {
        "path":  DATASET_DIR,
        "train": "train/images",
        "val":   "val/images",
        "nc":    nc,
        "names": classes,
    }

    out_path = f"{DATASET_DIR}/galamsey.yaml"
    with open(out_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    return out_path


def count_dataset() -> dict:
    """Count images and labels per split."""
    stats = {}
    for split in ["train", "val"]:
        img_dir = f"{DATASET_DIR}/{split}/images"
        lbl_dir = f"{DATASET_DIR}/{split}/labels"
        imgs = len(glob.glob(f"{img_dir}/*")) if os.path.exists(img_dir) else 0
        lbls = len(glob.glob(f"{lbl_dir}/*")) if os.path.exists(lbl_dir) else 0
        stats[split] = {"images": imgs, "labels": lbls}
    return stats


def print_dataset_summary(roboflow_data: dict):
    """Print a full summary before training starts."""
    classes = roboflow_data.get("names", [])
    stats   = count_dataset()

    print("\n" + "=" * 55)
    print("  GALAMSEY DETECTION — DATASET SUMMARY")
    print("=" * 55)
    print(f"  Classes ({len(classes)}):")
    for i, c in enumerate(classes):
        print(f"    [{i}] {c}")
    print()
    for split, s in stats.items():
        ok = "✓" if s["images"] >= 100 else "⚠ LOW"
        print(f"  {split:6}: {s['images']:4} images | {s['labels']:4} labels  {ok}")
    print()
    print(f"  Base model:  {BASE_MODEL}")
    print(f"  Epochs:      {EPOCHS}  (early stop after {PATIENCE} idle)")
    print(f"  Image size:  {IMG_SIZE}px  (CPU-optimised)")
    print(f"  Batch size:  {BATCH_SIZE}")
    print(f"  Output:      {PROJECT_DIR}/{MODEL_NAME}/")
    print("=" * 55)

    total = stats["train"]["images"] + stats["val"]["images"]
    if total < 100:
        print("\n  [!] Not enough images to train reliably.")
        print("      Add more images and try again.")
        print("      Target: 300+ train, 80+ val\n")
        sys.exit(1)


# ─── Training ─────────────────────────────────────────────────────────────────

def train():
    from ultralytics import YOLO

    roboflow_data = load_roboflow_yaml(ROBOFLOW_YAML)
    yaml_path     = build_dataset_yaml(roboflow_data)
    print_dataset_summary(roboflow_data)

    print("\n  Starting training — this will take a while on CPU.")
    print("  Estimated time: 2-5 hours for 80 epochs on i5 8th Gen.")
    print("  Leave it running and come back. Press Ctrl+C to pause.\n")
    time.sleep(2)

    model = YOLO(BASE_MODEL)

    results = model.train(
        data       = yaml_path,
        epochs     = EPOCHS,
        imgsz      = IMG_SIZE,
        batch      = BATCH_SIZE,
        name       = MODEL_NAME,
        project    = PROJECT_DIR,
        patience   = PATIENCE,
        workers    = WORKERS,
        device     = "cpu",
        save       = True,
        save_period= 10,         # save checkpoint every 10 epochs
        plots      = True,

        # Augmentation — helps a lot with small datasets
        augment    = True,
        degrees    = 5.0,        # slight rotation (excavators tilt on slopes)
        fliplr     = 0.5,        # horizontal flip
        flipud     = 0.0,        # no vertical flip (excavators don't fly)
        mosaic     = 0.8,        # combine 4 images (helps with small datasets)
        mixup      = 0.1,        # blend images slightly
        hsv_h      = 0.015,      # hue variation
        hsv_s      = 0.5,        # saturation variation (dusty vs wet conditions)
        hsv_v      = 0.3,        # brightness variation (day vs overcast)
        scale      = 0.3,        # random scale ± 30%
        translate  = 0.1,        # random translation
    )

    # ── Copy best model to FaceNet folder ────────────────────
    best_src = f"{PROJECT_DIR}/{MODEL_NAME}/weights/best.pt"
    best_dst = "F:/FaceNet/galamsey.pt"

    if os.path.exists(best_src):
        shutil.copy2(best_src, best_dst)
        print(f"\n{'='*55}")
        print(f"  Training complete!")
        print(f"  Best model copied to: {best_dst}")
        print(f"\n  To use in FaceNet:")
        print(f"  Open yolo_server.py and change:")
        print(f"    YOLO_MODEL = 'galamsey.pt'")
        print(f"  Then restart: uvicorn yolo_server:app --host 0.0.0.0 --port 8000 --reload")
        print(f"{'='*55}\n")
    else:
        print(f"\n[!] best.pt not found — check {PROJECT_DIR}/{MODEL_NAME}/weights/")

    return results


# ─── Validation ───────────────────────────────────────────────────────────────

def validate():
    from ultralytics import YOLO

    model_path = f"{PROJECT_DIR}/{MODEL_NAME}/weights/best.pt"
    if not os.path.exists(model_path):
        print(f"[!] No trained model at {model_path}")
        print("    Run: python galamsey_train.py --train")
        return

    roboflow_data = load_roboflow_yaml(ROBOFLOW_YAML)
    yaml_path     = build_dataset_yaml(roboflow_data)

    model   = YOLO(model_path)
    metrics = model.val(data=yaml_path, device="cpu")

    print(f"\n{'='*55}")
    print(f"  Validation Results")
    print(f"{'='*55}")
    print(f"  mAP@50:    {metrics.box.map50:.3f}  (target: >0.7)")
    print(f"  mAP@50-95: {metrics.box.map:.3f}  (target: >0.5)")
    print(f"  Precision: {metrics.box.mp:.3f}")
    print(f"  Recall:    {metrics.box.mr:.3f}")

    if metrics.box.map50 >= 0.7:
        print(f"\n  Model is ready for deployment!")
    elif metrics.box.map50 >= 0.5:
        print(f"\n  Decent model — train more epochs for better accuracy.")
    else:
        print(f"\n  Model needs more data or training. Add more images.")
    print(f"{'='*55}\n")


# ─── Quick test on a single image ─────────────────────────────────────────────

def test_image(image_path: str = None):
    from ultralytics import YOLO

    model_path = f"{PROJECT_DIR}/{MODEL_NAME}/weights/best.pt"
    if not os.path.exists(model_path):
        # Fall back to galamsey.pt in FaceNet folder
        model_path = "F:/FaceNet/galamsey.pt"

    if not os.path.exists(model_path):
        print("[!] No trained model found. Run --train first.")
        return

    if not image_path:
        # Use first image from val set as test
        val_imgs = glob.glob(f"{DATASET_DIR}/val/images/*")
        if not val_imgs:
            print("[!] No val images found.")
            return
        image_path = val_imgs[0]

    print(f"Testing on: {image_path}")
    model   = YOLO(model_path)
    results = model(image_path, conf=0.4, device="cpu")

    for r in results:
        print(f"\nDetections: {len(r.boxes)}")
        for box in r.boxes:
            cls  = int(box.cls[0])
            conf = float(box.conf[0])
            name = model.names[cls]
            print(f"  [{cls}] {name:20} {conf:.1%}")
        r.save(filename="F:/FaceNet/test_result.jpg")
        print(f"\nResult saved to F:/FaceNet/test_result.jpg")


# ─── Dataset check ────────────────────────────────────────────────────────────

def check():
    roboflow_data = load_roboflow_yaml(ROBOFLOW_YAML)
    build_dataset_yaml(roboflow_data)
    print_dataset_summary(roboflow_data)
    print("  Dataset looks good. Run with --train to start.\n")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--train" in sys.argv:
        train()
    elif "--val" in sys.argv:
        validate()
    elif "--test" in sys.argv:
        img = sys.argv[sys.argv.index("--test") + 1] if len(sys.argv) > sys.argv.index("--test") + 1 else None
        test_image(img)
    else:
        check()