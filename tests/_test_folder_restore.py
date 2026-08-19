# -*- coding: utf-8 -*-
"""验证文件夹恢复（两种结构），无 PySide6 环境"""
import os
import sys
import json
import shutil

sys.path.insert(0, r"c:\Arin - cabin\QC-PocketSpace\labs_dev\qx-launcher")
from app.backup_core import restore_from_folder  # noqa: E402

ROOT = r"c:\Arin - cabin\QC-PocketSpace\qc-pocket-space\tests\_frestore_test"
SRC_DATA = r"c:\Arin - cabin\QC-PocketSpace\data"
assert "PySide6" not in sys.modules

def make_backup_folder(dest, wrap):
    """造一个备份文件夹：wrap=True 套 data/，wrap=False 直接是内容"""
    if wrap:
        os.makedirs(dest)
        shutil.copytree(SRC_DATA, os.path.join(dest, "data"))
    else:
        shutil.copytree(SRC_DATA, dest)
    return dest

def run_case(name, src_folder):
    print(f"\n=== 场景: {name} ===")
    case_root = os.path.join(ROOT, name)
    if os.path.exists(case_root):
        shutil.rmtree(case_root)
    os.makedirs(case_root)
    test_data = os.path.join(case_root, "data")
    backup_root = os.path.join(case_root, "backups")

    shutil.copytree(SRC_DATA, test_data)
    cp = os.path.join(test_data, "users", "teachers", "1", "classes.json")
    with open(cp, "r", encoding="utf-8") as f:
        classes = json.load(f)
    orig = classes[0]["name"]
    classes[0]["name"] = "被篡改"
    with open(cp, "w", encoding="utf-8") as f:
        json.dump(classes, f, ensure_ascii=False)

    ok, msg = restore_from_folder(src_folder, test_data, backup_root)
    assert ok, f"恢复失败: {msg}"
    with open(cp, "r", encoding="utf-8") as f:
        classes = json.load(f)
    assert classes[0]["name"] == orig, f"班级名未还原: {classes[0]['name']}"
    baks = [d for d in os.listdir(case_root) if d.startswith("data.bak")]
    assert baks, "保护目录未生成"
    print(f"✅ 班级名还原: {classes[0]['name']}；保护: {baks}")

def main():
    if os.path.exists(ROOT):
        shutil.rmtree(ROOT)
    os.makedirs(ROOT)

    # 场景 A：文件夹直接是 data 内容
    run_case("direct", make_backup_folder(os.path.join(ROOT, "bk_direct"), wrap=False))
    # 场景 B：文件夹套了一层 data/
    run_case("wrapped", make_backup_folder(os.path.join(ROOT, "bk_wrapped"), wrap=True))
    # 场景 C：无效文件夹
    bad = os.path.join(ROOT, "bad")
    os.makedirs(bad)
    ok, msg = restore_from_folder(bad, os.path.join(ROOT, "x", "data"), os.path.join(ROOT, "x", "backups"))
    assert not ok, "无效文件夹不应恢复成功"
    print(f"\n✅ 无效文件夹被正确拒绝: {msg}")

    print("\n🎉 文件夹恢复全部通过（直接内容 / 套data/ / 无效拒绝）")

if __name__ == "__main__":
    main()
