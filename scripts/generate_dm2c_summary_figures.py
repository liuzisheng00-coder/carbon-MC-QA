#!/usr/bin/env python3
"""Generate source-backed figures for the DM2C V9 experiment summary."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import ListedColormap


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "research_experiments" / "summary_figures_v9"

SOURCES = {
    "e1": ROOT / "outputs/research_experiments/e1_independent_final_20260711/e1_independent_truth_final_report.json",
    "e2": ROOT / "outputs/research_experiments/run_20260708_153758/e2_property_report.json",
    "e2b": ROOT / "outputs/research_experiments/e2b_allocation_sensitivity/e2b_20260711_192317/allocation_sensitivity_report.json",
    "e2b_figure": ROOT / "outputs/research_experiments/e2b_allocation_sensitivity/e2b_20260711_192317/allocation_sensitivity_figure.png",
    "e3": ROOT / "outputs/research_experiments/run_20260708_153758/e3_data_readiness_profiles.json",
    "e3_figure": ROOT / "outputs/research_experiments/run_20260708_153758/e3_data_readiness_profile.png",
    "e4": ROOT / "outputs/research_experiments/e4_benchmark/frozen_20260712_145321_v9_human_pass/e4_benchmark_summary_human_reviewed_v9.json",
    "e5": ROOT / "outputs/research_experiments/qa_comparison_v9_m2/qa_comparison_20260712_155634/qa_comparison_report.json",
    "e6": ROOT / "outputs/research_experiments/e6_real_ablations_v9_m2/paper_ready_audit/e6_v9_paper_ready_audit.json",
    "e7_allocation": ROOT / "outputs/research_experiments/e7_m2_aligned_allocation/latest_injection_robustness_report.json",
    "e7_core": ROOT / "outputs/research_experiments/e7_m2_aligned_core/latest_injection_robustness_report.json",
    "m2": ROOT / "outputs/research_experiments/m2_typed_completed_20260728_layerdedup/m2_alignment_report.json",
}

COLORS = {
    "full": "#167D5A",
    "graph": "#2978A0",
    "llm": "#D97706",
    "kgllm": "#B33A3A",
    "muted": "#77808A",
    "light": "#D9E1E8",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configure_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.labelsize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 160,
            "savefig.dpi": 220,
            "savefig.bbox": "tight",
            "savefig.facecolor": "white",
            "axes.facecolor": "white",
            "figure.facecolor": "white",
        }
    )


def label_bars(ax: plt.Axes, bars: Any, *, percent: bool = False) -> None:
    for bar in bars:
        value = float(bar.get_height())
        label = f"{value * 100:.1f}%" if percent else f"{value:.0f}"
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + (0.018 if percent else 0.08),
            label,
            ha="center",
            va="bottom",
            fontsize=7.5,
        )


def fig_e2_property_checks() -> Path:
    report = read_json(SOURCES["e2"])
    main = report["graphs"]["m2_3_main_model_multigranular_carbon_kg"]["checks"]
    negatives = report["main_graph_perturbation_negative_controls"]
    labels = ["Main KG", "Delete edge", "Change unit", "Break allocation"]
    values = [
        main["violation_count"],
        negatives["delete_emission_of_edge"]["violation_count"],
        negatives["change_quantity_unit"]["violation_count"],
        negatives["break_batch_allocation"]["violation_count"],
    ]
    fig, ax = plt.subplots(figsize=(6.6, 3.2))
    bars = ax.bar(labels, values, color=[COLORS["full"], COLORS["llm"], COLORS["llm"], COLORS["llm"]])
    ax.set_ylabel("Detected property violations")
    ax.set_ylim(0, max(values) + 0.8)
    ax.axhline(0, color="#30363D", linewidth=0.8)
    label_bars(ax, bars)
    ax.text(0, 0.12, "PASS", ha="center", va="bottom", color=COLORS["full"], fontweight="bold")
    fig.tight_layout()
    path = OUT / "fig_e2_property_checks.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_e4_coverage() -> Path:
    counts = read_json(SOURCES["e4"])["validation"]["status_counts"]
    order = ["executable", "incomplete_path", "empty_result", "unresolved_target", "clarification_required"]
    labels = ["Executable", "Incomplete path", "Empty result", "Unresolved target", "Clarification"]
    values = [counts[key] for key in order]
    colors = [COLORS["full"], "#5B8FF9", "#A3AAB3", "#D97706", "#8E5AA7"]
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    bars = ax.bar(labels, values, color=colors)
    ax.set_ylabel("Questions")
    ax.set_ylim(0, 100)
    ax.tick_params(axis="x", rotation=18)
    label_bars(ax, bars)
    fig.tight_layout()
    path = OUT / "fig_e4_status_coverage.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_e5_baselines() -> Path:
    overall = read_json(SOURCES["e5"])["overall"]
    variants = ["full_real_v9_merged", "graph_only_real", "llm_only_real", "unconstrained_kg_llm_real"]
    labels = ["DM2C full", "Graph-only", "LLM-only", "KG+LLM free"]
    colors = [COLORS["full"], COLORS["graph"], COLORS["llm"], COLORS["kgllm"]]
    accuracy_metrics = ["numeric_accuracy", "status_accuracy", "pass_rate"]
    risk_metrics = ["unsupported_answer_rate", "hallucinated_reference_rate"]
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.7), gridspec_kw={"width_ratios": [1.45, 1]})
    x = np.arange(len(labels))
    width = 0.22
    for idx, metric in enumerate(accuracy_metrics):
        axes[0].bar(
            x + (idx - 1) * width,
            [overall[v][metric] for v in variants],
            width,
            label={"numeric_accuracy": "Numeric", "status_accuracy": "Status", "pass_rate": "Overall pass"}[metric],
            color=["#4C78A8", "#72B7B2", "#54A24B"][idx],
        )
    axes[0].set_xticks(x, labels, rotation=17)
    axes[0].set_ylabel("Rate")
    axes[0].set_ylim(0, 1.08)
    axes[0].legend(frameon=False, ncol=3, loc="upper center")
    for idx, metric in enumerate(risk_metrics):
        axes[1].bar(
            x + (idx - 0.5) * 0.3,
            [overall[v][metric] for v in variants],
            0.3,
            label={"unsupported_answer_rate": "Unsupported answer", "hallucinated_reference_rate": "Hallucinated ref."}[metric],
            color=["#E45756", "#B279A2"][idx],
        )
    axes[1].set_xticks(x, labels, rotation=17)
    axes[1].set_ylabel("Risk rate")
    axes[1].set_ylim(0, 1.08)
    axes[1].legend(frameon=False, loc="upper left")
    fig.tight_layout(w_pad=2.2)
    path = OUT / "fig_e5_baseline_comparison.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_e6_ablations() -> Path:
    report = read_json(SOURCES["e6"])
    full = report["full_system_metrics"]
    variants = report["variants"]
    rows = [
        ("Full", full),
        ("No validation gate", variants["no_validation_gate_real"]["metrics"]),
        ("No status blocking", variants["no_status_blocking_real"]["metrics"]),
        ("No provenance constraint", variants["no_provenance_constraint_real"]["metrics"]),
    ]
    labels = [row[0] for row in rows]
    x = np.arange(len(rows))
    fig, axes = plt.subplots(1, 2, figsize=(10.2, 3.7), gridspec_kw={"width_ratios": [1.35, 1]})
    for idx, metric in enumerate(["numeric_accuracy", "status_accuracy", "pass_rate"]):
        axes[0].bar(
            x + (idx - 1) * 0.22,
            [row[1].get(metric) or 0 for row in rows],
            0.22,
            label={"numeric_accuracy": "Numeric", "status_accuracy": "Status", "pass_rate": "Overall pass"}[metric],
            color=["#4C78A8", "#72B7B2", "#54A24B"][idx],
        )
    axes[0].set_xticks(x, labels, rotation=17)
    axes[0].set_ylim(0, 1.08)
    axes[0].set_ylabel("Rate")
    axes[0].legend(frameon=False, ncol=3, loc="upper center")
    risk = [row[1].get("unsupported_answer_rate") or 0 for row in rows]
    bars = axes[1].bar(labels, risk, color=[COLORS["full"], COLORS["muted"], COLORS["muted"], COLORS["kgllm"]])
    axes[1].set_ylabel("Unsupported-answer rate")
    axes[1].set_ylim(0, 0.36)
    axes[1].tick_params(axis="x", rotation=17)
    label_bars(axes[1], bars, percent=True)
    fig.tight_layout(w_pad=2.2)
    path = OUT / "fig_e6_ablation_effects.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def fig_e7_injections() -> Path:
    report = read_json(SOURCES["e7_allocation"])
    categories = [
        "ambiguous_target_name",
        "drop_allocation_basis",
        "drop_factor",
        "drop_process_record",
        "drop_quantity",
        "synonym_rewrite",
        "unit_mismatch",
    ]
    labels = ["Ambiguous target", "No allocation basis", "No factor", "No process record", "No quantity", "Synonym", "Unit mismatch"]
    metrics = ["status_accuracy", "disclosure_rate", "mutation_application_rate"]
    matrix = np.array(
        [
            [np.nan if report["by_category"][category].get(metric) is None else report["by_category"][category][metric] for metric in metrics]
            for category in categories
        ]
    )
    masked = np.ma.masked_invalid(matrix)
    cmap = ListedColormap(["#F2C14E", "#72B7B2", "#167D5A"])
    cmap.set_bad(color="#E7E9EC")
    fig, ax = plt.subplots(figsize=(6.6, 4.5))
    ax.imshow(masked, vmin=0, vmax=1, cmap=cmap, aspect="auto")
    ax.set_xticks(range(3), ["Status", "Disclosure", "Mutation applied"])
    ax.set_yticks(range(len(labels)), labels)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            text = "N/A" if np.isnan(matrix[i, j]) else f"{matrix[i, j]:.2f}"
            ax.text(j, i, text, ha="center", va="center", color="#202428", fontsize=8)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    path = OUT / "fig_e7_injection_robustness.png"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [str(path) for path in SOURCES.values() if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing formal experiment sources:\n" + "\n".join(missing))
    configure_style()
    generated = [
        fig_e2_property_checks(),
        fig_e4_coverage(),
        fig_e5_baselines(),
        fig_e6_ablations(),
        fig_e7_injections(),
    ]
    copied = []
    for source_key, output_name in [
        ("e2b_figure", "fig_e2b_allocation_sensitivity.png"),
        ("e3_figure", "fig_e3_data_readiness.png"),
    ]:
        destination = OUT / output_name
        shutil.copy2(SOURCES[source_key], destination)
        copied.append(destination)
    manifest = {
        "purpose": "DM2C V9 experiment and contribution summary",
        "sources": {
            key: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "bytes": path.stat().st_size}
            for key, path in SOURCES.items()
        },
        "figures": {
            path.name: {"path": str(path.relative_to(ROOT)), "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in generated + copied
        },
    }
    (OUT / "evidence_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"output_dir": str(OUT), "figure_count": len(generated) + len(copied)}, indent=2))


if __name__ == "__main__":
    main()
