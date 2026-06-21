import pytest

pytest_plugins = ("anyio",)


@pytest.fixture
def anyio_backend():
    return "asyncio"
