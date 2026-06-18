from pathlib import Path

import pytest

from stackowl.commands.config_helpers import load_yaml, save_yaml


def test_save_yaml_round_trips(tmp_path: Path):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"owls": [{"name": "a"}]})
    assert load_yaml(p) == {"owls": [{"name": "a"}]}


def test_save_yaml_leaves_no_temp_files(tmp_path: Path):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"k": 1})
    assert [f.name for f in tmp_path.iterdir()] == ["x.yaml"]


def test_save_yaml_existing_file_not_truncated_on_serialize_error(tmp_path: Path, monkeypatch):
    p = tmp_path / "x.yaml"
    save_yaml(p, {"good": 1})

    class Unrepresentable:
        pass

    with pytest.raises(Exception):
        save_yaml(p, {"bad": Unrepresentable()})
    assert load_yaml(p) == {"good": 1}
