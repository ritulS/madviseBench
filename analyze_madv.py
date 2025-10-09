#!/usr/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

# ---------- CONFIG ----------
CSV_PATH = Path("out_madv/results_all.csv")
OUT_DIR  = Path("out_madv/figs")
OUT_DIR.mkdir(parents=True, exist_ok=True)
sns.set_theme(style="whitegrid")
# ----------------------------

# ---------- LOAD + CLEAN ----------
df = pd.read_csv(CSV_PATH)
df["pattern"] = df["pattern"].astype(str)
df["madv"] = df["madv"].astype(str)
df["temp"] = df["temp"].astype(str)

# convenience keys
keys = ["pattern", "temp", "size_ratio", "stride_pages", "madv"]

# ---------- AGGREGATE ----------
def p10(s): return s.quantile(0.10)
def p90(s): return s.quantile(0.90)
def p99(s): return s.quantile(0.99)

stats = (
    df.groupby(keys)
      .agg(p50_thr=("throughput_mibps","median"),
           p10_thr=("throughput_mibps", p10),
           p90_thr=("throughput_mibps", p90),
           p50_time=("time_s","median"),
           p50_minflt=("minflt","median"),
           p50_majflt=("majflt","median"))
      .reset_index()
)

# ---------- (1) LATENCY p50/p99 with per-run rows + whiskers ----------
import numpy as np

# Keys that define one experiment combo
keys = ["pattern","temp","size_ratio","stride_pages","madv"]

# (A) Compute group stats on raw runs
grp_stats = (
    df.groupby(keys, as_index=False)
      .agg(p50_time=("time_s", "median"),
           p99_time=("time_s", lambda s: np.percentile(s, 99)))
)

# (B) Merge stats back onto every raw run row -> "lat" now has ALL runs + p50/p99 per combo
lat = df.merge(grp_stats, on=keys, how="left")

# (C) Restrict to size_ratio=1.0 (no hot/cold averaging later)
lat1 = lat[lat["size_ratio"] == 1.0].copy()
# print(lat1)

# ---- label helpers + consistent ordering ----
def short_pattern(p):
    if isinstance(p, str) and p.startswith("stride:"):
        return f"s{p.split(':',1)[1]}"   # stride:2 -> s2
    return str(p)

def pat_key(x: str):
    x = str(x)
    if x == "rand": return (0, 0)
    if x == "seq":  return (1, 0)
    if x.startswith("s") and x[1:].isdigit():
        return (2, int(x[1:]))   # s1, s2, s4, ...
    return (3, 0)

lat1["pattern"] = lat1["pattern"].apply(short_pattern)
pattern_order = sorted(lat1["pattern"].unique(), key=pat_key)
print(pattern_order)
lat1["pattern"] = pd.Categorical(lat1["pattern"], categories=pattern_order, ordered=True)

madv_order = ["none","rand","seq"]
lat1["madv"] = pd.Categorical(lat1["madv"],
                              categories=[m for m in madv_order if m in lat1["madv"].unique()],
                              ordered=True)

# ---- estimators for bars ----
def pct99(a):
    # a is a 1D array of run times for a (pattern,temp,size_ratio,stride_pages,madv) group
    return np.percentile(a, 99)

# ---- plotting: split hot/cold to avoid averaging them together ----
def plot_by_temp(temp_label: str, outname: str):
    d = lat1[lat1["temp"] == temp_label].copy()
    if d.empty:
        print(f"[warn] no data for temp={temp_label}")
        return

    fig, axes = plt.subplots(1, 2, figsize=(12,5), sharey=False)

    # p50: bar = median across runs; whiskers = 90% percentile interval across runs
    sns.barplot(
        data=d, x="pattern", y="time_s", hue="madv",
        order=pattern_order, estimator=np.median,
        errorbar=("pi", 90), capsize=0.2, ax=axes[0]
    )
    axes[0].set_title(f"Latency p50 by MADV (size_ratio=1.0, temp={temp_label})")
    axes[0].set_xlabel("Access Pattern")
    axes[0].set_ylabel("Time (s)")
    axes[0].legend_.remove()

    # p99: bar = 99th percentile across runs; whiskers = 90% percentile interval of that estimator via bootstrap
    sns.barplot(
        data=d, x="pattern", y="time_s", hue="madv",
        order=pattern_order, estimator=pct99,
        errorbar=("pi", 90), capsize=0.2, ax=axes[1]
    )
    axes[1].set_title(f"Latency p99 by MADV (size_ratio=1.0, temp={temp_label})")
    axes[1].set_xlabel("Access Pattern")
    axes[1].set_ylabel("Time (s)")
    axes[1].set_yscale("log")
    axes[1].legend(title="MADV", loc="upper left")

    plt.tight_layout()
    plt.savefig(OUT_DIR / outname, dpi=200)
    plt.close()

