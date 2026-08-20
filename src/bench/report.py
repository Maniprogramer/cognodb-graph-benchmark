"""Results rendering: markdown tables plus charts.

Palette is the validated five-slot categorical set (blue, orange, aqua,
yellow, magenta). Three of those sit below 3:1 contrast on a light surface, so
the relief rule applies: every chart carries direct value labels, and the full
numbers are also present as markdown tables. Colour identifies a platform and
never encodes rank, so a platform keeps its hue across every chart.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

# Force the non-interactive backend before pyplot is imported anywhere.
# matplotlib defaults to the "macosx" GUI backend on a Mac, which can block
# when the benchmark runs headless or as a background job.
matplotlib.use("Agg")

# Validated adjacent-pair palette. Assigned in fixed order, never cycled.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e3e2de"

HOPS = ["1-hop", "2-hop", "3-hop"]
LOOKUPS = ["point-lookup", "filtered-lookup", "aggregation"]


def _fmt(value, digits: int = 2, dash: str = "—") -> str:
    if value is None:
        return dash
    if isinstance(value, str):
        return value
    if isinstance(value, float) and math.isnan(value):
        return dash
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return f"{value:,}"


def _completed(results: dict) -> list[dict]:
    return [p for p in results["platforms"] if not p.get("skipped") and p.get("workloads")]


def _colors(platforms: list[dict]) -> dict[str, str]:
    return {p["id"]: SERIES[i % len(SERIES)] for i, p in enumerate(platforms)}


# --------------------------------------------------------------------- charts


def _style_axes(ax):
    ax.set_facecolor(SURFACE)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
        ax.spines[side].set_linewidth(1)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.grid(axis="y", color=GRID, linewidth=1, alpha=0.9)
    ax.set_axisbelow(True)


def _save(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    import matplotlib.pyplot as plt

    plt.close(fig)


def chart_traversal(results: dict, out_dir: Path, percentile: str = "p50") -> Path | None:
    import matplotlib.pyplot as plt
    import numpy as np

    platforms = _completed(results)
    if not platforms:
        return None
    colors = _colors(platforms)

    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=SURFACE)
    n = len(platforms)
    # 2px surface gap between adjacent bars at 160 dpi.
    group_width = 0.8
    bar_width = group_width / n * 0.88
    x = np.arange(len(HOPS))

    for i, p in enumerate(platforms):
        values = [p["workloads"].get(h, {}).get(percentile, float("nan")) for h in HOPS]
        offset = (i - (n - 1) / 2) * (group_width / n)
        bars = ax.bar(
            x + offset, values, bar_width, label=p["name"],
            color=colors[p["id"]], edgecolor=SURFACE, linewidth=1.5, zorder=3,
        )
        for bar, value in zip(bars, values):
            if value == value:  # not NaN
                ax.annotate(
                    f"{value:,.0f}" if value >= 10 else f"{value:,.1f}",
                    (bar.get_x() + bar.get_width() / 2, value),
                    textcoords="offset points", xytext=(0, 3),
                    ha="center", fontsize=7, color=INK_MUTED, rotation=90, zorder=4,
                )

    _style_axes(ax)
    ax.set_yscale("log")
    ax.set_xticks(x, HOPS)
    ax.set_ylabel(f"{percentile} latency (ms, log scale)", color=INK_MUTED, fontsize=9)
    ax.set_title(
        f"Traversal {percentile} latency by hop depth",
        color=INK, fontsize=12, fontweight="600", loc="left", pad=12,
    )
    legend = ax.legend(frameon=False, fontsize=9, ncol=min(n, 5), loc="upper left",
                       bbox_to_anchor=(0, -0.12))
    for text in legend.get_texts():
        text.set_color(INK_MUTED)

    path = out_dir / f"traversal_{percentile}.png"
    _save(fig, path)
    return path


def chart_ingest(results: dict, out_dir: Path) -> Path | None:
    import matplotlib.pyplot as plt

    platforms = [p for p in _completed(results) if p.get("load")]
    if not platforms:
        return None
    colors = _colors(_completed(results))

    ordered = sorted(platforms, key=lambda p: p["load"]["relationships_per_second"])
    names = [p["name"] for p in ordered]
    values = [p["load"]["relationships_per_second"] for p in ordered]

    fig, ax = plt.subplots(figsize=(8, 0.55 * len(ordered) + 2), facecolor=SURFACE)
    bars = ax.barh(names, values, height=0.62,
                   color=[colors[p["id"]] for p in ordered],
                   edgecolor=SURFACE, linewidth=1.5, zorder=3)
    for bar, value in zip(bars, values):
        ax.annotate(f"{value:,.0f}", (value, bar.get_y() + bar.get_height() / 2),
                    textcoords="offset points", xytext=(6, 0),
                    va="center", fontsize=9, color=INK_MUTED, zorder=4)

    _style_axes(ax)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=GRID, linewidth=1)
    ax.set_xlabel("relationships loaded per second", color=INK_MUTED, fontsize=9)
    ax.set_title("Ingest throughput", color=INK, fontsize=12, fontweight="600",
                 loc="left", pad=12)
    ax.set_xlim(0, max(values) * 1.18)

    path = out_dir / "ingest_throughput.png"
    _save(fig, path)
    return path


def chart_concurrency(results: dict, out_dir: Path) -> Path | None:
    import matplotlib.pyplot as plt

    platforms = [p for p in _completed(results) if p.get("concurrency")]
    if not platforms:
        return None
    colors = _colors(_completed(results))

    fig, ax = plt.subplots(figsize=(8, 4.6), facecolor=SURFACE)
    for p in platforms:
        levels = [c["clients"] for c in p["concurrency"]]
        qps = [c["throughput_qps"] for c in p["concurrency"]]
        ax.plot(levels, qps, marker="o", markersize=8, linewidth=2,
                color=colors[p["id"]], label=p["name"],
                markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=3)
        # Label only the endpoint, not every point.
        if levels:
            ax.annotate(f"{qps[-1]:,.0f}", (levels[-1], qps[-1]),
                        textcoords="offset points", xytext=(8, 0),
                        fontsize=8, color=INK_MUTED, va="center", zorder=4)

    _style_axes(ax)
    ax.set_xlabel("concurrent clients", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("sustained queries / second", color=INK_MUTED, fontsize=9)
    ax.set_title("Mixed-workload scaling (80% read / 20% write)",
                 color=INK, fontsize=12, fontweight="600", loc="left", pad=12)
    levels_all = sorted({c["clients"] for p in platforms for c in p["concurrency"]})
    ax.set_xticks(levels_all)
    legend = ax.legend(frameon=False, fontsize=9, ncol=min(len(platforms), 5),
                       loc="upper left", bbox_to_anchor=(0, -0.14))
    for text in legend.get_texts():
        text.set_color(INK_MUTED)

    path = out_dir / "concurrency_scaling.png"
    _save(fig, path)
    return path


def chart_lookups(results: dict, out_dir: Path) -> Path | None:
    import matplotlib.pyplot as plt
    import numpy as np

    platforms = _completed(results)
    if not platforms:
        return None
    colors = _colors(platforms)

    fig, ax = plt.subplots(figsize=(9, 4.6), facecolor=SURFACE)
    n = len(platforms)
    group_width, x = 0.8, np.arange(len(LOOKUPS))
    bar_width = group_width / n * 0.88

    for i, p in enumerate(platforms):
        values = [p["workloads"].get(w, {}).get("p50", float("nan")) for w in LOOKUPS]
        offset = (i - (n - 1) / 2) * (group_width / n)
        bars = ax.bar(x + offset, values, bar_width, label=p["name"],
                      color=colors[p["id"]], edgecolor=SURFACE, linewidth=1.5, zorder=3)
        for bar, value in zip(bars, values):
            if value == value:
                ax.annotate(f"{value:,.1f}", (bar.get_x() + bar.get_width() / 2, value),
                            textcoords="offset points", xytext=(0, 3), ha="center",
                            fontsize=7, color=INK_MUTED, rotation=90, zorder=4)

    _style_axes(ax)
    ax.set_yscale("log")
    ax.set_xticks(x, ["point lookup", "filtered lookup", "aggregation"])
    ax.set_ylabel("p50 latency (ms, log scale)", color=INK_MUTED, fontsize=9)
    ax.set_title("Lookup and aggregation p50 latency", color=INK, fontsize=12,
                 fontweight="600", loc="left", pad=12)
    legend = ax.legend(frameon=False, fontsize=9, ncol=min(n, 5), loc="upper left",
                       bbox_to_anchor=(0, -0.12))
    for text in legend.get_texts():
        text.set_color(INK_MUTED)

    path = out_dir / "lookups_p50.png"
    _save(fig, path)
    return path


# --------------------------------------------------------------------- tables


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_No data._\n"
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(out) + "\n"


def table_platforms(results: dict) -> str:
    rows = []
    for p in results["platforms"]:
        spec = p.get("spec") or {}
        status = p.get("skipped") or ("completed" if p.get("workloads") else "failed")
        rows.append([
            p["name"], p.get("query_language", "—"), spec.get("tier", "—"),
            spec.get("vcpu", "—"), spec.get("ram", "—"), spec.get("storage", "—"), status,
        ])
    return _table(
        ["Platform", "Query language", "Tier", "vCPU", "RAM", "Storage", "Status"], rows
    )


def table_ingest(results: dict) -> str:
    rows = []
    for p in _completed(results):
        load = p.get("load")
        if not load:
            continue
        rows.append([
            p["name"], _fmt(load["nodes_per_second"], 0), _fmt(load["relationships_per_second"], 0),
            _fmt(load["total_seconds"], 1), load["method"],
        ])
    return _table(
        ["Platform", "Nodes/s", "Rels/s", "Total load (s)", "Load method"], rows
    )


def table_traversal(results: dict) -> str:
    rows = []
    for p in _completed(results):
        row = [p["name"]]
        for hop in HOPS:
            w = p["workloads"].get(hop, {})
            row += [_fmt(w.get("p50")), _fmt(w.get("p95"))]
        rows.append(row)
    headers = ["Platform"] + [f"{h} {m}" for h in HOPS for m in ("p50", "p95")]
    return _table(headers, rows)


def table_lookups(results: dict) -> str:
    rows = []
    for p in _completed(results):
        row = [p["name"]]
        for w_name in LOOKUPS:
            w = p["workloads"].get(w_name, {})
            row += [_fmt(w.get("p50")), _fmt(w.get("p95"))]
        row.append(", ".join(p.get("indexes", [])) or "—")
        rows.append(row)
    headers = (
        ["Platform"]
        + [f"{n} {m}" for n in ("point", "filtered", "aggregation") for m in ("p50", "p95")]
        + ["Indexed properties"]
    )
    return _table(headers, rows)


def table_concurrency(results: dict) -> str:
    rows = []
    for p in _completed(results):
        for c in p.get("concurrency", []):
            rows.append([
                p["name"], c["clients"], _fmt(c["throughput_qps"], 1),
                _fmt(c["p50_ms"]), _fmt(c["p95_ms"]), _fmt(c["p99_ms"]),
                c["reads"], c["writes"], c["errors"],
            ])
    return _table(
        ["Platform", "Clients", "QPS", "p50 ms", "p95 ms", "p99 ms",
         "Reads", "Writes", "Errors"],
        rows,
    )


def table_footprint(results: dict) -> str:
    rows = []
    for p in _completed(results):
        observable = (p.get("spec") or {}).get("observable") or {}
        if "status" in observable:
            detail = observable["status"]
        else:
            detail = ", ".join(f"{k}={_fmt(v, 0)}" for k, v in observable.items())
        verification = p.get("verification") or {}
        rows.append([
            p["name"],
            _fmt(verification.get("nodes_actual")),
            _fmt(verification.get("relationships_actual")),
            "yes" if verification.get("complete") else "**NO**",
            detail or "not observable",
        ])
    return _table(
        ["Platform", "Nodes stored", "Rels stored", "Load complete", "Observable footprint"],
        rows,
    )


def table_baseline(results: dict) -> str:
    rows = []
    for p in _completed(results):
        rtt = p.get("baseline_rtt") or {}
        if "error" in rtt:
            rows.append([p["name"], rtt["error"], "—", "—", "—"])
            continue
        one_hop = p["workloads"].get("1-hop", {}).get("p50")
        floor = rtt.get("p50")
        if not (floor and one_hop and one_hop > 0):
            share = "—"
        elif floor >= one_hop:
            # The workload is not distinguishable from an empty query: its cost
            # is at or below the transport floor, so no useful share exists.
            share = "at floor"
        else:
            share = f"{100 * floor / one_hop:.0f}%"
        rows.append([
            p["name"], _fmt(floor), _fmt(rtt.get("p95")), _fmt(one_hop), share,
        ])
    return _table(
        ["Platform", "Transport p50 (ms)", "Transport p95 (ms)",
         "1-hop p50 (ms)", "Share of 1-hop that is transport"],
        rows,
    )


def table_cold_start(results: dict) -> str:
    rows = []
    for p in _completed(results):
        cold, warm = p.get("cold_start_ms", {}), p.get("workloads", {})
        for workload in ("1-hop", "3-hop", "aggregation"):
            rows.append([
                p["name"], workload, _fmt(cold.get(workload)),
                _fmt(warm.get(workload, {}).get("p50")),
            ])
    return _table(["Platform", "Workload", "Cold first-touch ms", "Warm p50 ms"], rows)


def table_variance(results: dict) -> str:
    rows = []
    for p in _completed(results):
        for workload, v in (p.get("variance") or {}).items():
            rows.append([
                p["name"], workload, v["runs"],
                ", ".join(_fmt(x) for x in v["p50_values"]),
                _fmt(v["p50_spread"]), f"{_fmt(v['p50_spread_pct'], 1)}%",
            ])
    return _table(
        ["Platform", "Workload", "Runs", "p50 per run (ms)", "Spread (ms)", "Spread %"], rows
    )


def table_parity(results: dict) -> str:
    parity = results.get("parity_check", {})
    workloads = parity.get("workloads", {})
    if not workloads:
        return f"_Parity not checkable: {parity.get('reason', 'unknown')}._\n"
    rows = []
    for workload, detail in workloads.items():
        values = detail["values"]
        rows.append([
            workload,
            "yes" if detail["agree"] else "**NO**",
            ", ".join(f"{name}={_fmt(v, 0)}" for name, v in values.items()),
        ])
    return _table(["Workload", "All platforms agree", "Summed result per platform"], rows)


# --------------------------------------------------------------------- writer


def write_report(results: dict, results_dir: Path) -> Path:
    results_dir = Path(results_dir)
    charts_dir = results_dir / "charts"

    charts = {
        "traversal_p50": chart_traversal(results, charts_dir, "p50"),
        "traversal_p95": chart_traversal(results, charts_dir, "p95"),
        "ingest": chart_ingest(results, charts_dir),
        "concurrency": chart_concurrency(results, charts_dir),
        "lookups": chart_lookups(results, charts_dir),
    }

    def embed(key: str, alt: str) -> str:
        path = charts.get(key)
        if not path:
            return ""
        return f"\n![{alt}](charts/{Path(path).name})\n"

    env = results["environment"]
    ds = results["dataset"]
    settings = results["settings"]

    sections = [
        f"# Benchmark results\n",
        f"Run `{results['run_id']}` · generated from `results.json`.\n",
        "## Environment\n",
        _table(
            ["Field", "Value"],
            [
                ["Client machine", env["platform"]],
                ["Client CPUs", env["cpu_count"]],
                ["Python", env["python"]],
                ["Run started (UTC)", env["timestamp_utc"]],
                ["Iterations per read workload", settings["iterations"]],
                ["Warm-up iterations", settings["warmup_iterations"]],
                ["Read-suite repeats", settings["repeats"]],
                ["Load batch size", settings["batch_size"]],
                ["Concurrency levels", settings["concurrency_levels"] or "skipped"],
                ["Mixed-workload read ratio", settings["read_ratio"]],
            ],
        ),
        "## Dataset\n",
        _table(
            ["Field", "Value"],
            [
                ["Source", f"[{ds['name']}]({ds['source_url']})"],
                ["Nodes", _fmt(ds["node_count"])],
                ["Relationships", _fmt(ds["relationship_count"])],
                ["Sampled", ds["sampled"]],
                ["Nodes with a known year", f"{_fmt(ds['nodes_with_known_year'])} ({ds['year_coverage_pct']}%)"],
                ["nodes.csv SHA-256", f"`{ds['nodes_csv_sha256'][:16]}…`"],
                ["edges.csv SHA-256", f"`{ds['edges_csv_sha256'][:16]}…`"],
            ],
        ),
        "## Platforms and tier parity\n",
        table_platforms(results),
        "## Result parity check\n",
        "Every read workload records the value it returned. If two platforms "
        "disagree, the queries are not equivalent and the latency comparison "
        "below is invalid.\n",
        table_parity(results),
        "## Transport baseline\n",
        table_baseline(results),
        "## Ingest throughput\n",
        table_ingest(results),
        embed("ingest", "Ingest throughput by platform"),
        "## Traversal latency\n",
        table_traversal(results),
        embed("traversal_p50", "Traversal p50 latency by hop depth"),
        embed("traversal_p95", "Traversal p95 latency by hop depth"),
        "## Lookups and aggregation\n",
        table_lookups(results),
        embed("lookups", "Lookup and aggregation p50 latency"),
        "## Mixed workload concurrency sweep\n",
        table_concurrency(results),
        embed("concurrency", "Mixed workload scaling"),
        "## Cold start vs warm\n",
        "First touch after load, before any warm-up, against the warm p50 for "
        "the same workload.\n",
        table_cold_start(results),
        "## Run-to-run variance\n",
        "The read suite repeated end to end. Spread shows how much of any "
        "difference between platforms is just noise.\n",
        table_variance(results),
        "## Footprint\n",
        table_footprint(results),
    ]

    errors = [
        (p["name"], e)
        for p in results["platforms"]
        for e in (p.get("errors") or [])
    ]
    skipped = [(p["name"], p["skipped"]) for p in results["platforms"] if p.get("skipped")]
    if errors or skipped:
        sections.append("## Errors and skipped platforms\n")
        sections.append(
            _table(["Platform", "Detail"], [[n, d] for n, d in skipped + errors])
        )

    report_path = results_dir / "REPORT.md"
    report_path.write_text("\n".join(sections))

    # Keep the README's results matrix generated rather than hand-maintained.
    inject_readme(results, results_dir.parent / "README.md")
    return report_path


README_START = "<!-- BENCHMARK_RESULTS:START -->"
README_END = "<!-- BENCHMARK_RESULTS:END -->"


def build_results_section(results: dict, charts_rel: str = "results/charts") -> str:
    """The results matrix as it appears in the README.

    Generated rather than hand-maintained: a results table typed by hand is a
    table that silently goes stale the next time the benchmark runs.
    """
    def embed(name: str, alt: str) -> str:
        path = Path(charts_rel) / name
        return f"\n![{alt}]({path.as_posix()})\n"

    ds = results["dataset"]
    env = results["environment"]
    settings = results["settings"]
    parity = results.get("parity_check", {})

    status_line = {
        "pass": "**All platforms returned identical results for every workload.** "
                "The latency comparison below is between engines answering the same questions.",
        "MISMATCH": "**Result parity FAILED.** At least two platforms returned different "
                    "values for the same workload, so the latency comparison below is "
                    "not valid. See the parity table.",
    }.get(parity.get("status"), f"Parity not checkable: {parity.get('reason', 'unknown')}.")

    parts = [
        f"Run `{results['run_id']}` · client: {env['platform']} "
        f"({env['cpu_count']} CPUs) · {settings['iterations']} iterations per read "
        f"workload after {settings['warmup_iterations']} warm-up, "
        f"{settings['repeats']} repeats · dataset "
        f"{ds['node_count']:,} nodes / {ds['relationship_count']:,} relationships.\n",
        "### Result parity\n",
        status_line + "\n",
        table_parity(results),
        "### Tier parity\n",
        table_platforms(results),
        "### Transport baseline\n",
        "Median round-trip for a query that does no work (`RETURN 1`). This is "
        "the floor under every latency below: no workload can beat its own "
        "transport. It is the number to subtract before comparing a managed "
        "endpoint against a container on loopback.\n",
        table_baseline(results),
        "### Ingest throughput\n",
        table_ingest(results),
        embed("ingest_throughput.png", "Ingest throughput by platform"),
        "### Traversal latency (ms)\n",
        table_traversal(results),
        embed("traversal_p50.png", "Traversal p50 latency by hop depth"),
        embed("traversal_p95.png", "Traversal p95 latency by hop depth"),
        "### Lookups and aggregation (ms)\n",
        table_lookups(results),
        embed("lookups_p50.png", "Lookup and aggregation p50 latency"),
        "### Mixed workload — concurrency sweep (80% read / 20% write)\n",
        table_concurrency(results),
        embed("concurrency_scaling.png", "Mixed workload scaling"),
        "### Cold start vs. warm\n",
        table_cold_start(results),
        "### Run-to-run variance\n",
        "How much of the difference between platforms is noise. Spread is the "
        "range of p50 across repeated runs of the whole read suite.\n",
        table_variance(results),
        "### Footprint\n",
        "Section 5.2 asks for resource usage *where observable*. It is stated "
        "plainly where it is not.\n",
        table_footprint(results),
    ]

    errors = [(p["name"], e) for p in results["platforms"] for e in (p.get("errors") or [])]
    skipped = [(p["name"], p["skipped"]) for p in results["platforms"] if p.get("skipped")]
    if errors or skipped:
        parts += [
            "### Skipped platforms and errors\n",
            _table(["Platform", "Detail"], [[n, d] for n, d in skipped + errors]),
        ]

    return "\n".join(parts)


def inject_readme(results: dict, readme_path: Path) -> bool:
    """Replace the marked region of the README with the generated results."""
    readme_path = Path(readme_path)
    if not readme_path.exists():
        return False
    text = readme_path.read_text()
    if README_START not in text or README_END not in text:
        return False

    before = text.split(README_START)[0]
    after = text.split(README_END, 1)[1]
    section = build_results_section(results)
    readme_path.write_text(f"{before}{README_START}\n\n{section}\n{README_END}{after}")
    return True
