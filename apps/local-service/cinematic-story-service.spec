# PyInstaller specification for the owned local-service executable.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

service_root = Path(SPECPATH)
source_root = service_root / "src"

# The speech adapter loads these fixed runtimes through importlib so they need
# explicit roots. Their normal PyInstaller hooks retain NumPy/ONNX Runtime
# native libraries. Only the English v1.0 dictionary data used by the governed
# en-US adapter is bundled; model and voice artifacts remain separately
# installed, verified application data and never enter the executable.
speech_runtime_hidden_imports = ["kokorog2p", "numpy", "onnxruntime"]
speech_runtime_data = (
    collect_data_files(
        "kokorog2p",
        includes=["data/kokoro_config.json", "en/data/us_*.json"],
    )
    + collect_data_files(
        "onnxruntime",
        includes=["LICENSE", "ThirdPartyNotices.txt"],
    )
    + copy_metadata("kokorog2p")
    + copy_metadata("numpy")
    + copy_metadata("onnxruntime")
)

hidden_imports = (
    collect_submodules("uvicorn")
    + collect_submodules("fastapi")
    + collect_submodules("lxml")
    + collect_submodules("pypdf")
    + collect_submodules("sqlalchemy.dialects.sqlite")
    + speech_runtime_hidden_imports
)

analysis = Analysis(
    [str(source_root / "cinematic_story_service" / "launcher.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[
        (
            str(
                source_root
                / "cinematic_story_service"
                / "catalogs"
                / "synthetic_voice_catalog.v1.json"
            ),
            "cinematic_story_service/catalogs",
        )
    ]
    + speech_runtime_data,
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