# Render separate figures (no hot/cold mixing)
plot_by_temp("cold", "fig_latency_p50_p99_cold.png")
plot_by_temp("hot",  "fig_latency_p50_p99_hot.png")





# ---------- [2] THROUGHPUT + MINOR FAULTS (cold & hot, grouped by MADV, s-labels) ----------

#### ---------- (2a) THROUGHPUT: size_ratio in {1.0, 1.5} × {cold, hot} ----------
sizes = [1.0, 1.5]
temps = ["cold", "hot"]

runs = df[df["size_ratio"].isin(sizes)].copy()

runs["pattern"] = runs["pattern"].apply(short_pattern)

pattern_order = sorted(runs["pattern"].unique(), key=pat_key)
runs["pattern"] = pd.Categorical(runs["pattern"], categories=pattern_order, ordered=True)

# keep a stable MADV order but only include those present
madv_order = ["none", "rand", "seq", "willneed", "dontneed", "free"]
runs["madv"] = pd.Categorical(
    runs["madv"],
    categories=[m for m in madv_order if m in runs["madv"].unique()],
    ordered=True
)

fig, axes = plt.subplots(len(temps), len(sizes), figsize=(12, 8), sharex=True, sharey=True)
if len(temps) == 1: axes = [axes]  # normalize for single row

for i, temp in enumerate(temps):
    for j, sr in enumerate(sizes):
        d = runs[(runs["temp"] == temp) & (runs["size_ratio"] == sr)]
        ax = axes[i][j]

        # Bars: median throughput across runs; Whiskers: 90% percentile interval via bootstrap
        sns.barplot(
            data=d, x="pattern", y="throughput_mibps", hue="madv",
            order=pattern_order, estimator=np.median, errorbar=("pi", 90), capsize=0.2, ax=ax
        )

        # OPTIONAL: show all per-run points on top (so you literally see "all throughput")
        # comment out if you don't want the dots
        sns.stripplot(
            data=d, x="pattern", y="throughput_mibps", hue="madv",
            order=pattern_order, dodge=True, alpha=0.35, size=3, ax=ax, legend=False
        )

        ax.set_title(f"Throughput (MiB/s) — {temp}, size_ratio={sr}")
        ax.set_xlabel("Access Pattern")
        ax.set_ylabel("Throughput (MiB/s)")
        ax.grid(axis="y", linestyle="--", alpha=0.4)

        # keep only one legend (top-right panel)
        if not (i == 0 and j == len(sizes)-1):
            leg = ax.get_legend()
            if leg: leg.remove()

# single legend on the top-right subplot
axes[0][len(sizes)-1].legend(title="MADV", loc="upper right")

plt.tight_layout()
plt.savefig(OUT_DIR / "fig2a_throughput_sr1.0_1.5.png", dpi=200)
plt.close()


# ---------- (2b) MINOR FAULTS: size_ratio in {1.0, 1.5} × {cold, hot} ----------
# ---------- (2b) MINOR FAULTS (use RAW runs, not pre-aggregated) ----------
import numpy as np

sizes = [1.0, 1.5]
temps = ["cold", "hot"]

runs_f = df[df["size_ratio"].isin(sizes)].copy()

runs_f["pattern"] = runs_f["pattern"].apply(short_pattern)

pattern_order = sorted(runs_f["pattern"].unique(), key=pat_key)
runs_f["pattern"] = pd.Categorical(runs_f["pattern"], categories=pattern_order, ordered=True)

# MADV order
madv_order = ["none", "rand", "seq", "willneed", "dontneed", "free"]
runs_f["madv"] = pd.Categorical(
    runs_f["madv"],
    categories=[m for m in madv_order if m in runs_f["madv"].unique()],
    ordered=True
)

fig, axes = plt.subplots(len(temps), len(sizes), figsize=(12, 8), sharex=True, sharey=True)
if len(temps) == 1:
    axes = [axes]

