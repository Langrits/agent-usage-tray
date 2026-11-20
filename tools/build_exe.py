"""Windows용 실행 파일(폴더째 ZIP)을 만든다. Python이 없는 PC에서도 압축만 풀고 실행할 수 있다.

준비 (한 번만): 앱 실행 환경(.venv)과 따로, 빌드 전용 가상환경을 만든다
    python -m venv .venv-build
    .venv-build\\Scripts\\python.exe -m pip install -r requirements.txt pyinstaller
실행:
    .venv-build\\Scripts\\python.exe tools\\build_exe.py
결과:
    dist\\AI-Usage-Tray-<버전>-windows-x64.zip  (안에 "AI Usage Tray" 폴더: exe, 라이선스 안내)

한 파일짜리 exe 대신 폴더로 묶는다: 실행할 때마다 임시 폴더에 풀지 않아 빨리 켜지고, 백신 오탐도 덜하다.
"""
import glob
import importlib.metadata as md
import os
import platform
import re
import shutil
import struct
import sys
import zipfile

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")
NAME = "AI Usage Tray"
# exe에 함께 들어가는 패키지 (requirements.txt와 그 의존성). 라이선스 전문을 같이 넣는다
BUNDLED = ("pystray", "six", "pillow", "requests", "urllib3", "certifi", "idna", "charset-normalizer")


def version() -> str:
    text = open(os.path.join(ROOT, "agent_usage", "__init__.py"), encoding="utf-8").read()
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def arch() -> str:
    """빌드한 Python의 아키텍처 (exe도 같은 아키텍처로 만들어진다)."""
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return "x64" if struct.calcsize("P") == 8 else "x86"


