"""尺寸表 / 尺寸推断测试。"""

from __future__ import annotations

import json

import pytest

from logobuilder.core import LogoSize, SizeCandidate, SizeDB, guess_sizes_for_blocks
from logobuilder.core.sizes import builtin_sizes, user_sizes_path


# ----------------------------------------------------------------------
# LogoSize
# ----------------------------------------------------------------------
def test_logo_size_basic():
    size = LogoSize(540, 960, 4)
    assert size.key == 540 * 960 * 4
    assert size.key == 2_073_600
    assert size.bpp == 4
    assert size.color_depth == 4
    assert size.pixel_count == 518_400
    assert size.aspect == pytest.approx(540 / 960)
    assert str(size) == "540x960x4"
    assert LogoSize(1, 2).color_depth == 2


@pytest.mark.parametrize(
    "width,height,depth",
    [(0, 10, 2), (10, 0, 2), (-1, 10, 2), (10, 10, 3), (10, 10, 1)],
)
def test_logo_size_invalid(width, height, depth):
    with pytest.raises(ValueError):
        LogoSize(width, height, depth)


def test_logo_size_parse():
    assert LogoSize.parse("540x960x4") == LogoSize(540, 960, 4)
    assert LogoSize.parse(" 540x960 ") == LogoSize(540, 960, 2)
    assert LogoSize.parse("540X960X2") == LogoSize(540, 960, 2)
    assert LogoSize.parse("540*960") == LogoSize(540, 960, 2)
    with pytest.raises(ValueError):
        LogoSize.parse("")
    with pytest.raises(ValueError):
        LogoSize.parse("540x960x3")
    with pytest.raises(ValueError):
        LogoSize.parse("abc")


def test_logo_size_from_key():
    candidates = LogoSize.from_key(2_073_600)
    assert LogoSize(540, 960, 4) in candidates
    assert any(c.color_depth == 2 for c in candidates)
    assert LogoSize.from_key(0) == []
    # 所有候选的 key 必须等于输入
    assert all(c.key == 2_073_600 for c in candidates)


# ----------------------------------------------------------------------
# SizeDB 基础
# ----------------------------------------------------------------------
def test_default_db_loads_49_builtin():
    db = SizeDB.default()
    builtin = builtin_sizes()
    assert len(builtin) == 49
    assert len(db) >= len(builtin)
    assert LogoSize(540, 960, 4) in db
    assert LogoSize(38, 54, 4) in db
    assert LogoSize(720, 1280, 4) in db


def test_db_add_and_exact():
    db = SizeDB([LogoSize(100, 200, 2)])
    assert db.exact(100 * 200 * 2) == [LogoSize(100, 200, 2)]
    db.add(LogoSize(100, 200, 2))  # 重复不增加
    assert len(db) == 1
    db.add(LogoSize(200, 100, 2))  # 同 key 的另一个尺寸
    assert len(db.exact(100 * 200 * 2)) == 2
    assert LogoSize(100, 200, 2) in db
    assert LogoSize(300, 300, 2) not in db
    assert "not-a-size" not in db
    assert db.exact(-1) == []


def test_db_default_exact_matches_real_block_lengths(default_db):
    # 真实 logo.bin 用到的解压长度都应能唯一命中
    lengths = [
        2_073_600,
        8_208,
        10_368,
        12_960,
        540,
        3_686_400,
    ]
    for length in lengths:
        exact = default_db.exact(length)
        assert len(exact) == 1, f"length {length} -> {exact}"


# ----------------------------------------------------------------------
# 候选推断
# ----------------------------------------------------------------------
def test_candidates_exact_score(default_db):
    candidates = default_db.candidates(2_073_600)
    assert candidates[0].size == LogoSize(540, 960, 4)
    assert candidates[0].score == pytest.approx(1.0)
    assert "精确匹配" in candidates[0].reason


def test_candidates_ranking_desc(default_db):
    candidates = default_db.candidates(8_208)
    assert candidates
    scores = [candidate.score for candidate in candidates]
    assert scores == sorted(scores, reverse=True)