for i, temp in enumerate(temps):
    for j, sr in enumerate(sizes):
        d = runs_f[(runs_f["temp"] == temp) & (runs_f["size_ratio"] == sr)]
        ax = axes[i][j]

        # Bars: median minor faults across runs; Whiskers: 90% percentile interval via bootstrap
        sns.barplot(
            data=d, x="pattern", y="minflt", hue="madv",
            order=pattern_order, estimator=np.median,
            errorbar=("pi", 90), capsize=0.2, ax=ax
        )

        # Optional: overlay per-run points for visibility
        sns.stripplot(
            data=d, x="pattern", y="minflt", hue="madv",
            order=pattern_order, dodge=True, alpha=0.35, size=3, ax=ax, legend=False
        )

        ax.set_title(f"Minor Faults (p50) — {temp}, size_ratio={sr}")
        ax.set_xlabel("Access Pattern")
        ax.set_ylabel("Fault Count")
        ax.grid(axis="y", linestyle="--", alpha=0.4)

        # keep only one legend (top-right subplot)
        if not (i == 0 and j == len(sizes)-1):
            leg = ax.get_legend()
            if leg: leg.remove()

# single legend on the top-right subplot
axes[0][len(sizes)-1].legend(title="MADV", loc="upper right")

plt.tight_layout()
plt.savefig(OUT_DIR / "fig2b_minfaults_sr1.0_1.5.png", dpi=200)
plt.close()


# ---------- (3) COLD vs HOT DELTA ----------
cold = stats[stats.temp=="cold"].rename(columns={"p50_thr":"thr_cold","p50_minflt":"minflt_cold"})
hot  = stats[stats.temp=="hot"].rename(columns={"p50_thr":"thr_hot","p50_minflt":"minflt_hot"})
delta = cold.merge(hot, on=["pattern","size_ratio","stride_pages","madv"])
delta["thr_delta"] = delta["thr_hot"] - delta["thr_cold"]
delta["minflt_delta"] = delta["minflt_cold"] - delta["minflt_hot"]


df_plot = delta[delta["size_ratio"] == 1.0].copy()
uniq_patterns = df_plot["pattern"].dropna().unique().tolist()
stride_vals = sorted(
    int(p.split(":")[1]) for p in uniq_patterns
    if isinstance(p, str) and p.startswith("stride:")
)
order = [p for p in ["seq", "rand"] if p in uniq_patterns] + [f"stride:{s}" for s in stride_vals]

plt.figure(figsize=(8,5))
ax = sns.barplot(
    data=df_plot,
    x="pattern",
    y="thr_delta",
    hue="madv",
    order=order
)
ax.set_title("Throughput Difference (hot - cold, size_ratio=1.0)")
ax.set_ylabel("ΔThroughput (MiB/s)")
ax.set_xlabel("Pattern")
ax.set_xticklabels([lab.replace("stride:", "s") for lab in order])
ax.legend(title="MADV")
plt.tight_layout()
plt.savefig(OUT_DIR / "fig3_coldhot.png", dpi=200)
plt.close()

# ---------- (4) WORKING-SET SWEEP ----------
ws = stats[(stats["pattern"]=="seq") & (stats["temp"]=="cold")]
plt.figure(figsize=(8,5))
sns.lineplot(data=ws, x="size_ratio", y="p50_thr", hue="madv", marker="o")
plt.title("Working-set Sweep (Sequential, Cold)")
plt.ylabel("Throughput (MiB/s, p50)")
plt.xlabel("Size Ratio (fraction of RAM)")
plt.tight_layout()
plt.savefig(OUT_DIR/"fig4_sizesweep.png", dpi=200)
plt.close()

# ---------- (5) STRIDE SENSITIVITY ----------
stride = stats[(stats["temp"]=="cold") & (stats["size_ratio"]==1.0)]
stride = stride[stride["stride_pages"] >= 0]
plt.figure(figsize=(8,5))
sns.lineplot(data=stride, x="stride_pages", y="p50_thr", hue="madv", marker="o")
plt.xscale("log", base=2)
plt.title("Stride Sensitivity (Cold, size_ratio=1.0)")
plt.ylabel("Throughput (MiB/s, p50)")
plt.xlabel("Stride (pages, log2 scale)")
plt.tight_layout()
plt.savefig(OUT_DIR/"fig5_stride.png", dpi=200)
plt.close()

print(f"Saved figures to: {OUT_DIR.resolve()}")
