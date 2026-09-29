# /// script
# requires-python = ">=3.10"
# dependencies = ["matplotlib==3.11.2", "numpy>=2"]
# ///
"""Generate chart/table/screenshot images whose answers are computed from the data.

Usage: uv run generate_items.py <out-dir>
Writes <out-dir>/<item>.png and <out-dir>/items.json ({id, image, question, answer}).
Every question has one short exact answer and a margin that keeps it unambiguous.
"""
from __future__ import annotations

import json
import random
import string
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SEED = 280926
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def distinct_ints(rng: random.Random, n: int, lo: int, hi: int, gap: int) -> list[int]:
    """n integers in [lo, hi] whose sorted neighbours differ by at least gap."""
    while True:
        values = [rng.randint(lo, hi) for _ in range(n)]
        ordered = sorted(values)
        if all(b - a >= gap for a, b in zip(ordered, ordered[1:])):
            return values


def save(fig, out: Path, name: str) -> str:
    fig.savefig(out / f"{name}.png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return f"{name}.png"


def bar_max(rng, out):
    cats = ["Aster", "Birch", "Cedar", "Dahlia", "Elm", "Fern", "Gorse", "Holly", "Iris"]
    vals = distinct_ints(rng, len(cats), 12, 88, 4)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.bar(cats, vals, color="#4c72b0")
    ax.grid(axis="y", alpha=0.4)
    ax.set_title("Units shipped by site")
    second = sorted(zip(vals, cats))[-2][1]
    return save(fig, out, "bar-second"), "Which site has the second-highest bar?", second


def bar_threshold(rng, out):
    cats = [f"T{i}" for i in range(1, 13)]
    vals = [v for v in (rng.choice([rng.randint(15, 45), rng.randint(56, 90)]) for _ in cats)]
    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.bar(cats, vals, color="#dd8452")
    ax.axhline(50, ls="--", color="black")
    ax.set_title("Latency p95 (ms) per test")
    return save(fig, out, "bar-threshold"), "How many bars rise above the dashed line?", str(sum(v > 50 for v in vals))


def line_cross(rng, out):
    k = rng.randint(4, 9)
    cost = np.linspace(60, 75, 12) + np.array([rng.uniform(-1, 1) for _ in range(12)])
    rev = np.array([cost[i] - rng.uniform(4, 10) if i < k else cost[i] + rng.uniform(4, 10) for i in range(12)])
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.plot(MONTHS, cost, marker="o", label="Cost")
    ax.plot(MONTHS, rev, marker="s", label="Revenue")
    ax.legend()
    ax.grid(alpha=0.3)
    return save(fig, out, "line-cross"), "In which month does Revenue first exceed Cost? Give the month abbreviation.", MONTHS[k]


def line_peak(rng, out):
    names = ["North", "South", "East", "West"]
    month = rng.randint(3, 9)
    lead = rng.choice(names)
    fig, ax = plt.subplots(figsize=(7, 3.5))
    for name in names:
        series = [rng.uniform(20, 60) for _ in MONTHS]
        series[month] = 80 if name == lead else rng.uniform(20, 55)
        ax.plot(MONTHS, series, marker=".", label=name)
    ax.legend(ncol=4, fontsize=8)
    ax.grid(alpha=0.3)
    return (save(fig, out, "line-peak"), f"Which region has the highest value in {MONTHS[month]}?", lead)


def table_items(rng, out):
    rows = [f"R{i}" for i in range(1, 13)]
    cols = ["Q1", "Q2", "Q3", "Q4", "H1", "H2"]
    data = [[rng.randint(100, 999) for _ in cols] for _ in rows]
    col = rng.randrange(len(cols))
    col_vals = distinct_ints(rng, len(rows), 100, 999, 7)
    for i, v in enumerate(col_vals):
        data[i][col] = v
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.axis("off")
    table = ax.table(cellText=[[str(v) for v in r] for r in data], rowLabels=rows, colLabels=cols, loc="center")
    table.set_fontsize(8)
    image = save(fig, out, "table")
    r, c = rng.randrange(len(rows)), rng.randrange(len(cols))
    best = rows[max(range(len(rows)), key=lambda i: data[i][col])]
    return [
        (image, f"What is the value in row {rows[r]}, column {cols[c]}?", str(data[r][c])),
        (image, f"Which row has the largest value in column {cols[col]}?", best),
    ]


def scatter_count(rng, out):
    n_red = rng.randint(5, 11)
    fig, ax = plt.subplots(figsize=(5, 4))
    pts = []
    while len(pts) < 45:
        p = (rng.uniform(0, 10), rng.uniform(0, 10))
        if all((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 > 0.5 for q in pts):
            pts.append(p)
    xs, ys = zip(*pts)
    ax.scatter(xs[n_red:], ys[n_red:], c="#4c72b0", s=30)
    ax.scatter(xs[:n_red], ys[:n_red], c="#d62728", s=30)
    return save(fig, out, "scatter-red"), "How many red points are there?", str(n_red)


def pie_second(rng, out):
    labels = ["Search", "Email", "Social", "Direct", "Referral", "Ads"]
    vals = distinct_ints(rng, len(labels), 5, 40, 3)
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.pie(vals, startangle=rng.randint(0, 359))
    ax.legend(labels, loc="center left", bbox_to_anchor=(1, 0.5))
    second = sorted(zip(vals, labels))[-2][1]
    return save(fig, out, "pie-second"), "Which channel has the second-largest slice?", second


def stacked_year(rng, out):
    years = [str(y) for y in range(2019, 2027)]
    segs = ["Cloud", "Licences", "Services"]
    cloud = distinct_ints(rng, len(years), 10, 60, 4)
    data = {"Cloud": cloud, "Licences": [rng.randint(10, 40) for _ in years], "Services": [rng.randint(5, 30) for _ in years]}
    fig, ax = plt.subplots(figsize=(7, 3.5))
    bottom = np.zeros(len(years))
    for s in segs:
        ax.bar(years, data[s], bottom=bottom, label=s)
        bottom += np.array(data[s])
    ax.legend()
    best = years[max(range(len(years)), key=lambda i: cloud[i])]
    return save(fig, out, "stacked"), "In which year is the Cloud segment tallest?", best


def flow(rng, out):
    names = ["Ingest", "Validate", "Enrich", "Score", "Route", "Archive"]
    order = names[:1] + rng.sample(names[1:], len(names) - 1)
    pos = {n: (i % 3 * 3.0, -(i // 3) * 2.0) for i, n in enumerate(rng.sample(names, len(names)))}
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.axis("off")
    for n, (x, y) in pos.items():
        ax.text(x, y, n, ha="center", va="center", bbox={"boxstyle": "round", "fc": "#eef"})
    for a, b in zip(order, order[1:]):
        ax.annotate("", xy=pos[b], xytext=pos[a], arrowprops={"arrowstyle": "->", "shrinkA": 22, "shrinkB": 22})
    ax.set_xlim(-1.5, 7.5)
    ax.set_ylim(-3, 1)
    src = rng.choice(order[:-1])
    return save(fig, out, "flow"), f"Which box does the arrow from {src} point to?", order[order.index(src) + 1]


def log_text(rng, out):
    code = f"E{rng.randint(1000, 9999)}"
    err_line = rng.randint(8, 26)
    lines = []
    for i in range(1, 31):
        if i == err_line:
            body = f"ERROR worker-{rng.randint(1, 9)} code={code} upstream timeout"
        else:
            body = f"INFO  worker-{rng.randint(1, 9)} processed batch {rng.randint(1000, 9999)} in {rng.randint(10, 99)}ms"
        lines.append(f"{i:>3}  {body}")
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.axis("off")
    ax.text(0, 1, "\n".join(lines), family="monospace", fontsize=7, va="top")
    return [
        (save(fig, out, "log"), "What is the error code on the ERROR line?", code),
        ("log.png", "On which line number is the ERROR entry?", str(err_line)),
    ]


def dual_axis(rng, out):
    unit = rng.choice(["kPa", "mmHg", "psi", "bar"])
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.plot(range(10), [rng.uniform(10, 30) for _ in range(10)], color="#4c72b0")
    ax.set_ylabel("Temperature (°C)")
    ax2 = ax.twinx()
    ax2.plot(range(10), [rng.uniform(1, 3) for _ in range(10)], color="#c44e52")
    ax2.set_ylabel(f"Pressure ({unit})")
    return save(fig, out, "dual-axis"), "What unit is on the right-hand y-axis?", unit


def heatmap(rng, out):
    grid = np.array([[rng.uniform(0, 0.7) for _ in range(6)] for _ in range(6)])
    r, c = rng.randrange(6), rng.randrange(6)
    grid[r][c] = 1.0
    fig, ax = plt.subplots(figsize=(5, 4))
    im = ax.imshow(grid, cmap="viridis")
    ax.set_xticks(range(6), [f"C{i}" for i in range(1, 7)])
    ax.set_yticks(range(6), [f"R{i}" for i in range(1, 7)])
    fig.colorbar(im)
    return (save(fig, out, "heatmap"), "Which cell has the highest value? Answer as row,column labels, e.g. R1,C1.",
            f"R{r + 1},C{c + 1}")


def form(rng, out):
    invoice = "".join(rng.choice(string.ascii_uppercase) for _ in range(2)) + "-" + str(rng.randint(10000, 99999))
    fields = [("Customer", "Harbour & Finch Ltd"), ("Invoice #", invoice), ("Due", f"2026-{rng.randint(10, 12)}-{rng.randint(10, 28)}"),
              ("PO", str(rng.randint(100000, 999999))), ("Terms", "Net 30")]
    fig, ax = plt.subplots(figsize=(6, 3))
    ax.axis("off")
    for i, (k, v) in enumerate(fields):
        ax.text(0.02, 0.9 - i * 0.18, k, fontsize=9, color="#555")
        ax.text(0.35, 0.9 - i * 0.18, v, fontsize=9, bbox={"fc": "#f4f4f4", "ec": "#bbb"})
    return save(fig, out, "form"), "What is the Invoice # value?", invoice


def histogram(rng, out):
    peak = rng.randint(1, 7)
    edges = list(range(0, 90, 10))
    counts = [rng.randint(3, 12) for _ in range(8)]
    counts[peak] = 20
    fig, ax = plt.subplots(figsize=(7, 3.5))
    ax.bar([f"{a}-{a + 10}" for a in edges[:-1]], counts, width=1.0, edgecolor="black")
    ax.set_xlabel("Response time (ms)")
    return save(fig, out, "histogram"), "Which bin has the most values? Give its label, e.g. 0-10.", f"{edges[peak]}-{edges[peak] + 10}"


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    items = []
    for fn in (bar_max, bar_threshold, line_cross, line_peak, table_items, scatter_count, pie_second,
               stacked_year, flow, log_text, dual_axis, heatmap, form, histogram):
        produced = fn(rng, out)
        for image, question, answer in produced if isinstance(produced, list) else [produced]:
            items.append({"id": f"{Path(image).stem}-{len(items) + 1:02d}", "image": image, "question": question, "answer": answer})
    (out / "items.json").write_text(json.dumps(items, indent=2) + "\n")
    print(f"{len(items)} items -> {out}")


if __name__ == "__main__":
    main()
