"""Dashboard service: render dashboards from dataset data.

Dashboards are rendered server-side as HTML from dataset rows + widget config.
No S3 deployment — dashboards live as routes in the tenant console.
"""

from __future__ import annotations

import hashlib
import html
import secrets
from typing import Any


def generate_read_token() -> tuple[str, str]:
    """Generate a read token and its hash.

    Returns (raw_token, token_hash).
    """
    raw = secrets.token_urlsafe(32)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def hash_password(password: str) -> str:
    """Hash a dashboard access password."""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a password against a stored hash."""
    return hashlib.sha256(password.encode()).hexdigest() == password_hash


def build_default_config(columns: list[dict]) -> dict:
    """Build a default dashboard config showing all columns as a table.

    Args:
        columns: Dataset column definitions, e.g. [{"name": "url", "type": "text"}]

    Returns:
        Config dict with a single table widget.
    """
    col_names = [c.get("name", "unknown") for c in columns] if columns else []
    return {
        "widgets": [
            {
                "type": "table",
                "title": "All Data",
                "columns": col_names,
                "sort_by": None,
                "sort_dir": "desc",
                "limit": 100,
            }
        ]
    }


def _esc(val: Any) -> str:
    """HTML-escape a value for safe rendering."""
    if val is None:
        return "—"
    return html.escape(str(val))


def _render_table_widget(widget: dict, rows: list[dict]) -> str:
    """Render a table widget as HTML."""
    columns = widget.get("columns", [])
    title = _esc(widget.get("title", "Data"))
    sort_by = widget.get("sort_by")
    sort_dir = widget.get("sort_dir", "desc")
    limit = widget.get("limit", 100)

    # Sort rows if sort_by specified
    display_rows = list(rows)
    if sort_by and display_rows:
        display_rows.sort(
            key=lambda r: (r.get(sort_by) is None, r.get(sort_by, "")),
            reverse=(sort_dir == "desc"),
        )
    display_rows = display_rows[:limit]

    if not columns and display_rows:
        # Auto-detect columns from first row
        columns = list(display_rows[0].keys())

    header = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body_rows = []
    for row in display_rows:
        cells = "".join(f"<td>{_esc(row.get(c))}</td>" for c in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "\n".join(body_rows)

    return f"""
    <div class="db-widget db-table">
        <h3>{title}</h3>
        <div class="db-table-wrap">
            <table>
                <thead><tr>{header}</tr></thead>
                <tbody>{body}</tbody>
            </table>
        </div>
        <div class="db-table-count">{len(display_rows)} of {len(rows)} rows</div>
    </div>
    """


def _render_metric_widget(widget: dict, rows: list[dict]) -> str:
    """Render a metric widget as HTML."""
    title = _esc(widget.get("title", "Metric"))
    column = widget.get("column")
    aggregation = widget.get("aggregation", "count")

    if aggregation == "count" or column is None:
        value = len(rows)
    else:
        values = [r.get(column) for r in rows if r.get(column) is not None]
        numeric = []
        for v in values:
            try:
                numeric.append(float(v))
            except (ValueError, TypeError):
                pass

        if aggregation == "sum":
            value = sum(numeric) if numeric else 0
        elif aggregation == "avg":
            value = sum(numeric) / len(numeric) if numeric else 0
        elif aggregation == "min":
            value = min(numeric) if numeric else 0
        elif aggregation == "max":
            value = max(numeric) if numeric else 0
        else:
            value = len(rows)

    # Format nicely
    if isinstance(value, float):
        display = f"{value:,.1f}"
    else:
        display = f"{value:,}"

    return f"""
    <div class="db-widget db-metric">
        <div class="db-metric-value">{display}</div>
        <div class="db-metric-label">{title}</div>
    </div>
    """


def _render_stat_widget(widget: dict, rows: list[dict]) -> str:
    """Render a stat widget — like metric but with optional comparison."""
    title = _esc(widget.get("title", "Stat"))
    column = widget.get("column")
    aggregation = widget.get("aggregation", "count")

    if aggregation == "count" or column is None:
        value = len(rows)
    else:
        values = [r.get(column) for r in rows if r.get(column) is not None]
        numeric = []
        for v in values:
            try:
                numeric.append(float(v))
            except (ValueError, TypeError):
                pass
        if aggregation == "sum":
            value = sum(numeric) if numeric else 0
        elif aggregation == "avg":
            value = sum(numeric) / len(numeric) if numeric else 0
        elif aggregation == "min":
            value = min(numeric) if numeric else 0
        elif aggregation == "max":
            value = max(numeric) if numeric else 0
        else:
            value = len(rows)

    if isinstance(value, float):
        display = f"{value:,.1f}"
    else:
        display = f"{value:,}"

    subtitle = _esc(widget.get("subtitle", ""))
    subtitle_html = f'<div class="db-stat-sub">{subtitle}</div>' if subtitle else ""

    return f"""
    <div class="db-widget db-stat">
        <div class="db-stat-label">{title}</div>
        <div class="db-stat-value">{display}</div>
        {subtitle_html}
    </div>
    """


def _render_bar_chart_widget(widget: dict, rows: list[dict]) -> str:
    """Render a horizontal bar chart as pure HTML/CSS (no JS libraries)."""
    title = _esc(widget.get("title", "Chart"))
    label_col = widget.get("label_column") or widget.get("label", "")
    value_col = widget.get("value_column") or widget.get("value", "")
    limit = widget.get("limit", 20)
    sort_dir = widget.get("sort_dir", "desc")

    if not label_col or not value_col:
        # Try to auto-detect: first string-ish col as label, first numeric as value
        if rows:
            first = rows[0]
            for k, v in first.items():
                if not label_col and isinstance(v, str):
                    label_col = k
                if not value_col:
                    try:
                        float(v)
                        value_col = k
                    except (ValueError, TypeError):
                        pass

    if not label_col or not value_col:
        return f'<div class="db-widget"><h3>{title}</h3><div class="db-empty">Cannot render chart: specify label_column and value_column.</div></div>'

    # Extract and sort data
    entries: list[tuple[str, float]] = []
    for r in rows:
        label = r.get(label_col)
        val = r.get(value_col)
        if label is not None and val is not None:
            try:
                entries.append((_esc(str(label)), float(val)))
            except (ValueError, TypeError):
                pass

    entries.sort(key=lambda x: x[1], reverse=(sort_dir == "desc"))
    entries = entries[:limit]

    if not entries:
        return f'<div class="db-widget"><h3>{title}</h3><div class="db-empty">No data for chart.</div></div>'

    max_val = max(v for _, v in entries) if entries else 1
    if max_val == 0:
        max_val = 1

    bars_html = []
    for label, val in entries:
        pct = (val / max_val) * 100
        display_val = f"{val:,.1f}" if isinstance(val, float) and val != int(val) else f"{int(val):,}"
        bars_html.append(
            f'<div class="db-bar-row">'
            f'<span class="db-bar-label">{label}</span>'
            f'<div class="db-bar-track"><div class="db-bar-fill" style="width:{pct:.1f}%"></div></div>'
            f'<span class="db-bar-val">{display_val}</span>'
            f'</div>'
        )

    return f"""
    <div class="db-widget db-bar-chart">
        <h3>{title}</h3>
        <div class="db-bars">{"".join(bars_html)}</div>
    </div>
    """


def _render_pie_chart_widget(widget: dict, rows: list[dict]) -> str:
    """Render a pie chart as pure CSS conic-gradient (no JS libraries)."""
    title = _esc(widget.get("title", "Distribution"))
    label_col = widget.get("label_column") or widget.get("label", "")
    value_col = widget.get("value_column") or widget.get("value", "")
    limit = widget.get("limit", 10)

    if not label_col or not value_col:
        if rows:
            first = rows[0]
            for k, v in first.items():
                if not label_col and isinstance(v, str):
                    label_col = k
                if not value_col:
                    try:
                        float(v)
                        value_col = k
                    except (ValueError, TypeError):
                        pass

    if not label_col or not value_col:
        return f'<div class="db-widget"><h3>{title}</h3><div class="db-empty">Cannot render chart: specify label_column and value_column.</div></div>'

    entries: list[tuple[str, float]] = []
    for r in rows:
        label = r.get(label_col)
        val = r.get(value_col)
        if label is not None and val is not None:
            try:
                entries.append((_esc(str(label)), abs(float(val))))
            except (ValueError, TypeError):
                pass

    entries.sort(key=lambda x: x[1], reverse=True)
    entries = entries[:limit]

    total = sum(v for _, v in entries)
    if total == 0:
        return f'<div class="db-widget"><h3>{title}</h3><div class="db-empty">No data for chart.</div></div>'

    # Build conic-gradient stops
    colors = [
        "#667eea", "#764ba2", "#f093fb", "#4facfe", "#00f2fe",
        "#43e97b", "#fa709a", "#fee140", "#a18cd1", "#fbc2eb",
    ]
    stops = []
    legend_items = []
    cumulative = 0.0
    for i, (label, val) in enumerate(entries):
        color = colors[i % len(colors)]
        start_pct = (cumulative / total) * 100
        cumulative += val
        end_pct = (cumulative / total) * 100
        stops.append(f"{color} {start_pct:.1f}% {end_pct:.1f}%")
        pct_display = f"{(val / total) * 100:.1f}%"
        legend_items.append(
            f'<div class="db-pie-legend-item">'
            f'<span class="db-pie-swatch" style="background:{color}"></span>'
            f'<span class="db-pie-legend-label">{label}</span>'
            f'<span class="db-pie-legend-val">{pct_display}</span>'
            f'</div>'
        )

    gradient = ", ".join(stops)
    legend_html = "".join(legend_items)

    return f"""
    <div class="db-widget db-pie-chart">
        <h3>{title}</h3>
        <div class="db-pie-container">
            <div class="db-pie-circle" style="background: conic-gradient({gradient});"></div>
            <div class="db-pie-legend">{legend_html}</div>
        </div>
    </div>
    """


def render_dashboard_html(
    dashboard_name: str,
    dataset_name: str,
    config: dict,
    rows: list[dict],
    *,
    back_url: str | None = None,
) -> str:
    """Render a full dashboard page as HTML.

    Args:
        dashboard_name: Dashboard title
        dataset_name: Source dataset name
        config: Widget configuration
        rows: Dataset rows (list of dicts)
        back_url: Optional back link (for authenticated views)

    Returns:
        Complete HTML page string.
    """
    widgets_html = []
    for widget in config.get("widgets", []):
        wtype = widget.get("type", "table")
        if wtype == "table":
            widgets_html.append(_render_table_widget(widget, rows))
        elif wtype in ("metric", "stat"):
            widgets_html.append(_render_stat_widget(widget, rows))
        elif wtype == "bar_chart":
            widgets_html.append(_render_bar_chart_widget(widget, rows))
        elif wtype == "pie_chart":
            widgets_html.append(_render_pie_chart_widget(widget, rows))

    if not widgets_html:
        widgets_html.append(
            '<div class="db-empty">No widgets configured.</div>'
        )

    widgets_block = "\n".join(widgets_html)
    back_link = f'<a href="{_esc(back_url)}" class="db-back">&larr; Back to console</a>' if back_url else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(dashboard_name)} — nrev-lite Dashboard</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0f; color: #e0e0e0; }}
    .db-header {{ padding: 24px 32px; border-bottom: 1px solid #1e1e2e; display: flex; align-items: center; gap: 16px; }}
    .db-header h1 {{ font-size: 20px; font-weight: 600; color: #fff; }}
    .db-header .db-source {{ font-size: 13px; color: #888; background: #1a1a2e; padding: 4px 10px; border-radius: 6px; }}
    .db-back {{ color: #888; text-decoration: none; font-size: 13px; }}
    .db-back:hover {{ color: #fff; }}
    .db-body {{ padding: 24px 32px; max-width: 1400px; }}
    .db-widget {{ margin-bottom: 24px; background: #111118; border: 1px solid #1e1e2e; border-radius: 10px; overflow: hidden; }}
    .db-widget h3 {{ padding: 16px 20px 12px; font-size: 14px; font-weight: 600; color: #ccc; }}
    .db-table-wrap {{ overflow-x: auto; }}
    .db-table-wrap table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .db-table-wrap th {{ text-align: left; padding: 10px 16px; background: #0d0d14; color: #888; font-weight: 500; border-bottom: 1px solid #1e1e2e; white-space: nowrap; }}
    .db-table-wrap td {{ padding: 10px 16px; border-bottom: 1px solid #141420; color: #ccc; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .db-table-wrap tr:hover td {{ background: #14141f; }}
    .db-table-count {{ padding: 10px 20px; font-size: 12px; color: #666; }}
    .db-metric {{ padding: 24px 20px; text-align: center; }}
    .db-metric-value {{ font-size: 42px; font-weight: 700; color: #fff; }}
    .db-metric-label {{ font-size: 13px; color: #888; margin-top: 6px; }}
    .db-stat {{ padding: 24px 20px; text-align: center; }}
    .db-stat-value {{ font-size: 42px; font-weight: 700; color: #fff; }}
    .db-stat-label {{ font-size: 13px; color: #888; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .db-stat-sub {{ font-size: 12px; color: #666; margin-top: 6px; }}
    .db-bars {{ padding: 12px 20px 20px; }}
    .db-bar-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
    .db-bar-label {{ width: 120px; font-size: 12px; color: #ccc; text-align: right; flex-shrink: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .db-bar-track {{ flex: 1; height: 20px; background: #1a1a2e; border-radius: 4px; overflow: hidden; }}
    .db-bar-fill {{ height: 100%; background: linear-gradient(90deg, #667eea, #764ba2); border-radius: 4px; transition: width 0.3s; }}
    .db-bar-val {{ width: 60px; font-size: 12px; color: #888; text-align: right; flex-shrink: 0; }}
    .db-pie-container {{ display: flex; align-items: center; gap: 32px; padding: 16px 20px 24px; flex-wrap: wrap; }}
    .db-pie-circle {{ width: 180px; height: 180px; border-radius: 50%; flex-shrink: 0; }}
    .db-pie-legend {{ flex: 1; min-width: 150px; }}
    .db-pie-legend-item {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 12px; }}
    .db-pie-swatch {{ width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }}
    .db-pie-legend-label {{ color: #ccc; flex: 1; }}
    .db-pie-legend-val {{ color: #888; }}
    .db-empty {{ padding: 40px; text-align: center; color: #666; }}
    .db-password-form {{ max-width: 360px; margin: 80px auto; text-align: center; }}
    .db-password-form h2 {{ margin-bottom: 16px; font-size: 18px; color: #fff; }}
    .db-password-form input {{ width: 100%; padding: 10px 14px; background: #111118; border: 1px solid #1e1e2e; border-radius: 8px; color: #fff; font-size: 14px; margin-bottom: 12px; }}
    .db-password-form button {{ padding: 10px 24px; background: linear-gradient(135deg, #667eea, #764ba2); border: none; border-radius: 8px; color: #fff; font-size: 14px; cursor: pointer; }}
</style>
</head>
<body>
<div class="db-header">
    {back_link}
    <h1>{_esc(dashboard_name)}</h1>
    <span class="db-source">Source: {_esc(dataset_name)}</span>
</div>
<div class="db-body">
    {widgets_block}
</div>
</body>
</html>"""


