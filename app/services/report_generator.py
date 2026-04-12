"""
Report generator — Jinja2 HTML → PDF via WeasyPrint
Professional snagging report with summary, tables, photos
"""
from datetime import datetime
from typing import List, Dict, Any


REPORT_HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 20mm 15mm 25mm 15mm;
    @bottom-center {
      content: "Page " counter(page) " of " counter(pages);
      font-size: 9px;
      color: #999;
    }
  }
  body {
    font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 11px;
    color: #1a1a1a;
    line-height: 1.5;
  }
  .header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    padding-bottom: 12px;
    border-bottom: 3px solid #FF6B35;
    margin-bottom: 20px;
  }
  .header h1 {
    font-size: 22px;
    color: #FF6B35;
    margin: 0 0 4px 0;
    letter-spacing: -0.5px;
  }
  .header .subtitle { font-size: 14px; color: #333; margin: 0; }
  .header .meta { font-size: 10px; color: #888; text-align: right; line-height: 1.8; }

  h2 {
    font-size: 13px;
    color: #FF6B35;
    margin: 24px 0 10px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid #eee;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .summary-grid {
    display: flex;
    gap: 12px;
    margin-bottom: 20px;
  }
  .summary-box {
    flex: 1;
    text-align: center;
    padding: 12px;
    border-radius: 8px;
    background: #f8f8f8;
    border: 1px solid #eee;
  }
  .summary-box .number {
    font-size: 28px;
    font-weight: 700;
    line-height: 1;
  }
  .summary-box .label {
    font-size: 9px;
    color: #888;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 4px;
  }
  .summary-box.open .number { color: #EF4444; }
  .summary-box.closed .number { color: #22C55E; }
  .summary-box.high .number { color: #F59E0B; }

  table {
    width: 100%;
    border-collapse: collapse;
    margin: 8px 0 16px 0;
    font-size: 10px;
  }
  th {
    background: #f5f5f5;
    padding: 8px 6px;
    text-align: left;
    font-weight: 600;
    border: 1px solid #ddd;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: #555;
  }
  td {
    padding: 8px 6px;
    border: 1px solid #ddd;
    vertical-align: top;
  }
  tr:nth-child(even) { background: #fafafa; }

  .badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
  }
  .badge-open { background: #fef2f2; color: #EF4444; }
  .badge-closed { background: #f0fdf4; color: #22C55E; }
  .badge-high { background: #fef2f2; color: #EF4444; }
  .badge-medium { background: #fff7ed; color: #F59E0B; }
  .badge-low { background: #f5f5f5; color: #888; }

  .snag-photo {
    width: 80px;
    height: 60px;
    object-fit: cover;
    border-radius: 4px;
    border: 1px solid #ddd;
  }

  .footer {
    margin-top: 30px;
    padding-top: 10px;
    border-top: 1px solid #ddd;
    font-size: 9px;
    color: #aaa;
    display: flex;
    justify-content: space-between;
  }

  .closed-row td { color: #999; }
  .closed-note { text-decoration: line-through; }
</style>
</head>
<body>

<!-- HEADER -->
<div class="header">
  <div>
    <h1>SNAGGING REPORT</h1>
    <p class="subtitle">{{ project.name }}</p>
    <p style="font-size: 11px; color: #666; margin-top: 2px;">
      Client: {{ project.client or '—' }}
    </p>
  </div>
  <div class="meta">
    <div>Date: {{ report_date }}</div>
    <div>Inspector: {{ inspector }}</div>
    <div>Ref: SF-{{ project.id[:8] | upper }}</div>
    {% if project.address %}
    <div>Site: {{ project.address }}</div>
    {% endif %}
  </div>
</div>

<!-- SUMMARY -->
<h2>Summary</h2>
<div class="summary-grid">
  <div class="summary-box">
    <div class="number">{{ total }}</div>
    <div class="label">Total Snags</div>
  </div>
  <div class="summary-box open">
    <div class="number">{{ open_count }}</div>
    <div class="label">Open</div>
  </div>
  <div class="summary-box closed">
    <div class="number">{{ closed_count }}</div>
    <div class="label">Closed</div>
  </div>
  <div class="summary-box high">
    <div class="number">{{ high_count }}</div>
    <div class="label">High Priority</div>
  </div>
</div>

<!-- OPEN SNAGS -->
{% if open_snags %}
<h2>Open Snags ({{ open_snags | length }})</h2>
<table>
  <thead>
    <tr>
      <th style="width: 6%">#</th>
      {% if has_photos %}<th style="width: 12%">Photo</th>{% endif %}
      <th>Description</th>
      <th style="width: 20%">Location</th>
      <th style="width: 10%">Priority</th>
      <th style="width: 12%">Date</th>
    </tr>
  </thead>
  <tbody>
    {% for snag in open_snags %}
    <tr>
      <td style="font-weight: 600; font-family: monospace;">{{ loop.index }}</td>
      {% if has_photos %}
      <td>
        {% if snag.photo_url %}
        <img class="snag-photo" src="{{ snag.photo_url }}" alt="Snag photo">
        {% else %}
        <span style="color: #ccc; font-size: 9px;">No photo</span>
        {% endif %}
      </td>
      {% endif %}
      <td>{{ snag.note }}</td>
      <td>{{ snag.location or '—' }}</td>
      <td><span class="badge badge-{{ snag.priority }}">{{ snag.priority | upper }}</span></td>
      <td style="font-size: 9px;">{{ snag.created_at[:10] }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

<!-- CLOSED SNAGS -->
{% if closed_snags %}
<h2>Closed Snags ({{ closed_snags | length }})</h2>
<table>
  <thead>
    <tr>
      <th style="width: 6%">#</th>
      <th>Description</th>
      <th style="width: 20%">Location</th>
      <th style="width: 12%">Date</th>
    </tr>
  </thead>
  <tbody>
    {% for snag in closed_snags %}
    <tr class="closed-row">
      <td style="font-weight: 600; font-family: monospace;">{{ loop.index }}</td>
      <td class="closed-note">{{ snag.note }}</td>
      <td>{{ snag.location or '—' }}</td>
      <td style="font-size: 9px;">{{ snag.created_at[:10] }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% endif %}

<!-- FOOTER -->
<div class="footer">
  <span>Generated by SnagFlow — {{ report_date }}</span>
  <span>{{ inspector }}</span>
</div>

</body>
</html>
"""


def generate_report_pdf(
    project: Dict[str, Any],
    snags: List[Dict[str, Any]],
    inspector_email: str,
) -> bytes:
    """
    Generate a PDF report from project data and snags.
    Returns raw PDF bytes.
    """
    from jinja2 import Template
    from xhtml2pdf import pisa

    open_snags = [s for s in snags if s["status"] == "open"]
    closed_snags = [s for s in snags if s["status"] == "closed"]
    high_priority = [s for s in open_snags if s.get("priority") == "high"]
    has_photos = any(s.get("photo_url") for s in snags)

    template = Template(REPORT_HTML_TEMPLATE)
    html_content = template.render(
        project=project,
        open_snags=open_snags,
        closed_snags=closed_snags,
        total=len(snags),
        open_count=len(open_snags),
        closed_count=len(closed_snags),
        high_count=len(high_priority),
        has_photos=has_photos,
        inspector=inspector_email,
        report_date=datetime.now().strftime("%d %b %Y"),
    )

   from io import BytesIO
    buffer = BytesIO()
    pisa.CreatePDF(html_content, dest=buffer)
    return buffer.getvalue()
