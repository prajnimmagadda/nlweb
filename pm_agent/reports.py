"""Render a stored status report as an email (plain text and HTML).

The email is built only from the stored report, so its RAG and metrics are the
computed ones and the agent cannot add content that isn't in the report.
"""

from html import escape

from pm_agent.schemas import ProjectProfile, StatusReport

_COLOURS = {"green": "#1a7f37", "amber": "#9a6700", "red": "#cf222e", "unknown": "#57606a"}


def subject(report: StatusReport, profile: ProjectProfile) -> str:
    return f"[{profile.name}] Status {report.period_end.isoformat()}: {report.overall.upper()}"


def _sections(report: StatusReport) -> list[tuple[str, list[str]]]:
    return [
        ("Accomplishments", report.accomplishments),
        ("Next steps", report.next_steps),
        ("Decisions needed", report.decisions_needed),
        ("Top risks", report.top_risks),
    ]


def render_text(report: StatusReport, profile: ProjectProfile) -> str:
    lines = [
        f"{profile.name}: status {report.period_start.isoformat()} to {report.period_end.isoformat()}",
        f"Overall: {report.overall.upper()}",
        "",
        report.summary.strip(),
        "",
        "Health (computed from project data):",
    ]
    lines += [f"  - {d.name}: {d.rag.upper()}" + (f" ({d.value})" if d.value else "") + f". {d.explanation}"
              for d in report.dimensions]
    for title, items in _sections(report):
        if items:
            lines += ["", f"{title}:"] + [f"  - {item}" for item in items]
    lines += ["", f"Report {report.id}, drafted by PM Copilot. Review before sending."]
    return "\n".join(lines)


def render_html(report: StatusReport, profile: ProjectProfile) -> str:
    def badge(rag: str) -> str:
        return (f'<span style="color:#fff;background:{_COLOURS[rag]};padding:1px 6px;border-radius:4px;'
                f'font-weight:600">{escape(rag.upper())}</span>')

    rows = "".join(
        f"<tr><td style='padding:2px 8px'>{escape(d.name)}</td><td style='padding:2px 8px'>{badge(d.rag)}</td>"
        f"<td style='padding:2px 8px'>{escape(d.value or '')}</td>"
        f"<td style='padding:2px 8px'>{escape(d.explanation)}</td></tr>"
        for d in report.dimensions
    )
    sections = "".join(
        f"<h3>{escape(title)}</h3><ul>{''.join(f'<li>{escape(item)}</li>' for item in items)}</ul>"
        for title, items in _sections(report) if items
    )
    return (
        f"<div style='font-family:sans-serif'>"
        f"<h2>{escape(profile.name)}: {badge(report.overall)}</h2>"
        f"<p>{escape(report.period_start.isoformat())} to {escape(report.period_end.isoformat())}</p>"
        f"<p>{escape(report.summary.strip())}</p>"
        f"<h3>Health (computed from project data)</h3><table>{rows}</table>{sections}"
        f"<p style='color:#57606a'>Report {escape(report.id)}, drafted by PM Copilot. Review before sending.</p>"
        f"</div>"
    )
