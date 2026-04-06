"""Tax export service — generate CSV and PDF mining income reports."""

import csv
import io
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class TaxExportService:
    def __init__(self, history_svc):
        self.history_svc = history_svc

    def _query_data(self, start_date: str, end_date: str) -> list[dict]:
        """Query profit_snapshots grouped by date and miner for a date range.
        Uses AVG to collapse multiple snapshots per day into one daily figure."""
        conn = self.history_svc._get_conn()
        rows = conn.execute(
            """SELECT
                DATE(timestamp) as date,
                miner_id,
                miner_name,
                algorithm,
                best_coin,
                AVG(daily_revenue) as avg_revenue,
                AVG(daily_electricity) as avg_electricity,
                AVG(daily_profit) as avg_profit,
                COUNT(*) as snapshots_in_day
               FROM profit_snapshots
               WHERE DATE(timestamp) >= ? AND DATE(timestamp) <= ?
               GROUP BY DATE(timestamp), miner_id
               ORDER BY DATE(timestamp), miner_name""",
            (start_date, end_date),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def generate_csv(self, start_date: str, end_date: str) -> str:
        """Generate CSV string with daily mining income data."""
        rows = self._query_data(start_date, end_date)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Date", "Miner", "Algorithm", "Coin Mined",
            "Daily Revenue (USD)", "Electricity Cost (USD)", "Net Profit (USD)",
        ])

        for row in rows:
            writer.writerow([
                row["date"],
                row["miner_name"],
                row["algorithm"] or "",
                row["best_coin"] or "",
                f"{row['avg_revenue']:.2f}",
                f"{row['avg_electricity']:.2f}",
                f"{row['avg_profit']:.2f}",
            ])

        # Summary row
        total_revenue = sum(r["avg_revenue"] for r in rows)
        total_electricity = sum(r["avg_electricity"] for r in rows)
        total_profit = sum(r["avg_profit"] for r in rows)
        writer.writerow([])
        writer.writerow([
            "TOTAL", "", "", "",
            f"{total_revenue:.2f}",
            f"{total_electricity:.2f}",
            f"{total_profit:.2f}",
        ])

        return output.getvalue()

    def generate_pdf(self, start_date: str, end_date: str) -> bytes:
        """Generate PDF report with mining income data."""
        from fpdf import FPDF

        rows = self._query_data(start_date, end_date)

        total_revenue = sum(r["avg_revenue"] for r in rows)
        total_electricity = sum(r["avg_electricity"] for r in rows)
        total_profit = sum(r["avg_profit"] for r in rows)

        # Count unique days
        unique_days = len(set(r["date"] for r in rows))
        unique_miners = len(set(r["miner_id"] for r in rows))

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()

        # Title
        pdf.set_font("Helvetica", "B", 18)
        pdf.cell(0, 12, "Mining Income Tax Report", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(4)

        # Date range
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 8, f"Period: {start_date} to {end_date}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 8, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.ln(8)

        # Summary table
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Summary", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        pdf.set_font("Helvetica", "", 10)
        summary_data = [
            ("Total Days Tracked", str(unique_days)),
            ("Total Miners", str(unique_miners)),
            ("Total Revenue", f"${total_revenue:,.2f}"),
            ("Total Electricity Cost", f"${total_electricity:,.2f}"),
            ("Total Net Profit", f"${total_profit:,.2f}"),
            ("Avg Daily Profit", f"${total_profit / unique_days:,.2f}" if unique_days > 0 else "$0.00"),
        ]

        col_w = [80, 60]
        for label, value in summary_data:
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(col_w[0], 7, label, border=1)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(col_w[1], 7, value, border=1, new_x="LMARGIN", new_y="NEXT")

        pdf.ln(10)

        # Detailed daily table
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 10, "Daily Breakdown", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # Table header
        headers = ["Date", "Miner", "Algorithm", "Coin", "Revenue", "Elec. Cost", "Net Profit"]
        col_widths = [24, 38, 28, 22, 26, 26, 26]

        pdf.set_font("Helvetica", "B", 8)
        pdf.set_fill_color(230, 230, 230)
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 7, h, border=1, fill=True)
        pdf.ln()

        # Table rows
        pdf.set_font("Helvetica", "", 7.5)
        for row in rows:
            # Check if we need a new page
            if pdf.get_y() > 265:
                pdf.add_page()
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_fill_color(230, 230, 230)
                for i, h in enumerate(headers):
                    pdf.cell(col_widths[i], 7, h, border=1, fill=True)
                pdf.ln()
                pdf.set_font("Helvetica", "", 7.5)

            values = [
                row["date"],
                row["miner_name"][:18],
                (row["algorithm"] or "")[:14],
                (row["best_coin"] or "")[:10],
                f"${row['avg_revenue']:.2f}",
                f"${row['avg_electricity']:.2f}",
                f"${row['avg_profit']:.2f}",
            ]
            for i, v in enumerate(values):
                pdf.cell(col_widths[i], 6, v, border=1)
            pdf.ln()

        # Totals row
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_widths[0] + col_widths[1] + col_widths[2] + col_widths[3], 7, "TOTAL", border=1)
        pdf.cell(col_widths[4], 7, f"${total_revenue:,.2f}", border=1)
        pdf.cell(col_widths[5], 7, f"${total_electricity:,.2f}", border=1)
        pdf.cell(col_widths[6], 7, f"${total_profit:,.2f}", border=1)
        pdf.ln()

        # Footer
        pdf.ln(10)
        pdf.set_font("Helvetica", "I", 8)
        pdf.cell(0, 6, "This report is generated from estimated profitability data and should be used for reference only.", new_x="LMARGIN", new_y="NEXT", align="C")
        pdf.cell(0, 6, "Consult a tax professional for official tax filings.", new_x="LMARGIN", new_y="NEXT", align="C")

        return pdf.output()
