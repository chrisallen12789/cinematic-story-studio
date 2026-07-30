# PyInstaller specification for the owned local-service executable.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

service_root = Path(SPECPATH)
source_root = service_root / "src"

hidden_imports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("lxml")
    + collect_submodules("pypdf")
    + collect_submodules("sqlalchemy.dialects.sqlite")
)

analysis = Analysis(
    [str(source_root / "cinematic_story_service" / "launcher.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="cinematic-story-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Electron starts this console subsystem executable with windowsHide and inherited pipes.
    # A console-capable bootloader is required for the authenticated stdin/stdout protocol.
    console=True,
)
