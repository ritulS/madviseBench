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

# ---------- (1) LATENCY p50/p99 grouped by MADV ----------
lat = (
    df.groupby(["pattern","temp","size_ratio","stride_pages","madv"])
      .agg(p50_time=("time_s","median"),
           p99_time=("time_s", p99))
      .reset_index()
)
lat1 = lat[lat["size_ratio"] == 1.0].copy()

def short_pattern(p):
    if isinstance(p, str) and p.startswith("stride:"):
        return f"s{p.split(':',1)[1]}"   # stride:2 -> s2
    return str(p)

lat1["pattern"] = lat1["pattern"].apply(short_pattern)

def pat_key(x: str):
    x = str(x)
    if x == "rand": return (0, 0)
    if x == "seq":  return (1, 0)
    if x.startswith("s") and x[1:].isdigit():
        return (2, int(x[1:]))   # s1, s2, s4, ...
    return (3, 0)

pattern_order = sorted(lat1["pattern"].unique(), key=pat_key)
lat1["pattern"] = pd.Categorical(lat1["pattern"], categories=pattern_order, ordered=True)

fig, axes = plt.subplots(1, 2, figsize=(12,5), sharey=False)

sns.barplot(data=lat1, x="pattern", y="p50_time", hue="madv",
            ax=axes[0], capsize=0.2, order=pattern_order)
axes[0].set_title("Latency p50 by MADV (size_ratio=1.0)")
axes[0].set_xlabel("Access Pattern")
axes[0].set_ylabel("Time (s)")
axes[0].legend_.remove()

sns.barplot(data=lat1, x="pattern", y="p99_time", hue="madv",
            ax=axes[1], capsize=0.2, order=pattern_order)
axes[1].set_title("Latency p99 by MADV (size_ratio=1.0)")
axes[1].set_xlabel("Access Pattern")
axes[1].set_ylabel("Time (s)")
axes[1].set_yscale("log")
axes[1].legend(title="MADV", loc="upper left")

plt.tight_layout()
plt.savefig(OUT_DIR / "fig1_latency_p50_p99_grouped.png", dpi=200)
plt.close()

# ---------- (2) THROUGHPUT + MINOR FAULTS (cold & hot, grouped by MADV, s-labels) ----------

# ---------- (2a) THROUGHPUT: size_ratio in {1.0, 1.5} × {cold, hot} ----------
sizes = [1.0, 1.5]
temps = ["cold", "hot"]

subset = stats[stats["size_ratio"].isin(sizes)].copy()

# shorten stride labels
def short_pattern(p):
    if isinstance(p, str) and p.startswith("stride:"):
        return f"s{p.split(':',1)[1]}"
    return str(p)

subset["pattern"] = subset["pattern"].apply(short_pattern)

# robust ordering: rand, seq, then s1, s2, s4, ...
def pat_key(x: str):
    x = str(x)
    if x == "rand": return (0, 0)
    if x == "seq":  return (1, 0)
    if x.startswith("s") and x[1:].isdigit():
        return (2, int(x[1:]))
    return (3, 0)

pattern_order = sorted(subset["pattern"].unique(), key=pat_key)
subset["pattern"] = pd.Categorical(subset["pattern"], categories=pattern_order, ordered=True)

fig, axes = plt.subplots(len(temps), len(sizes), figsize=(12, 8), sharex=True, sharey=True)
if len(temps) == 1: axes = [axes]  # normalize shape for 1 row

for i, temp in enumerate(temps):
    for j, sr in enumerate(sizes):
        data_ij = subset[(subset["temp"] == temp) & (subset["size_ratio"] == sr)]
        ax = axes[i][j]
        sns.barplot(data=data_ij, x="pattern", y="p50_thr", hue="madv",
                    ax=ax, capsize=0.2, order=pattern_order)
        ax.set_title(f"Throughput (MiB/s) — {temp}, size_ratio={sr}")
        ax.set_xlabel("Access Pattern")
        ax.set_ylabel("Throughput (MiB/s)")
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        if not (i == 0 and j == len(sizes)-1):
            # only keep one legend (top-right panel)
            leg = ax.get_legend()
            if leg: leg.remove()

# single legend on top-right subplot
axes[0][len(sizes)-1].legend(title="MADV", loc="upper right")

plt.tight_layout()
plt.savefig(OUT_DIR / "fig2a_throughput_sr1.0_1.5.png", dpi=200)
plt.close()

# ---------- (2b) MINOR FAULTS: size_ratio in {1.0, 1.5} × {cold, hot} ----------
sizes = [1.0, 1.5]
temps = ["cold", "hot"]

subset_f = stats[stats["size_ratio"].isin(sizes)].copy()
subset_f["pattern"] = subset_f["pattern"].apply(short_pattern)
subset_f["pattern"] = pd.Categorical(subset_f["pattern"], categories=pattern_order, ordered=True)

fig, axes = plt.subplots(len(temps), len(sizes), figsize=(12, 8), sharex=True, sharey=True)
if len(temps) == 1: axes = [axes]

for i, temp in enumerate(temps):
    for j, sr in enumerate(sizes):
        data_ij = subset_f[(subset_f["temp"] == temp) & (subset_f["size_ratio"] == sr)]
        ax = axes[i][j]
        sns.barplot(data=data_ij, x="pattern", y="p50_minflt", hue="madv",
                    ax=ax, capsize=0.2, order=pattern_order)
        ax.set_title(f"Minor Faults (p50) — {temp}, size_ratio={sr}")
        ax.set_xlabel("Access Pattern")
        ax.set_ylabel("Fault Count")
        ax.grid(axis="y", linestyle="--", alpha=0.4)
        if not (i == 0 and j == len(sizes)-1):
            leg = ax.get_legend()
            if leg: leg.remove()

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
