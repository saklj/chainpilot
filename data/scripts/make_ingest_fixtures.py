"""Generate deterministic Excel fixtures for M6-T6b ingest acceptance and demos.

Five files under data/fixtures/ingest/ (all keys reference real seed data):
1. 样例_历史整理版.xlsx   — template-registration sample, Chinese aliases the
                            deterministic matcher must hit
2. 样例_怪列名.xlsx       — odd column names that force the LLM suggestion path
3. 导入_正常批次.xlsx     — 8 clean new POs (unique ids, valid keys, future ETAs)
4. 导入_含坏行.xlsx       — 3 valid rows mixed with every rejection class:
                            in-file duplicate, existing po_id conflict, unknown
                            material, unknown supplier, negative qty, bad date,
                            empty po_id
5. 导入_超行数.xlsx       — 50,001 rows to trip the row-count limit

No randomness is used, so re-running reproduces identical content.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "data" / "fixtures" / "ingest"

TEMPLATE_HEADERS = ["采购单号", "物料号", "供应商", "数量", "预计到货日"]
WEIRD_HEADERS = ["PO编号#", "料件Code", "供货方", "订购Qty", "到货ETA"]


def _write(name: str, headers: list[str], rows: list[list[object]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT_DIR / name)
    print(f"{name}: {len(rows)} data rows")


def main() -> None:
    history = [
        ["PO-000001", "PN-00297", "SUP-006", 2805, date(2016, 9, 14)],
        ["PO-000002", "PN-00112", "SUP-022", 1877, date(2016, 8, 11)],
        ["PO-000003", "PN-00166", "SUP-037", 2610, date(2016, 7, 28)],
        ["PO-000777", "PN-00001", "SUP-001", 1200, date(2016, 6, 30)],
        ["PO-000778", "PN-00002", "SUP-002", 950, date(2016, 7, 5)],
        ["PO-000779", "PN-00003", "SUP-003", 400, date(2016, 7, 12)],
    ]
    _write("样例_历史整理版.xlsx", TEMPLATE_HEADERS, history)
    _write("样例_怪列名.xlsx", WEIRD_HEADERS, history[:3])

    clean = [
        [f"PO-9000{index:02d}", pn, sup, qty, eta]
        for index, (pn, sup, qty, eta) in enumerate(
            [
                ("PN-00001", "SUP-001", 800, date(2016, 6, 5)),
                ("PN-00002", "SUP-002", 1500, date(2016, 6, 12)),
                ("PN-00003", "SUP-003", 600, date(2016, 6, 19)),
                ("PN-00004", "SUP-004", 300, date(2016, 6, 26)),
                ("PN-00005", "SUP-001", 2200, date(2016, 7, 3)),
                ("PN-00006", "SUP-002", 450, date(2016, 7, 10)),
                ("PN-00001", "SUP-002", 900, date(2016, 7, 17)),
                ("PN-00002", "SUP-003", 1100, date(2016, 7, 24)),
            ],
            start=1,
        )
    ]
    _write("导入_正常批次.xlsx", TEMPLATE_HEADERS, clean)

    dirty = [
        ["PO-910001", "PN-00001", "SUP-001", 500, date(2016, 6, 8)],  # 合法
        ["PO-910002", "PN-00002", "SUP-002", 750, date(2016, 6, 15)],  # 合法
        ["PO-910002", "PN-00003", "SUP-003", 320, date(2016, 6, 22)],  # 文件内重复 po_id
        ["PO-000001", "PN-00004", "SUP-004", 640, date(2016, 6, 29)],  # 与库内现有 po_id 冲突
        ["PO-910005", "PN-99999", "SUP-001", 480, date(2016, 7, 6)],  # 未知物料
        ["PO-910006", "PN-00005", "SUP-999", 510, date(2016, 7, 13)],  # 未知供应商
        ["PO-910007", "PN-00006", "SUP-002", -30, date(2016, 7, 20)],  # 负数量
        ["PO-910008", "PN-00001", "SUP-003", 260, "下周三"],  # 坏日期
        ["", "PN-00002", "SUP-004", 380, date(2016, 8, 3)],  # 空 po_id
        ["PO-910010", "PN-00003", "SUP-001", 940, date(2016, 8, 10)],  # 合法
    ]
    _write("导入_含坏行.xlsx", TEMPLATE_HEADERS, dirty)

    oversize = [
        [f"PO-95{index:05d}", "PN-00001", "SUP-001", 100, date(2016, 8, 1)]
        for index in range(1, 50002)
    ]
    _write("导入_超行数.xlsx", TEMPLATE_HEADERS, oversize)


if __name__ == "__main__":
    main()
