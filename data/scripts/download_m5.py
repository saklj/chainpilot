"""Download the three required M5 competition files with a Kaggle access token."""

from __future__ import annotations

import json
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

COMPETITION = "m5-forecasting-accuracy"
API_ROOT = "https://www.kaggle.com/api/v1/competitions/data"
REQUIRED_FILES = (
    "sales_train_evaluation.csv",
    "calendar.csv",
    "sell_prices.csv",
)
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
TOKEN_PATH = Path.home() / ".kaggle" / "access_token"


def print_credential_help() -> None:
    """Print actionable setup instructions for Kaggle authentication failures."""
    print(
        "Kaggle 认证失败。请在 Kaggle Settings → API 中生成 access token，\n"
        "将纯文本 token 单独一行保存到 ~/.kaggle/access_token，并在 M5 竞赛页接受规则。\n"
        "也可以手动下载竞赛 zip，将 sales_train_evaluation.csv、calendar.csv、\n"
        "sell_prices.csv 解压到 data/raw/ 后重跑。",
        file=sys.stderr,
    )


def read_token() -> str:
    """Read and validate the one-line Kaggle access token."""
    if not TOKEN_PATH.is_file():
        print_credential_help()
        raise SystemExit(1)
    token = TOKEN_PATH.read_text(encoding="utf-8").strip()
    if not token:
        print_credential_help()
        raise SystemExit(1)
    return token


def request(url: str, token: str) -> urllib.response.addinfourl:
    """Open an authenticated Kaggle request and normalize authentication errors."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "ChainPilot/0.1",
        },
    )
    try:
        return urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            print_credential_help()
            raise SystemExit(1) from exc
        raise


def get_file_sizes(token: str) -> dict[str, int]:
    """Return expected uncompressed byte sizes from the competition file list."""
    url = f"{API_ROOT}/list/{COMPETITION}"
    with request(url, token) as response:
        payload: Any = json.load(response)
    entries = payload if isinstance(payload, list) else payload.get("files", [])
    sizes: dict[str, int] = {}
    for entry in entries:
        name = entry.get("name") or entry.get("ref")
        size = entry.get("totalBytes")
        if isinstance(name, str) and isinstance(size, int):
            sizes[Path(name).name] = size
    missing = [name for name in REQUIRED_FILES if name not in sizes]
    if missing:
        raise RuntimeError(f"Kaggle 文件清单缺少: {', '.join(missing)}")
    return sizes


def download_file(filename: str, expected_size: int, token: str) -> None:
    """Download one file, accepting either a zip response or a raw CSV response."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    target = RAW_DIR / filename
    if target.is_file() and target.stat().st_size == expected_size:
        print(f"跳过 {filename}（已存在且大小完整）")
        return

    encoded_name = urllib.parse.quote(filename)
    url = f"{API_ROOT}/download/{COMPETITION}/{encoded_name}"
    temporary = RAW_DIR / f".{filename}.download"
    with request(url, token) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)

    if zipfile.is_zipfile(temporary):
        with zipfile.ZipFile(temporary) as archive:
            matching = [member for member in archive.namelist() if Path(member).name == filename]
            if len(matching) != 1:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(f"下载包中未唯一找到 {filename}")
            with archive.open(matching[0]) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        temporary.unlink()
    else:
        temporary.replace(target)

    actual_size = target.stat().st_size
    if actual_size != expected_size:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"{filename} 大小校验失败: expected={expected_size}, actual={actual_size}"
        )
    print(f"下载完成 {filename}: {actual_size} bytes")


def main() -> None:
    """Download every required M5 input file idempotently."""
    token = read_token()
    sizes = get_file_sizes(token)
    for filename in REQUIRED_FILES:
        download_file(filename, sizes[filename], token)


if __name__ == "__main__":
    main()
