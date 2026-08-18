"""HTML + PDF rendering for the yearly report (PRD FR-10.1). Split into two
functions -- render_yearly_report_html (pure string templating, no native
deps) and render_yearly_report_pdf (WeasyPrint, needs Pango/cairo) -- so
tests asserting on report *content* never need WeasyPrint importable at
all, matching capital_gains_csv.py's "pure formatter, takes the dataclass"
shape, just HTML instead of CSV.
"""

from jinja2 import Template

from fundlens.compute.yearly_report import FundPerformance, YearlyReport

_TEMPLATE = Template(
    """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #1a1a1a; font-size: 11pt; }
  h1 { font-size: 18pt; margin-bottom: 4px; }
  h2 { font-size: 13pt; margin-top: 24px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }
  .muted { color: #666; font-size: 9pt; }
  table { width: 100%; border-collapse: collapse; margin-top: 8px; }
  th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #eee; font-size: 10pt; }
  th { color: #666; font-weight: 600; }
  .positive { color: #0a7a2f; }
  .negative { color: #b3261e; }
</style>
</head>
<body>
  <h1>FundLens Yearly Report -- FY {{ r.financial_year }}</h1>
  <p class="muted">
    Period: {{ r.fy_start.isoformat() }} to {{ r.report_end.isoformat() }}
    {% if r.report_end.isoformat() != fy_end_iso %}(in progress -- through today){% endif %}
  </p>

  <h2>Portfolio Value</h2>
  <table>
    <tr><th>Opening value</th><td>{{ fmt_paise(r.opening_value_paise) }}</td></tr>
    <tr><th>Closing value</th><td>{{ fmt_paise(r.closing_value_paise) }}</td></tr>
  </table>

  <h2>Returns</h2>
  <table>
    <tr><th>This year's XIRR</th><td>{{ fmt_xirr(r.portfolio_xirr_this_year, r.portfolio_xirr_this_year_reason) }}</td></tr>
    <tr><th>Since-inception XIRR (as of {{ r.report_end.isoformat() }})</th>
        <td>{{ fmt_xirr(r.portfolio_xirr_since_inception, r.portfolio_xirr_since_inception_reason) }}</td></tr>
    <tr><th>Benchmark ({{ r.benchmark_id.value }}) since inception</th>
        <td>{{ fmt_xirr(r.benchmark_xirr_since_inception, r.benchmark_xirr_since_inception_reason) }}</td></tr>
    <tr><th>Alpha since inception</th><td>{{ fmt_alpha(r.alpha_pct_since_inception) }}</td></tr>
  </table>
  <p class="muted">
    Benchmark comparison is since-inception as of this FY's end, not a FY-only figure --
    see docs/architecture/compute-engine.md &sect;11 for why.
  </p>

  <h2>Best / Worst Performing Funds This Year</h2>
  {% if r.best_funds %}
  <table>
    <tr><th>Best</th><th>AMC</th><th>XIRR</th></tr>
    {% for f in r.best_funds %}
    <tr><td>{{ f.scheme_name }}</td><td>{{ f.amc }}</td><td>{{ fmt_xirr(f.xirr, f.xirr_reason) }}</td></tr>
    {% endfor %}
  </table>
  <table>
    <tr><th>Worst</th><th>AMC</th><th>XIRR</th></tr>
    {% for f in r.worst_funds %}
    <tr><td>{{ f.scheme_name }}</td><td>{{ f.amc }}</td><td>{{ fmt_xirr(f.xirr, f.xirr_reason) }}</td></tr>
    {% endfor %}
  </table>
  {% else %}
  <p class="muted">Not enough distinct positions this year to rank.</p>
  {% endif %}

  <h2>Allocation Drift</h2>
  {% if r.allocation_drift %}
  <table>
    <tr><th></th><th>Target</th><th>Actual</th></tr>
    <tr><td>Equity</td><td>{{ r.allocation_drift.target_equity_pct }}%</td>
        <td>{{ fmt_pct(r.allocation_drift.actual_equity_pct) }}</td></tr>
    <tr><td>Debt</td><td>{{ r.allocation_drift.target_debt_pct }}%</td>
        <td>{{ fmt_pct(r.allocation_drift.actual_debt_pct) }}</td></tr>
  </table>
  {% else %}
  <p class="muted">No risk profile set -- target allocation not configured.</p>
  {% endif %}

  <h2>Estimated Annual Fees</h2>
  <p>{{ fmt_paise(r.estimated_annual_fee_paise) }} / year across currently held funds
    {% if r.fee_funds_excluded %}({{ r.fee_funds_excluded }} fund(s) excluded -- expense ratio or NAV unavailable){% endif %}
  </p>

  <h2>Realized Gains (FY {{ r.financial_year }})</h2>
  <table>
    <tr><th>Total STCG</th><td>{{ fmt_paise(r.capital_gains.total_stcg_paise) }}</td></tr>
    <tr><th>Total LTCG</th><td>{{ fmt_paise(r.capital_gains.total_ltcg_paise) }}</td></tr>
    <tr><th>Total taxable gain</th><td>{{ fmt_paise(r.capital_gains.total_taxable_gain_paise) }}</td></tr>
    <tr><th>LTCG exemption used</th><td>{{ fmt_paise(r.capital_gains.total_ltcg_exemption_used_paise) }}</td></tr>
  </table>
  <p class="muted">
    {{ r.capital_gains.lines | length }} realized-gain transaction(s) this FY.
    Full line-by-line detail: GET /reports/capital-gains.csv?fy={{ r.financial_year }}
  </p>
</body>
</html>
"""
)


def _fmt_paise(paise: int) -> str:
    return f"Rs {paise / 100:,.2f}"


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def _fmt_xirr(rate: float | None, reason: str | None) -> str:
    if rate is None:
        return f"n/a ({reason})" if reason else "n/a"
    return f"{rate * 100:.2f}%"


def _fmt_alpha(alpha_pct: float | None) -> str:
    return "-" if alpha_pct is None else f"{'+' if alpha_pct >= 0 else ''}{alpha_pct:.2f}%"


def render_yearly_report_html(report: YearlyReport) -> str:
    fy_end_iso = f"{int(report.financial_year.split('-')[0]) + 1}-03-31"
    return _TEMPLATE.render(
        r=report,
        fy_end_iso=fy_end_iso,
        fmt_paise=_fmt_paise,
        fmt_pct=_fmt_pct,
        fmt_xirr=_fmt_xirr,
        fmt_alpha=_fmt_alpha,
    )


def render_yearly_report_pdf(html: str) -> bytes:
    import weasyprint

    return bytes(weasyprint.HTML(string=html).write_pdf())


__all__ = [
    "FundPerformance",
    "render_yearly_report_html",
    "render_yearly_report_pdf",
]
