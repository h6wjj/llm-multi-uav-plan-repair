"""Rebuild Figures 6/7 from frozen formal records; no simulation or source mutation."""
from pathlib import Path
import csv
import hashlib
import json
import shutil
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import t

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "UAV_PAPER/style"))
import paper_style  # noqa: E402,F401

FILES = {"Immediate local": "Simple_local_reassignment.jsonl",
         "CDS-MR": "CDS_MR.jsonl", "Global total-route": "Global_total_route_reallocation.jsonl"}
# Keep archived method keys intact; only publication labels are renamed.
DISPLAY_LABELS = {"Immediate local": "Local", "CDS-MR": "Minimal repair",
                  "Global total-route": "Global"}
FIG7_ONLY = "--fig7-only" in sys.argv
PLOT_ONLY = "--plot-only" in sys.argv
PATH = "executed_suffix_path_length_m"
TIME = "mission_completion_time_seconds"
NPSD = "normalized_plan_stability_distance"
BLOCKS = {"B2": "suffix_grid_three_way_formal_v1", "B3": "b3_independent_task_distribution_formal_v1"}
OUT = ROOT / "图表数据/suffix_results"
OUT.mkdir(parents=True, exist_ok=True)
sources = []
def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def register(p):
    sources.append({"relative_path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)})
def load_block(name):
    folder = ROOT / "实验数据/results" / BLOCKS[name]
    data = {}
    b3 = json.loads((folder / "B3_ANALYSIS.json").read_text(encoding="utf-8")) if name == "B3" else None
    for method, filename in FILES.items():
        p = folder / filename
        if b3:
            assert digest(p) == b3["result_sha256"][method], p
        register(p)
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        assert len(rows) == 90
        data[method] = {r["task_id"]: r["standard_metrics"] for r in rows}
        assert len(data[method]) == 90
        for r in rows:
            m = r["standard_metrics"]
            assert r["runner_status"] == "completed"
            assert m["recovery_success_rate_item"] and m["strict_contract_satisfied"]
            assert m["trajectory_scope"] == "commanded_repair_UAV_only"
    assert all(set(data[m]) == set(data["CDS-MR"]) for m in data)
    register(folder / "formal_metric_audit.json")
    if b3:
        register(folder / "B3_ANALYSIS.json")
    return data
def savefig(fig, number):
    if FIG7_ONLY and number != 7:
        plt.close(fig)
        return
    base = ROOT / f"UAV_PAPER/figures/source/fig{number}"
    base.mkdir(parents=True, exist_ok=True)
    stem = f"Fig{number}_suffix_results"
    for ext in ("pdf", "svg", "png"):
        fig.savefig(base / f"{stem}.{ext}", dpi=300, bbox_inches="tight")
    # The manuscript consumes the exact vector master, not a second layout pass.
    shutil.copyfile(base / f"{stem}.pdf", ROOT / f"paper_applied_intelligence/figures/Fig{number}.pdf")
    plt.close(fig)
def clean(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#E5E5E5", linewidth=.5)
    ax.set_axisbelow(True)
def csv_out(name, rows):
    if PLOT_ONLY:
        return
    with (OUT / name).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

b2, b3 = load_block("B2"), load_block("B3")
# Export full metric table used by every plotted point and manuscript table.
allrows = []
for block, data in (("B2", b2), ("B3", b3)):
    for method, records in data.items():
        for task_id, m in sorted(records.items()):
            allrows.append({"block": block, "method": method, "task_id": task_id,
                            **{k: m[k] for k in (PATH, TIME, NPSD, "estimated_full_suffix_route_cost",
                                                "repair_solver_wallclock_seconds", "repair_decision_latency_seconds")}})
csv_out("formal_metrics.csv", allrows)
colors = ["#0072B2", "#D55E00", "#009E73"]
fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.8), layout="constrained")
cellrows = []
for k, structure in enumerate(("chain", "branch", "merge")):
    for ax, metric in zip(axes, (PATH, TIME)):
        means, errors = [], []
        for coupling in ("none", "weak", "strong"):
            ids = sorted(i for i in b2["CDS-MR"] if f"_{structure}_{coupling}_" in i)
            assert len(ids) == 10
            delta = np.array([b2["Immediate local"][i][metric] - b2["CDS-MR"][i][metric] for i in ids])
            mean = delta.mean()
            err = t.ppf(.975, 9) * delta.std(ddof=1) / np.sqrt(10)
            means.append(mean); errors.append(err)
            cellrows.append(dict(structure=structure, coupling=coupling, metric=metric,
                                 n=10, mean=float(mean), ci_low=float(mean-err), ci_high=float(mean+err)))
        ax.errorbar(np.arange(3)+(k-1)*.045, means, yerr=errors, label=structure.title(),
                    color=colors[k], marker=("o","s","^")[k], linestyle=("-","--",":")[k],
                    capsize=3, linewidth=1.2, markersize=4)
for ax in axes:
    clean(ax)
    ax.axhline(0, color="#777777", linewidth=.6)
    ax.set_xticks(range(3), ["None", "Weak", "Strong"])
    ax.set_xlabel("Prespecified suffix coupling")
axes[0].set_ylabel("Repair-UAV path reduction (m)")
axes[1].set_ylabel("Completion-time reduction (s)")
axes[0].set_title("(a) Executed path", loc="left")
axes[1].set_title("(b) Simulated completion time", loc="left")
axes[0].legend(frameon=False, fontsize=8)
savefig(fig, 6)
csv_out("grid_paired_effects.csv", cellrows)

ids = sorted(b3["CDS-MR"])
x = np.array([b3["Immediate local"][i][PATH] for i in ids])
y = np.array([b3["CDS-MR"][i][PATH] for i in ids])
rel = (x-y)/x
cats = np.where(rel >= .01, "Benefit", np.where(rel <= -.01, "Regression", "Practical tie"))
assert [(cats == c).sum() for c in ("Benefit","Practical tie","Regression")] == [30,59,1]
assert np.isclose((x-y).mean(), .534363, atol=1e-6)
fig, axes = plt.subplots(1, 3, figsize=(7.2, 3.2), layout="constrained",
                         gridspec_kw={"width_ratios":[1,1.1,1]})
palette = {"Benefit":"#009E73", "Practical tie":"#777777", "Regression":"#D55E00"}
for c, marker in zip(palette, ("o",".","X")):
    take = cats == c
    axes[0].scatter(x[take], y[take], c=palette[c], s=23 if c!="Practical tie" else 15,
                    marker=marker, alpha=.8, label=c)
lo, hi = min(x.min(),y.min())-.3, max(x.max(),y.max())+.3
axes[0].plot([lo,hi],[lo,hi], "--", color="#999999", linewidth=.8)
axes[0].set(xlim=(lo,hi), ylim=(lo,hi), xlabel="Immediate local path (m)", ylabel="Minimal-repair path (m)")
axes[0].set_aspect("equal", adjustable="box")
counts = []
for structure in ("chain", "branch", "merge"):
    take = np.array([f"_{structure}_" in i for i in ids])
    counts.append([(cats[take]==c).sum() for c in palette])
counts = np.asarray(counts)
left = np.zeros(3)
for j, (c, color) in enumerate(palette.items()):
    axes[1].barh(range(3), counts[:,j], left=left, color=color, height=.58,
                 label=c, hatch=("", "//", "xx")[j], edgecolor="white", linewidth=.5)
    for row, value in enumerate(counts[:,j]):
        if value:
            if value == 1:
                axes[1].annotate("1", (left[row]+value/2, row), xytext=(0, 14),
                                textcoords="offset points", ha="center", fontsize=8,
                                arrowprops={"arrowstyle":"-", "color":"#555555", "lw":.6})
            else:
                axes[1].text(left[row]+value/2, row, str(value), ha="center", va="center",
                             color="white", fontsize=8)
    left += counts[:,j]
axes[1].set_yticks(range(3), ["Chain","Branch","Merge"])
axes[1].set(xlim=(0,30), xlabel="Cases (30 per structure)")
axes[1].invert_yaxis()
axes[1].legend(frameon=False, loc="upper center", bbox_to_anchor=(.5,-.21), fontsize=7, ncol=1)
for method, color, marker, dy in zip(FILES, ("#777777","#009E73","#0072B2"), ("s","o","^"), (9,-16,9)):
    m = b3[method]
    px = np.mean([v[NPSD] for v in m.values()])
    py = np.mean([v[PATH] for v in m.values()])
    axes[2].scatter(px, py, color=color, marker=marker, s=40)
    label = DISPLAY_LABELS[method]
    offset = (4, dy) if method == "CDS-MR" else (0, dy)
    alignment = "left" if method == "CDS-MR" else "center"
    axes[2].annotate(label, (px,py), xytext=offset, textcoords="offset points", ha=alignment, fontsize=8)
axes[2].set(xlim=(.29,.56), ylim=(4.85,6.4), xlabel="Mean NPSD", ylabel="Mean repair-UAV path (m)")
for ax, title in zip(axes, ("(a) Paired paths", "(b) Outcome counts", "(c) Plan / path tradeoff")):
    clean(ax)
    ax.set_title(title, loc="left", fontsize=9)
savefig(fig, 7)
csv_out("random_paired_outcomes.csv", [dict(task_id=i,local_path=float(a),cds_path=float(b),
        relative_reduction=float(r),category=str(c)) for i,a,b,r,c in zip(ids,x,y,rel,cats)])
generated = []
for p in sorted(OUT.glob("*.csv")):
    generated.append({"relative_path":p.relative_to(ROOT).as_posix(), "sha256":digest(p)})
register(Path(__file__))
report = {"sources":sources,"generated":generated, "checks":{"tasks_per_block":90,
          "strict_pass_per_method_per_block":90, "b3_categories":[30,59,1]},
          "scope":"repair-UAV path only; B2 designed grid and B3 random geometry reported separately"}
report["publication_labels"] = DISPLAY_LABELS
report["figure_outputs"] = [
    {"relative_path": p.relative_to(ROOT).as_posix(), "sha256": digest(p)}
    for number in ((7,) if FIG7_ONLY else (6, 7))
    for p in (
        ROOT / f"paper_applied_intelligence/figures/Fig{number}.pdf",
        *(ROOT / f"UAV_PAPER/figures/source/fig{number}/Fig{number}_suffix_results.{ext}"
          for ext in ("pdf", "svg", "png")),
    )
]
(OUT/"figure_provenance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(report["checks"]))
