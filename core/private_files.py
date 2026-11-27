"""Private local operational storage; fail closed on permission errors."""
import os
from pathlib import Path
import subprocess


def private_directory(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        identity = subprocess.check_output(["whoami"], text=True).strip()
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r",
             f"{identity}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
            check=True, capture_output=True,
        )
    else:
        path.chmod(0o700)
    return path


def outside_uploads(path, roots):
    path = Path(path).resolve()
    for root in roots:
        root = Path(root).resolve()
        if path == root or path.is_relative_to(root) or root.is_relative_to(path):
            raise ValueError("运行目录与附件目录不能相同或互相包含。")
    return path
