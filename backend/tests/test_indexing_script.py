import importlib.util
import json
from pathlib import Path


def _load_script_module():
    """Load scripts/index_court_practice.py as a module (it is not a package)."""
    path = Path(__file__).resolve().parents[1] / "scripts" / "index_court_practice.py"
    spec = importlib.util.spec_from_file_location("index_court_practice_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_directory_region_code_from_subj(tmp_path):
    (tmp_path / "_catalog.json").write_text(
        json.dumps({"region": "Ленинградская область", "subj": "47"}), encoding="utf-8"
    )
    assert _load_script_module()._directory_region_code(tmp_path) == 47


def test_directory_region_code_from_region_name_when_no_subj(tmp_path):
    # Old-format catalog (Нижегородская) has no subj — resolved by region name.
    (tmp_path / "_catalog.json").write_text(
        json.dumps({"region": "Нижегородская область"}), encoding="utf-8"
    )
    assert _load_script_module()._directory_region_code(tmp_path) == 52


def test_directory_region_code_none_without_catalog(tmp_path):
    assert _load_script_module()._directory_region_code(tmp_path) is None


def test_directory_case_type_from_delo_table(tmp_path):
    (tmp_path / "_catalog.json").write_text(
        json.dumps(
            {"region": "Нижегородская область", "subj": 52, "category": "уголовные",
             "deloFilter": {"delo_table": "u1_case"}}
        ),
        encoding="utf-8",
    )
    assert _load_script_module()._directory_case_type(tmp_path) == "criminal"


def test_directory_case_type_none_without_catalog(tmp_path):
    assert _load_script_module()._directory_case_type(tmp_path) is None
