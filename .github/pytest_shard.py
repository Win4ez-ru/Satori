"""Run one disjoint group of test modules from pytest's normal collection."""

import sys

import pytest


class ModuleShard:
    def __init__(self, index: int, total: int) -> None:
        if not 0 <= index < total:
            raise ValueError("shard index outside matrix")
        self.index = index
        self.total = total

    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        paths = sorted({item.path for item in items})
        assigned = set(paths[self.index :: self.total])
        removed = [item for item in items if item.path not in assigned]
        items[:] = [item for item in items if item.path in assigned]
        config.hook.pytest_deselected(items=removed)


if __name__ == "__main__":
    shard = ModuleShard(int(sys.argv[1]), int(sys.argv[2]))
    raise SystemExit(pytest.main(sys.argv[3:], plugins=[shard]))
