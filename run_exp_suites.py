#!/usr/bin/env python3
import subprocess as sp
import os, sys
from pathlib import Path
from datetime import datetime

# ========= CONFIG =========
BIN = Path(os.environ.get("MADVBENCH_BIN", "./madvbench")).resolve()
FILE = Path(os.environ.get("MADVBENCH_FILE", "test.dat")).resolve()
REPEAT = int(os.environ.get("MADVBENCH_REPEAT", "5"))
SEED = int(os.environ.get("MADVBENCH_SEED", "1"))
OUT_DIR = Path(os.environ.get("MADVBENCH_OUTDIR", "out_madv")).resolve()



# Optional pinning (leave empty or set like: "taskset -c 0-3" or "numactl --cpunodebind=0 --membind=0")
PIN = os.environ.get("MADVBENCH_PIN", "").strip()

# Suites (tuned for graphs 1–5)
PATTERNS_CORE = ["seq", "rand", "stride:4"]                    # figs 1–3
TEMPS_CORE = ["cold", "hot"]
MADVS = ["none", "seq", "rand"]

SIZE_RATIOS = [0.50, 0.75, 1.00, 1.25, 1.50]                   # fig 4 (seq,cold)
STRIDES = [1, 2, 4, 8, 16, 32, 64, 128]                        # fig 5 (cold, sr=1.0)

CORE_CSV = "core.csv"          # figs 1–3
SIZE_CSV = "size_sweep.csv"    # fig 4
STRD_CSV = "stride_sweep.csv"  # fig 5
ALL_CSV  = "results_all.csv"
# ==========================


def ensure_outdir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

def ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def run_madv(args) -> tuple[str, str, int]:
    """
    Runs BIN with given args, returns (stdout, stderr, rc).
    stdout = CSV (header + rows), stderr = human logs (we keep them).
    """
    cmd = []
    if PIN:
        cmd.extend(PIN.split())
    cmd.append(BIN)
    cmd.extend(args)
    p = sp.run(cmd, stdout=sp.PIPE, stderr=sp.PIPE, text=True)
    return p.stdout, p.stderr, p.returncode

def append_csv_text(dest_path: Path, csv_text: str):
    """Append CSV text to dest file; keep header only once."""
    if not csv_text.strip():
        return
    lines = csv_text.splitlines()
    if not lines:
        return
    header = lines[0]
    body = lines[1:]  # may be empty if only header present
    if not dest_path.exists() or dest_path.stat().st_size == 0:
        dest_path.write_text(header + "\n")
    with dest_path.open("a") as f:
        for i, line in enumerate(lines):
            if i == 0:  # header
                continue
            if line.strip():
                f.write(line + "\n")

def log_append(log_path: Path, text: str):
    if not text:
        return
    with log_path.open("a") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")


def run_core_suite():
    """patterns x temps x madv at size_ratio=1.0 (figs 1–3)."""
    dest_csv = OUT_DIR / CORE_CSV
    log_path = OUT_DIR / "core.log"
    dest_csv.write_text("")  # truncate
    log_append(log_path, f"[{ts()}] CORE SUITE START")
    for temp in TEMPS_CORE:
        for pat in PATTERNS_CORE:
            for madv in MADVS:
                args = [
                    "--file", FILE,
                    "--size-ratio", "1.0",
                    "--pattern", pat,
                    "--madv", madv,
                    "--repeat", str(REPEAT),
                    "--temp", temp,
                    "--seed", str(SEED),
                    "--csv", "yes",
                ]
                out, err, rc = run_madv(args)
                append_csv_text(dest_csv, out)
                log_append(log_path, f"[{ts()}] rc={rc} temp={temp} pattern={pat} madv={madv}\n{err}")
    log_append(log_path, f"[{ts()}] CORE SUITE DONE")


SIZE_PATTERNS = ["seq", "rand"] + [f"stride:{s}" for s in STRIDES]  # or just ["seq","rand"] if you don't want strides