def test_candidates_neighbor_consistency():
    db = SizeDB()
    neighbor = LogoSize(540, 960, 4)
    # 与相邻块 key 完全相同 -> 0.9
    candidates = db.candidates(neighbor.key, neighbors=[neighbor])
    assert candidates
    top = candidates[0]
    assert top.size == neighbor
    assert top.score == pytest.approx(0.9)
    assert "相邻块" in top.reason
    # 宽高相同、仅色深不同 -> 0.85
    candidates = db.candidates(540 * 960 * 2, neighbors=[neighbor])
    assert candidates[0].size == LogoSize(540, 960, 2)
    assert candidates[0].score == pytest.approx(0.85)


def test_candidates_aspect_hint():
    db = SizeDB()
    length = 1080 * 1920 * 2
    candidates = db.candidates(length, aspect_hint=1080 / 1920)
    assert candidates
    assert candidates[0].size == LogoSize(1080, 1920, 2)
    assert candidates[0].score >= 0.6


def test_candidates_unknown_length_returns_candidates():
    db = SizeDB()
    # 一个不在内置表的长度：1000 * 700 * 2
    length = 1_400_000
    candidates = db.candidates(length)
    assert candidates
    assert all(isinstance(c, SizeCandidate) for c in candidates)
    assert candidates[0].size.key == length


def test_candidates_invalid_input(default_db):
    assert default_db.candidates(0) == []
    assert default_db.candidates(-5) == []


def test_guess_sizes_for_blocks(default_db):
    lengths = [2_073_600, 2_073_600, 8_208]
    result = guess_sizes_for_blocks(lengths, default_db)
    assert len(result) == 3
    assert result[0][0].size == LogoSize(540, 960, 4)
    assert result[2][0].size == LogoSize(38, 54, 4)
    assert guess_sizes_for_blocks([], default_db) == []
    # 不传 db 时也能工作（用内置表）
    assert guess_sizes_for_blocks([2_073_600])[0][0].size == LogoSize(540, 960, 4)


def test_guess_sizes_neighbor_vote_for_unknown():
    db = SizeDB([LogoSize(540, 960, 4)])
    unknown = 540 * 960 * 2
    result = guess_sizes_for_blocks([540 * 960 * 4, unknown], db)
    assert result[1]
    assert result[1][0].size == LogoSize(540, 960, 2)
    assert result[1][0].score >= 0.85


# ----------------------------------------------------------------------
# 用户配置持久化
# ----------------------------------------------------------------------
def test_user_save_and_load(tmp_path):
    path = tmp_path / "user_sizes.json"
    db = SizeDB()
    db.add(LogoSize(111, 222, 2))
    db.add(LogoSize(333, 444, 4))
    saved = db.save_user(path)
    assert saved == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == "logobuilder-sizes"
    assert "111x222x2" in payload["sizes"]

    loaded = SizeDB.load_user(path)
    assert LogoSize(111, 222, 2) in loaded
    assert LogoSize(333, 444, 4) in loaded
    assert len(loaded) == 2


def test_user_load_text_format(tmp_path):
    path = tmp_path / "sizes.txt"
    path.write_text("540x960x4\r\n38x54x2\r\n# comment\r\nbadline\r\n", encoding="utf-8")
    db = SizeDB.load_user(path)
    assert LogoSize(540, 960, 4) in db
    assert LogoSize(38, 54, 2) in db
    assert len(db) == 2


def test_user_load_missing_or_broken(tmp_path):
    assert len(SizeDB.load_user(tmp_path / "missing.json")) == 0
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert len(SizeDB.load_user(broken)) == 0


def test_default_uses_config_env(isolated_config_dir):
    db = SizeDB.default()
    db.add(LogoSize(1200, 2000, 2))
    db.save_user()
    assert user_sizes_path().parent == isolated_config_dir
    assert LogoSize(1200, 2000, 2) in SizeDB.load_user()
    # default() 会把用户增量合并进来
    assert LogoSize(1200, 2000, 2) in SizeDB.default()


def test_size_candidate_validation():
    with pytest.raises(ValueError):
        SizeCandidate(LogoSize(1, 2), 1.5, "x")
    with pytest.raises(ValueError):
        SizeCandidate(LogoSize(1, 2), 0.5, "")
