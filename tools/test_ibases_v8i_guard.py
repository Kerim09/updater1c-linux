from pathlib import Path
from tempfile import TemporaryDirectory
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ibases_v8i_guard import repair_file


OLD = """[AccountingBase]
Connect=File="/base/accounting";
ID=11111111-1111-1111-1111-111111111111
OrderInList=100
Folder=/
OrderInTree=100
External=0

[PharmCorp]
Connect=File="/base/pharmcorp";
ID=22222222-2222-2222-2222-222222222222
OrderInList=200
Folder=/Рабочие
OrderInTree=200
External=0
"""

BROKEN = """[AccountingBase]
Connect=File="/base/accounting";
ID=aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa
OrderInList=-1
Folder=/
OrderInTree=16384
External=0

]
ID=450aed67-ee17-430a-a75f-6a926dac4888
OrderInList=-1
Folder=/
OrderInTree=16384
External=0

[PharmCorp]
Connect=File="/base/pharmcorp";
ID=bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb
OrderInList=-1
Folder=/
OrderInTree=32768
External=0

[NewBase]
Connect=File="/base/new";
ID=33333333-3333-3333-3333-333333333333
OrderInList=-1
Folder=/
OrderInTree=-1
External=0
"""


def main():
    with TemporaryDirectory() as td:
        p = Path(td) / "ibases.v8i"
        p.write_text(BROKEN, encoding="utf-8")

        changed = repair_file(p, old_snapshot=OLD.encode("utf-8"), make_backup=False)
        result = p.read_text(encoding="utf-8")

        assert changed is True
        assert "\n]\n" not in result
        assert "450aed67-ee17-430a-a75f-6a926dac4888" not in result

        assert "11111111-1111-1111-1111-111111111111" in result
        assert "22222222-2222-2222-2222-222222222222" in result
        assert "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa" not in result
        assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" not in result

        assert "[NewBase]" in result
        assert "33333333-3333-3333-3333-333333333333" in result

        print("OK: ibases_v8i_guard regression passed")


if __name__ == "__main__":
    main()