def run_size_sweep():
    """size_ratio x temp x pattern x madv (for Fig 2a/2b if you want full coverage)."""
    dest_csv = OUT_DIR / SIZE_CSV
    log_path = OUT_DIR / "size_sweep.log"
    dest_csv.write_text("")
    log_append(log_path, f"[{ts()}] SIZE SWEEP START")
    for sr in SIZE_RATIOS:
        sr_str = f"{sr:.2f}"
        for temp in TEMPS_CORE:                     # cold + hot
            for pat in SIZE_PATTERNS:               # <<< added patterns here
                for madv in MADVS:
                    args = [
                        "--file", FILE,
                        "--size-ratio", sr_str,
                        "--pattern", pat,
                        "--madv", madv,
                        "--repeat", str(REPEAT),
                        "--temp", temp,
                        "--seed", str(SEED),
                        "--csv", "yes",
                    ]
                    out, err, rc = run_madv(args)
                    append_csv_text(dest_csv, out)
                    log_append(log_path, f"[{ts()}] rc={rc} sr={sr_str} temp={temp} pattern={pat} madv={madv}\n{err}")
    log_append(log_path, f"[{ts()}] SIZE SWEEP DONE")


def run_stride_sweep():
    """stride x madv x temp at size_ratio=1.0 (fig 5)."""
    dest_csv = OUT_DIR / STRD_CSV
    log_path = OUT_DIR / "stride_sweep.log"
    dest_csv.write_text("")
    log_append(log_path, f"[{ts()}] STRIDE SWEEP START")
    for stride in STRIDES:
        pat = f"stride:{stride}"
        for temp in TEMPS_CORE:                        
            for madv in MADVS:
                args = [
                    "--file", FILE,
                    "--size-ratio", "1.0",
                    "--pattern", pat,
                    "--madv", madv,
                    "--repeat", str(REPEAT),
                    "--temp", temp,                  
                    "--seed", str(SEED),
                    "--csv", "yes",
                ]
                out, err, rc = run_madv(args)
                append_csv_text(dest_csv, out)
                log_append(log_path, f"[{ts()}] rc={rc} stride={stride} temp={temp} madv={madv}\n{err}")
    log_append(log_path, f"[{ts()}] STRIDE SWEEP DONE")

def combine_csvs():
    """Combine core + size + stride into results_all.csv (header once)."""
    dest = OUT_DIR / ALL_CSV
    dest.write_text("")

    def append_file(fp: Path):
        if not fp.exists(): return
        txt = fp.read_text()
        append_csv_text(dest, txt)

    append_file(OUT_DIR / CORE_CSV)
    append_file(OUT_DIR / SIZE_CSV)
    append_file(OUT_DIR / STRD_CSV)

def main():
    ensure_outdir()

    # Basic checks
    if not Path(BIN).exists():
        sys.exit(f"ERROR: binary not found: {BIN}")
    if not Path(FILE).exists():
        sys.exit(f"ERROR: file not found: {FILE}")

    print(f"[{ts()}] Running suites with BIN={BIN}, FILE={FILE}, REPEAT={REPEAT}, SEED={SEED}")
    if PIN:
        print(f"[{ts()}] Pinning via: {PIN}")

    run_core_suite()
    run_size_sweep()
    run_stride_sweep()
    combine_csvs()

    print(f"[{ts()}] Done.")
    print(f"  Core CSV:      {OUT_DIR / CORE_CSV}")
    print(f"  Size sweep:    {OUT_DIR / SIZE_CSV}")
    print(f"  Stride sweep:  {OUT_DIR / STRD_CSV}")
    print(f"  Combined CSV:  {OUT_DIR / ALL_CSV}")
    print(f"  Logs: {OUT_DIR/'core.log'} | {OUT_DIR/'size_sweep.log'} | {OUT_DIR/'stride_sweep.log'}")

if __name__ == "__main__":
    main()