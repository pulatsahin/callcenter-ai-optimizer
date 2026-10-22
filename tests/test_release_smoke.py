"""v1.0 release smoke checks; run from project root with pytest."""
from __future__ import annotations
import ast
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_python_syntax():
    ast.parse((ROOT / 'dashboard.py').read_text(encoding='utf-8'))


def test_dashboard_four_primary_tabs():
    source = (ROOT / 'dashboard.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    tabs = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == 'tabs']
    assert any(len(node.args) == 1 and isinstance(node.args[0], (ast.List, ast.Tuple))
               and len(node.args[0].elts) == 4 for node in tabs)


def test_dashboard_required_local_modules_exist():
    modules = [
        'agents/graph.py', 'database/init_db.py', 'services/capacity_engine.py',
        'services/observed_capacity.py', 'services/performance_variance.py',
        'services/operations_brief.py', 'services/port_optimizer.py',
        'services/retry_capacity.py', 'services/retry_policy.py',
        'services/retry_dispositions.py', 'services/outbound_importer.py',
        'services/schedule_planner.py',
    ]
    missing = [name for name in modules if not (ROOT / name).is_file()]
    assert not missing, f'Missing project modules: {missing}'


def test_example_csv_import():
    from services.outbound_importer import load_outbound_csv, summarize_outbound
    path = ROOT / 'data' / 'outbound_attempts_example.csv'
    records = load_outbound_csv(path)
    assert len(records) == 4
    summary = summarize_outbound(records)
    assert sum(row['attempts'] for row in summary) == 4


def test_holiday_calendars_exist():
    for year in (2026, 2027):
        assert (ROOT / 'data' / f'holidays_tr_{year}.json').is_file()
