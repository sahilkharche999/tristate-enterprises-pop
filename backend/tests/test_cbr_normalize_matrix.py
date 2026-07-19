"""Unit tests for pure min-max normalization in CBR (no sklearn)."""
import numpy as np

from app.ai_implementation.pipeline.cbr_engine import _normalize_matrix


def test_empty_matrix_passthrough():
    m = np.empty((0, 10))
    out = _normalize_matrix(m)
    assert out.shape == (0, 10)


def test_zero_range_column_becomes_zeros():
    m = np.array([[1.0, 5.0], [1.0, 10.0], [1.0, 15.0]])
    out = _normalize_matrix(m)
    # col 0 constant → zeros
    assert np.allclose(out[:, 0], 0.0)
    # col 1 scales 5..15 → 0..1
    assert np.allclose(out[:, 1], [0.0, 0.5, 1.0])
    assert not np.isnan(out).any()


def test_normal_columns_scale_to_unit_interval():
    m = np.array([[0.0, 2.0], [2.0, 4.0]])
    out = _normalize_matrix(m)
    assert np.allclose(out, [[0.0, 0.0], [1.0, 1.0]])
