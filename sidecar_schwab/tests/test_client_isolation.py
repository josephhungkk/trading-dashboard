"""Phase 7a B3 -- M3: schwabdev MUST be confined to client.py only."""
from pathlib import Path


def test_only_client_py_imports_schwabdev():
    """grep ensures handlers.py / normalize.py / auth.py never import schwabdev."""
    pkg_root = Path(__file__).resolve().parent.parent
    forbidden = [
        "handlers.py",
        "normalize.py",
        "auth.py",
        "metrics.py",
        "main.py",
        "config.py",
    ]
    for fname in forbidden:
        path = pkg_root / fname
        if not path.exists():
            continue
        text = path.read_text()
        assert "import schwabdev" not in text, (
            f"{fname} must not import schwabdev (M3)"
        )
        assert "from schwabdev" not in text, (
            f"{fname} must not import schwabdev (M3)"
        )

    client_text = (pkg_root / "client.py").read_text()
    assert "schwabdev" in client_text, "client.py expected to import schwabdev"


def test_pyproject_pins_schwabdev_exact_version():
    pkg_root = Path(__file__).resolve().parent.parent
    pyproj = (pkg_root / "pyproject.toml").read_text()
    assert "schwabdev==3.0.3" in pyproj, (
        "schwabdev MUST be pinned to ==3.0.3 (M3)"
    )
