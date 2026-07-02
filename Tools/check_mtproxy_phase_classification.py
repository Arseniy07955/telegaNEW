#!/usr/bin/env python3
"""Guard: MtProxyPhaseClassification.h must match Tools/mtproxy_phase_contract.py.

The header is generated (single source of truth is the Python contract); a
stale checked-in copy means a phase classification edit did not go through the
contract. Regenerate with Tools/generate_mtproxy_phase_classification.py.
"""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "Tools/generate_mtproxy_phase_classification.py"


def main() -> int:
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        print("MTProxy phase classification guard failed.", file=sys.stderr)
        return 1
    print("MTProxy phase classification guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
