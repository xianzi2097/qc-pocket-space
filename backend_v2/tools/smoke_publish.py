"""M4 ③ vitepress 真实发布冒烟 —— 补规范 §8.5 标记的盲区

M2 的发布队列一直用 monkeypatch 假渲染测（pytest 管不到真实 Node 构建）。
本脚本在【临时数据区】建一门含 md 的课，调真实 _run_build（便携 Node +
runtime/vitepress-template），断言产物 index.html 真的落盘，跑完清理。

用法：cd backend_v2 && python tools/smoke_publish.py
真跑一次 = 规范 §8.5 的"M4 收口跑一次真实发布"。
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(tempfile.mkdtemp(prefix="qc_smoke_"))
os.environ["QC_V2_DATA_ROOT"] = str(_TMP / "data")
os.environ["QC_V2_DB"] = str(_TMP / "smoke.db")
os.environ["QC_DISABLE_AUTO_BACKUP"] = "1"

from app import config, publish          # noqa: E402  环境变量必须先于 import
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Asset, new_id, now_str  # noqa: E402


def main() -> int:
    config.ensure_dirs()
    init_db()

    # 1. 建一门含 md 的课（两级结构，验 sidebar 递归）
    asset_id = new_id()
    db = SessionLocal()
    try:
        db.add(Asset(id=asset_id, owner_id=new_id(), bucket="courses", name="冒烟课程",
                     description="smoke test", tags=[], type="scratch", format="folder",
                     publish={}, created_at=now_str()))
        db.commit()
    finally:
        db.close()

    root = config.DATA_ROOT / "assets" / asset_id
    root.mkdir(parents=True)                                # 直插库没走接口，手动备货
    (root / "第一章 入门.md").write_text("# 第一章\n\n冒烟：中文文件名 + 正文\n", encoding="utf-8")
    (root / "进阶").mkdir()
    (root / "进阶" / "第二章.md").write_text("# 第二章\n\n```python\nprint('smoke')\n```\n",
                                            encoding="utf-8")

    site = publish.SITES_DIR / asset_id
    log = publish.LOGS_DIR / f"{asset_id}.log"
    print(f"[smoke] 资产 {asset_id} 建好，开始真实渲染（Node + vitepress）...", flush=True)

    # 2. 真跑（失败会抛异常）
    try:
        publish._run_build(asset_id)

        # 3. 断言产物
        assert (site / "index.html").exists(), f"产物缺失: {site}"
        html = (site / "index.html").read_text(encoding="utf-8", errors="replace")
        assert "冒烟课程" in html, "首页没有课程名（VP_TITLE 没生效）"
        pages = list(site.rglob("*.html"))
        print(f"[smoke] 渲染成功：{len(pages)} 个 html 页面，产物 {site}", flush=True)
        if log.exists():
            tail = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:]
            safe = "\n  ".join(tail).encode("gbk", errors="replace").decode("gbk")
            print("[smoke] 构建日志尾部：\n  " + safe, flush=True)
        return 0
    finally:
        # 4. 清理：正式产物（这是一次性冒烟，不留在 frontend/）+ 临时数据区 + 日志
        shutil.rmtree(site, ignore_errors=True)
        shutil.rmtree(Path(str(site) + ".bak"), ignore_errors=True)
        log.unlink(missing_ok=True)
        shutil.rmtree(_TMP, ignore_errors=True)
        print("[smoke] 已清理临时产物与数据区", flush=True)


if __name__ == "__main__":
    sys.exit(main())
