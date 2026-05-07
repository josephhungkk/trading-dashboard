import pytest


# Empty file is acceptable; auto-mode is set in pyproject.toml [tool.pytest.ini_options].
# If autouse markers are needed later, add here.
def pytest_configure(config: pytest.Config) -> None:
    setattr(config.option, "asyncio_mode", "auto")
