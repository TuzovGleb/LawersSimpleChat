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


def _write_page(path, case_number="02-1/2024"):
    path.write_text(
        json.dumps({"courtName": "Суд", "vnkod": "77RS0006",
                    "cases": [{"caseNumber": case_number, "actText": "Решение"}]}),
        encoding="utf-8",
    )


def test_week_filter_keeps_only_in_range_and_drops_unweeked(tmp_path):
    mod = _load_script_module()
    court = tmp_path / "court"; court.mkdir()
    _write_page(court / "page-227003.json")   # week 227 — keep
    _write_page(court / "page-330001.json")   # week 330 — keep
    _write_page(court / "page-226009.json")   # week 226 — drop (below)
    _write_page(court / "page-331001.json")   # week 331 — drop (above)
    _write_page(court / "page-0001.json")     # legacy, no week — drop
    docs = list(mod.iter_cases_from_directory(court, case_type="civil", week_from=227, week_to=330))
    assert len(docs) == 2
    assert all(d["case_type"] == "civil" and d["region_code"] == 77 for d in docs)


def test_file_week_parsing():
    mod = _load_script_module()
    from pathlib import Path
    assert mod._file_week(Path("x/page-227003.json")) == 227
    assert mod._file_week(Path("x/page-0001.json")) is None
