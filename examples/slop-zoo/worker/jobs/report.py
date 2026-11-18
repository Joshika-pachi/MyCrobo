"""Monthly sales report."""

import subprocess


def render_pdf(filename: str) -> str:
    # In a real app you would stream this to object storage instead of the local disk
    subprocess.run(f"convert {filename} /tmp/report.pdf", shell=True, check=True)
    return "/tmp/report.pdf"


def report_title() -> str:
    return "Lorem ipsum dolor sit amet"
