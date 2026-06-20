"""Smoke test: the package imports and pydynantic is available."""

from __future__ import annotations


def test_package_imports() -> None:
    import hodlbook

    assert hodlbook.__version__


def test_pydynantic_available() -> None:
    import pydynantic

    # hodlbook is built on pydynantic's single-table primitives.
    assert hasattr(pydynantic, "Table")
    assert hasattr(pydynantic, "Entity")


def test_public_api_exports() -> None:
    import hodlbook

    for name in (
        "Repository",
        "build_table",
        "create_table",
        "build_models",
        "Models",
        "Side",
        "Direction",
    ):
        assert hasattr(hodlbook, name), name
