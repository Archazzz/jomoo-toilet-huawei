"""Package the original report module, excluding demo data and vendor resources."""

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "web" / "usage-report"
FILES = ("index.html", "report.css", "report.js", "native.js")


def build(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {
        "module": "jomoo-usage-report",
        "version": 1,
        "entry": "index.html",
        "host_contract": "jomooReportHost-v1",
        "publication": "requires-authorized-product-host-and-platform-review",
        "files": {},
    }
    archive = destination / "jomoo-usage-report.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in FILES:
            data = (ASSETS / name).read_bytes()
            if len(data) > 2 * 1024 * 1024:
                raise ValueError(f"模块文件过大：{name}")
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, data)
        bundle.writestr(
            zipfile.ZipInfo("report-module.json", date_time=(2026, 1, 1, 0, 0, 0)),
            json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
        )
    return archive


if __name__ == "__main__":
    print(build(ROOT / "dist"))
