from __future__ import annotations

from pathlib import Path
import re
from typing import Sequence

from pydantic import BaseModel, Field, field_validator

from elasticsearch_agent.config import cfg


class PieChartInput(BaseModel):
    title: str = Field(min_length=1)
    labels: list[str] = Field(min_length=1)
    values: list[float] = Field(min_length=1)
    output_path: str | None = None

    @field_validator("values")
    @classmethod
    def valid_values(cls, values: list[float]) -> list[float]:
        if any(v < 0 for v in values) or sum(values) <= 0:
            raise ValueError("values must be non-negative and have a positive total")
        return values

    def validate_lengths(self):
        if len(self.labels) != len(self.values):
            raise ValueError("labels and values must have equal lengths")


def create_pie_chart(title: str, labels: Sequence[str], values: Sequence[float], output_path: str | None = None) -> dict:
    request = PieChartInput(title=title, labels=list(labels), values=list(values), output_path=output_path)
    request.validate_lengths()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if request.output_path:
        path = Path(request.output_path)
    else:
        slug = re.sub(r"[^a-z0-9]+", "_", request.title.lower()).strip("_") or "chart"
        path = cfg.chart_output_dir / f"{slug}_pie.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(9, 6))
    total = sum(request.values)
    legend_labels = [
        f"{label} — {value:g} ({value / total * 100:.1f}%)"
        for label, value in zip(request.labels, request.values)
    ]
    wedges, _, autotexts = ax.pie(
        request.values,
        labels=None,
        autopct="%1.1f%%",
        pctdistance=0.7,
        startangle=90,
    )
    for autotext in autotexts:
        autotext.set_color("white")
        autotext.set_fontsize(9)
    ax.legend(
        wedges,
        legend_labels,
        title="Categories",
        loc="center left",
        bbox_to_anchor=(1.02, 0.5),
        fontsize=9,
    )
    ax.set_title(request.title)
    ax.axis("equal")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return {"artifact_path": str(path), "title": request.title, "labels": request.labels, "values": request.values, "format": "png"}