def render_password_page(dashboard_name: str, token: str) -> str:
    """Render a password prompt page for protected dashboards."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(dashboard_name)} — Password Required</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0a0a0f; color: #e0e0e0; }}
    .db-password-form {{ max-width: 360px; margin: 120px auto; text-align: center; }}
    .db-password-form h2 {{ margin-bottom: 8px; font-size: 18px; color: #fff; }}
    .db-password-form p {{ margin-bottom: 20px; font-size: 13px; color: #888; }}
    .db-password-form input {{ width: 100%; padding: 10px 14px; background: #111118; border: 1px solid #1e1e2e; border-radius: 8px; color: #fff; font-size: 14px; margin-bottom: 12px; }}
    .db-password-form button {{ padding: 10px 24px; background: linear-gradient(135deg, #667eea, #764ba2); border: none; border-radius: 8px; color: #fff; font-size: 14px; cursor: pointer; width: 100%; }}
</style>
</head>
<body>
<div class="db-password-form">
    <h2>{_esc(dashboard_name)}</h2>
    <p>This dashboard is password-protected.</p>
    <form method="POST" action="/d/{_esc(token)}">
        <input type="password" name="password" placeholder="Enter password" autofocus>
        <button type="submit">View Dashboard</button>
    </form>
</div>
</body>
</html>"""