def make_icon(path: str) -> None:
    """트레이 아이콘과 같은 모양(초록 둥근 사각형 + 글자)으로 exe 아이콘을 만든다."""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=56, fill="#0ca30c")
    try:
        font = ImageFont.truetype("segoeuib.ttf", 132)
    except OSError:
        font = ImageFont.load_default()
    d.text((size / 2, size / 2 + 4), "AI", fill="#ffffff", font=font, anchor="mm")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def write_version_file(path: str, ver: str) -> None:
    """exe 속성 창(자세히)에 보이는 이름·버전."""
    nums = (tuple(int(n) for n in re.findall(r"\d+", ver)) + (0, 0, 0, 0))[:4]
    info = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Langrits'),
      StringStruct('FileDescription', '{NAME}'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', '{NAME}'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', '{NAME}.exe'),
      StringStruct('ProductName', '{NAME}'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(info)


def write_notices(app_dir: str) -> None:
    """함께 들어간 Python·Tcl/Tk·패키지의 라이선스 전문과 목록. 꼭 있어야 할 라이선스를 못 찾으면 빌드를 멈춘다."""
    import PyInstaller
    lic_dir = os.path.join(app_dir, "licenses")
    os.makedirs(lic_dir, exist_ok=True)
    shutil.copy(os.path.join(ROOT, "LICENSE"), os.path.join(app_dir, "LICENSE.txt"))
    ver = version()
    lines = [f"{NAME} {ver} bundles the Python runtime and the packages below.",
             "Their license texts are in the 'licenses' folder.",
             f"Built with Python {sys.version.split()[0]} and PyInstaller {PyInstaller.__version__}"
             " by tools/build_exe.py.", ""]

    found = [p for p in (os.path.join(sys.base_prefix, n) for n in os.listdir(sys.base_prefix))
             if os.path.basename(p).upper().startswith("LICENSE") and os.path.isfile(p)]
    python_license = next((p for p in found if "Python Software Foundation" in
                           open(p, encoding="utf-8", errors="replace").read()), None)
    if not python_license:
        raise SystemExit(f"Python license not found in {sys.base_prefix}: not packaging without it")
    shutil.copy(python_license, os.path.join(lic_dir, "Python-LICENSE.txt"))
    lines.append(f"Python {sys.version.split()[0]} - PSF License (licenses/Python-LICENSE.txt)")

    import tkinter
    tcl_lib = tkinter.Tcl().eval("info library")       # 예: .../lib/tcl8.6. Tk 라이선스는 옆의 tk8.6 폴더에 있다
    parent = os.path.dirname(tcl_lib)
    # conda의 Python은 Tcl 라이선스를 패키지 정보 폴더(pkgs/tk-*/info/licenses/tcl*)에 둔다
    conda_tcl = sorted(glob.glob(os.path.join(sys.base_prefix, "pkgs", "tk-*", "info", "licenses", "tcl*", "license.terms")))
    terms = [("Tcl", os.path.join(tcl_lib, "license.terms"))] + [("Tcl", p) for p in conda_tcl[-1:]] + [
        ("Tk", os.path.join(parent, d, "license.terms")) for d in sorted(os.listdir(parent)) if d.lower().startswith("tk")]
    found_terms: dict[str, str] = {}
    for who, p in terms:
        if os.path.isfile(p):
            found_terms.setdefault(who, p)
    if not found_terms:
        raise SystemExit(f"Tcl/Tk license not found near {tcl_lib}: not packaging without it")
    for who, p in found_terms.items():                 # Tcl·Tk를 각각 넣는다
        shutil.copy(p, os.path.join(lic_dir, f"{who}-license.terms"))
        lines.append(f"{who} - BSD-style license (licenses/{who}-license.terms)")
    for who in {"Tcl", "Tk"} - set(found_terms):       # 한쪽만 찾았으면: Tcl과 Tk는 같은 조건으로 배포된다
        other = next(iter(found_terms))
        lines.append(f"{who} - same BSD-style license terms as {other} (licenses/{other}-license.terms)")

    for name in BUNDLED:
        dist = md.distribution(name)
        meta = dist.metadata
        lic = meta.get("License-Expression") or next(iter((meta.get("License") or "").splitlines()), "")
        urls = {u.split(",", 1)[0].strip().lower(): u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []}
        url = meta.get("Home-page") or next((urls[k] for k in ("source", "source code", "homepage", "repository", "code")
                                             if k in urls), next(iter(urls.values()), ""))
        copied = []
        for f in dist.files or []:
            base = os.path.basename(str(f)).upper()
            if any(key in base for key in ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
                src = dist.locate_file(f)
                if os.path.isfile(src):
                    dest = os.path.join(lic_dir, f"{meta['Name']}-{os.path.basename(str(f))}")
                    shutil.copy(src, dest)
                    copied.append("licenses/" + os.path.basename(dest))
        if not copied:
            raise SystemExit(f"No license file found for {meta['Name']}: not packaging without it")
        lines.append(f"{meta['Name']} {dist.version} - {lic or 'see license file'} {url}".rstrip()
                     + f" ({', '.join(copied)})")

    # pystray(LGPL v3): 들어간 버전의 소스를 그대로 넣고, 이 앱의 같은 버전 소스와 빌드 방법을 가리킨다
    pystray = md.distribution("pystray")
    src_dir = os.path.join(lic_dir, f"pystray-{pystray.version}-source")
    shutil.copytree(os.path.dirname(pystray.locate_file("pystray/__init__.py")), src_dir,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    lines += ["",
              f"pystray {pystray.version} is licensed under the LGPL v3. Its source code as bundled is in",
              f"licenses/pystray-{pystray.version}-source (also https://pypi.org/project/pystray/{pystray.version}/).",
              f"The source of this app at this version is https://github.com/Langrits/agent-usage-tray/tree/v{ver}",
              "and tools/build_exe.py there rebuilds this bundle, so pystray can be replaced with a modified version."]
    with open(os.path.join(app_dir, "THIRD-PARTY-NOTICES.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    import PyInstaller.__main__

    ver = version()
    os.makedirs(BUILD, exist_ok=True)
    icon, vfile = os.path.join(BUILD, "icon.ico"), os.path.join(BUILD, "version.txt")
    make_icon(icon)
    write_version_file(vfile, ver)
    PyInstaller.__main__.run([
        "--noconfirm", "--clean", "--windowed", "--name", NAME,
        "--icon", icon, "--version-file", vfile,
        "--hidden-import", "pystray._win32",          # pystray는 트레이 모듈을 실행 중에 불러온다
        "--distpath", DIST, "--workpath", os.path.join(BUILD, "work"), "--specpath", BUILD,
        os.path.join(ROOT, "run.pyw"),
    ])
    app_dir = os.path.join(DIST, NAME)
    write_notices(app_dir)
    zip_path = os.path.join(DIST, f"AI-Usage-Tray-{ver}-windows-{arch()}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, _, files in os.walk(app_dir):
            for name in files:
                full = os.path.join(folder, name)
                z.write(full, os.path.relpath(full, DIST))
    print(f"\nbuilt {zip_path} ({os.path.getsize(zip_path) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
