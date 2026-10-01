"""PyInstaller one-folder build for Windows x86-64."""

from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve().parent
datas = [
    (str(project_root / "web" / "ui"), "web/ui"),
    (str(project_root / "config" / "tdms_rules.yaml"), "config"),
    (str(project_root / "config" / "wav_rules.yaml"), "config"),
    (str(project_root / "config" / "sample_profiles"), "config/sample_profiles"),
]
hiddenimports = collect_submodules("plotly.graph_objs") + [
    "librosa.feature.spectral",
    "librosa.core.spectrum",
    "librosa.filters",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.lifespan.on",
]

a = Analysis(
    [str(project_root / "desktop_launcher.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "torch", "tensorflow", "jax", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AI3-Audio-Labeling",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="AI3-Audio-Labeling",
)
