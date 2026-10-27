import importlib.util
import json
import zipfile
from pathlib import Path


def test_offline_package_excludes_demo_and_is_reproducible(tmp_path):
    path = Path(__file__).parents[1] / "tools/build_usage_report.py"
    spec = importlib.util.spec_from_file_location("build_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(tmp_path).read_bytes()
    second = module.build(tmp_path)
    assert first == second.read_bytes()
    with zipfile.ZipFile(second) as archive:
        assert set(archive.namelist()) == {*module.FILES, "report-module.json"}
        manifest = json.loads(archive.read("report-module.json"))
        assert manifest["host_contract"] == "jomooReportHost-v1"
        for name in module.FILES:
            text = archive.read(name).decode()
            assert "https://" not in text and "http://" not in text
            assert "Bearer " not in text
