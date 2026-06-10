"""Weekly branded-content refresh — orchestrates the whole pipeline so
new videos get a promotion verdict + sponsor with no manual steps.

Runs, in order:
  1. scan_branded_content     — find candidates (YouTube flag + text
                                disclosure phrases, multilingual).
  2. normalize_brand_candidates — clean the raw brand captures.
  3. verify_branded_candidates  — curated CANON map + FALSE set, plus
                                  auto-confirm for NEW sponsors using
                                  the normalized name.
  4. extract_flag_brands        — pull the sponsor for flag-only videos
                                  from @mentions / possessives.

Idempotent end to end. Cheap by design: the scan is INCREMENTAL
(only videos published in the last ~10 days), so the whole weekly run
is ~1 min, not a 187k-video re-scan. Pass nothing for the normal
weekly run; to rebuild from scratch run
`scan_branded_content.py --full` once by hand.

Usage:
  python3 scripts/refresh_branded.py
"""
from __future__ import annotations

import os
import runpy
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_STEPS = [
    "scan_branded_content",
    "normalize_brand_candidates",
    "verify_branded_candidates",
    "extract_flag_brands",
]


def main() -> int:
    here = Path(__file__).resolve().parent
    start = time.time()
    for name in _STEPS:
        print(f"\n{'='*60}\n▶ {name}\n{'='*60}", flush=True)
        t0 = time.time()
        try:
            runpy.run_path(str(here / f"{name}.py"), run_name="__main__")
        except SystemExit as e:
            # Scripts call sys.exit(); a non-zero from a step is logged
            # but we continue — a later step still improves coverage.
            if e.code:
                print(f"  ⚠ {name} exited with {e.code}", flush=True)
        except Exception as e:
            print(f"  ✗ {name} failed: {e}", flush=True)
        print(f"  {name} done in {time.time() - t0:.0f}s", flush=True)
    print(f"\nPipeline complete in {time.time() - start:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
