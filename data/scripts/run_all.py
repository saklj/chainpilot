"""Run the complete ChainPilot data pipeline in fail-fast order."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PIPELINE_STEPS = (
    "download_m5.py",
    "preprocess_m5.py",
    "generate_sim.py",
    "build_db.py",
)


def run_all() -> None:
    """Execute every data pipeline stage with the current Python interpreter."""
    for script_name in PIPELINE_STEPS:
        print(f"\n=== {script_name} ===", flush=True)
        subprocess.run([sys.executable, str(SCRIPT_DIR / script_name)], check=True)
    print("\nChainPilot 数据底座全流程成功")


def main() -> None:
    """Run the pipeline and propagate the first failed stage."""
    try:
        run_all()
    except subprocess.CalledProcessError as exc:
        print(f"流水线停止: {Path(exc.cmd[-1]).name} exit={exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
