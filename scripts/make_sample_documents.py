"""Regenerate docs/sample_documents/*.pdf from the sample database (for the README / demos)."""
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    tmp = Path(tempfile.mkdtemp())
    (tmp / "data").mkdir()
    shutil.copy2(ROOT / "sample_data" / "sdj_sample.db", tmp / "data" / "sdj_mobile_care.db")
    os.environ["SDJ_DATA_DIR"] = str(tmp)
    from sdj import config
    from sdj.core import auth, closing, repairs, reports, sales
    from sdj.core.auth import ALL_PERMISSIONS, CurrentUser
    from sdj.db.init_db import open_database
    from sdj.output.pdf_docs import invoice_pdf, job_pdf
    from sdj.output.pdf_report import render_report_pdf
    open_database(config.db_path())
    auth.set_current_user(CurrentUser(1, "admin", "Shop Owner", "ADMIN", set(ALL_PERMISSIONS)))
    out = ROOT / "docs" / "sample_documents"
    out.mkdir(parents=True, exist_ok=True)
    rows = sales.list_sales()
    phone = next(r for r in rows if r["status"] == "COMPLETED" and any(l["imei"] for l in sales.get_sale(r["id"])["lines"]))
    d = sales.get_sale(phone["id"])
    invoice_pdf(d, "A4", out / "Invoice_A4_sample.pdf")
    invoice_pdf(d, "THERMAL_80", out / "Receipt_80mm_sample.pdf")
    invoice_pdf(sales.get_sale(rows[3]["id"]), "THERMAL_58", out / "Receipt_58mm_sample.pdf")
    job = repairs.list_jobs(statuses=["DELIVERED"])[0]["id"]
    job_pdf(repairs.get_job(job), "RECEIPT", "A4", out / "Service_Bill_A4_sample.pdf")
    job_pdf(repairs.get_job(job), "JOB_CARD", "THERMAL_80", out / "Job_Card_80mm_sample.pdf")
    render_report_pdf(closing.saved_report(date.today() - timedelta(days=1)), out / "Daily_Closing_Report_sample.pdf")
    t = date.today()
    render_report_pdf(reports.build_period_report(t.replace(day=1), t, "Monthly Report"), out / "Monthly_Report_sample.pdf")
    print("Sample documents written to", out)


if __name__ == "__main__":
    main()
