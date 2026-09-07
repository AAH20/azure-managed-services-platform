from __future__ import annotations

from html import escape
from pathlib import Path

from .models import Customer, WorkItem, Workload


def render_customer_report(
    customer: Customer,
    workloads: list[Workload],
    work_items: list[WorkItem],
    kpis: dict[str, object],
    destination: Path,
) -> None:
    cards = "".join(
        f'<div class="card"><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>'
        for label, value in (
            ("Resources", kpis["resources_evaluated"]),
            ("Open findings", kpis["findings"]),
            ("Ownership coverage", f'{kpis["ownership_coverage_pct"]}%'),
            ("Automatic changes", kpis["automatic_changes_executed"]),
        )
    )
    workload_rows = "".join(
        "<tr>"
        f"<td>{escape(item.name)}</td><td>{escape(item.owner)}</td>"
        f"<td>{escape(item.criticality)}</td><td>{len(item.resource_ids)}</td>"
        f"<td>${item.monthly_revenue_usd:,.0f}</td><td>{item.slo_pct:.2f}%</td>"
        "</tr>"
        for item in workloads
    )
    work_rows = "".join(
        "<tr>"
        f"<td>{escape(item.work_item_id)}</td><td>{escape(item.workload_id)}</td>"
        f"<td>{escape(item.proposal.control_id)}</td><td>{item.priority_score:.2f}</td>"
        f"<td>${item.estimated_monthly_exposure_usd:,.2f}</td>"
        f'<td><span class="status">{escape(item.status)}</span></td>'
        "</tr>"
        for item in work_items
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(customer.name)} Azure operations report</title>
<style>
:root{{--ink:#172033;--muted:#687386;--line:#dce2ea;--blue:#0067b8;--bg:#f5f7fa}}
*{{box-sizing:border-box}}body{{margin:0;font:15px system-ui;color:var(--ink);background:var(--bg)}}
main{{max-width:1180px;margin:auto;padding:42px 24px}}header{{display:flex;justify-content:space-between;gap:24px;align-items:end}}
h1{{font-size:34px;margin:0 0 8px}}p{{color:var(--muted);margin:0}}.badge{{background:#e8f2fb;color:#07558d;padding:8px 12px;border-radius:999px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}}.card{{background:white;border:1px solid var(--line);padding:18px;border-radius:10px}}
.card span{{display:block;color:var(--muted);font-size:13px}}.card strong{{display:block;font-size:28px;margin-top:7px}}
section{{background:white;border:1px solid var(--line);border-radius:10px;padding:22px;margin:18px 0;overflow:auto}}h2{{margin:0 0 16px}}
table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{text-align:left;padding:11px;border-bottom:1px solid var(--line)}}th{{font-size:12px;color:var(--muted);text-transform:uppercase}}
.status{{background:#fff4ce;color:#664d03;padding:4px 8px;border-radius:999px}}footer{{color:var(--muted);margin-top:20px;font-size:13px}}
@media(max-width:760px){{.cards{{grid-template-columns:1fr 1fr}}header{{display:block}}.badge{{display:inline-block;margin-top:14px}}}}
</style></head><body><main>
<header><div><h1>Azure managed operations</h1><p>{escape(customer.name)} · tenant {escape(customer.tenant_id)}</p></div><div class="badge">Synthetic evidence</div></header>
<div class="cards">{cards}</div>
<section><h2>Business workloads</h2><table><thead><tr><th>Workload</th><th>Owner</th><th>Criticality</th><th>Resources</th><th>Monthly revenue</th><th>SLO</th></tr></thead><tbody>{workload_rows}</tbody></table></section>
<section><h2>Prioritized work queue</h2><table><thead><tr><th>Work item</th><th>Workload</th><th>Control</th><th>Priority</th><th>Estimated exposure</th><th>Status</th></tr></thead><tbody>{work_rows}</tbody></table></section>
<footer>Generated from a synthetic fixture. Financial exposure is a prioritization estimate, not a guaranteed loss or saving. No cloud changes were executed. · <a href="https://a2zsoc.com">A2Z SOC</a></footer>
</main></body></html>"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
