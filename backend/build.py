"""电子书渲染引擎：用便携 Node + vitepress 模板把课程 md 渲染成静态站点

设计要点：
- 全局串行（一次只渲染一门课），避免 Node build 并发争抢模板目录
- 渲染在模板目录内进行（node_modules 只存在一处，无需逐课拷贝 323MB）
- 成功后才原子替换到 course_vitepress_dist；失败则旧产物原样保留
- 日志落盘 data/build_logs/，状态写回 course.json 的 publish 字段
"""
import os
import json
import shutil
import subprocess
import threading
from datetime import datetime

from utils import _read_course_meta, _write_course_meta, _natural_sort_key, _read_order_file, check_order_file

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
QC_DIR = os.path.abspath(os.path.join(BACKEND_DIR, ".."))            # qc-pocket-space
RUNTIME_DIR = os.path.join(QC_DIR, "runtime")
TEMPLATE_DIR = os.path.join(RUNTIME_DIR, "vitepress-template")
NODE_EXE = os.path.join(RUNTIME_DIR, "node", "node.exe")
SITES_DIR = os.path.join(QC_DIR, "frontend", "course_vitepress_dist")
LOGS_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "build_logs"))

_BUILD_TIMEOUT = 300  # 秒（服务器性能弱，vitepress 渲染可能较慢，放宽到 5 分钟）

_build_lock = threading.Lock()   # 全局串行：一次只渲染一门课
_active = {}                     # course_id -> "building"


def queue_publish(teacher_key: str, course_id: str, course_path: str) -> dict:
    """入队后台渲染，立即返回。若该课已在渲染则直接返回 building。"""
    if _active.get(course_id) == "building":
        return {"status": "building", "message": "正在渲染中"}
    _active[course_id] = "building"
    threading.Thread(
        target=_build_worker, args=(teacher_key, course_id, course_path), daemon=True
    ).start()
    return {"status": "building", "message": "已开始渲染"}


def get_active_status(course_id: str) -> dict:
    """返回内存中的实时状态（building），渲染结束返回 None"""
    st = _active.get(course_id)
    return {"status": st} if st else None


def remove_site(teacher_key: str, course_id: str) -> None:
    """删除课程时清理渲染产物与日志"""
    final_dir = os.path.join(SITES_DIR, course_id)
    shutil.rmtree(final_dir, ignore_errors=True)
    shutil.rmtree(final_dir + ".bak", ignore_errors=True)
    log_path = os.path.join(LOGS_DIR, f"{teacher_key}_{course_id}.log")
    if os.path.exists(log_path):
        try:
            os.remove(log_path)
        except OSError:
            pass


def _clear_workspace_docs() -> None:
    """清空模板 docs/（保留 .vitepress），用于构建前后清理临时拷贝"""
    docs_dir = os.path.join(TEMPLATE_DIR, "docs")
    if not os.path.isdir(docs_dir):
        return
    for name in os.listdir(docs_dir):
        if name == ".vitepress":
            continue
        p = os.path.join(docs_dir, name)
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)


def _ensure_index(docs_dir: str, title: str, description: str = "") -> None:
    """课程根目录没有 index.md 时自动生成一个首页（课程名 + 描述 + 顶层条目）"""
    if os.path.exists(os.path.join(docs_dir, "index.md")):
        return
    lines = [f"# {title}"]
    if description.strip():
        lines += ["", description.strip()]
    lines.append("")
    for name in sorted(os.listdir(docs_dir)):
        if name.startswith(".") or name == "course.json":
            continue
        p = os.path.join(docs_dir, name)
        if os.path.isdir(p):
            if os.path.exists(os.path.join(p, "index.md")):
                lines.append(f"- [{name}](/{name}/)")
            else:
                lines.append(f"- 📁 {name}")
        elif name.endswith(".md"):
            lines.append(f"- [{name[:-3]}](/{name[:-3]})")
    if len(lines) == 1:
        lines.append("")
        lines.append("本课程暂无内容，请上传 md 文件。")
    with open(os.path.join(docs_dir, "index.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _build_worker(teacher_key: str, course_id: str, course_path: str) -> None:
    with _build_lock:  # 串行
        try:
            _do_build(teacher_key, course_id, course_path)
        except Exception:
            # _do_build 内部已兜底，这里只是双保险
            pass
        finally:
            _active.pop(course_id, None)


def _do_build(teacher_key: str, course_id: str, course_path: str) -> None:
    log_path = os.path.join(LOGS_DIR, f"{teacher_key}_{course_id}.log")
    os.makedirs(LOGS_DIR, exist_ok=True)
    logf = open(log_path, "w", encoding="utf-8")

    def log(msg: str) -> None:
        try:
            print(msg, flush=True)
        except (UnicodeEncodeError, OSError):
            pass  # 控制台编码不支持某些字符（如 ✓）时忽略控制台输出，日志文件仍完整写入
        logf.write(msg + "\n")
        logf.flush()

    meta = _read_course_meta(course_path)
    publish = meta.get("publish", {})
    publish.update({
        "status": "building", "log": log_path,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    meta["publish"] = publish
    _write_course_meta(course_path, meta)

    try:
        docs_dir = os.path.join(TEMPLATE_DIR, "docs")
        vp_dir = os.path.join(docs_dir, ".vitepress")
        if not os.path.isdir(vp_dir):
            raise RuntimeError(f"模板缺失: {vp_dir}")
        if not os.path.exists(NODE_EXE):
            raise RuntimeError(f"便携 Node 缺失: {NODE_EXE}")

        # 1. 清空 docs，但保留 .vitepress（配置 + 主题 + gen）
        _clear_workspace_docs()

        # 2. 拷贝课程内容（排除 course.json 与隐藏文件）
        for name in os.listdir(course_path):
            if name == "course.json" or name.startswith("."):
                continue
            src = os.path.join(course_path, name)
            dst = os.path.join(docs_dir, name)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        # 2.1 若课程根目录只有一个子目录、没有其他文件，压平一层
        top_entries = [n for n in os.listdir(docs_dir) if not n.startswith(".")]
        top_dirs = [n for n in top_entries if os.path.isdir(os.path.join(docs_dir, n))]
        top_files = [n for n in top_entries if os.path.isfile(os.path.join(docs_dir, n))]
        if len(top_dirs) == 1 and len(top_files) == 0:
            sole = os.path.join(docs_dir, top_dirs[0])
            for child in os.listdir(sole):
                shutil.move(os.path.join(sole, child), os.path.join(docs_dir, child))
            shutil.rmtree(sole)
            log(f"📚 压平顶层目录: {top_dirs[0]}/")

        # 2.5 无首页时自动生成 index.md（否则 vitepress 不产 index.html）
        _ensure_index(docs_dir, meta.get("name") or "课程", meta.get("description", ""))

        # 3. 生成 sidebar（读 _order.json 支持自定义排序）
        order_map = _read_order_file(course_path)
        order_warn = check_order_file(course_path)
        if order_warn:
            log(f"⚠ {order_warn}")
        sidebar = _build_sidebar(docs_dir, order_map=order_map)
        gen_dir = os.path.join(vp_dir, "gen")
        os.makedirs(gen_dir, exist_ok=True)
        with open(os.path.join(gen_dir, "sidebar.mjs"), "w", encoding="utf-8") as f:
            f.write("export const sidebar = " + json.dumps(sidebar, ensure_ascii=False, indent=2) + "\n")

        # 4. 跑 build
        env = dict(os.environ)
        # URL 前缀只用课程 id（纯 ASCII）。base 若含中文（如老师名），Vite 会把 base
        # URL-encode，而 vitepress 客户端路由 pathToFile 用 decodeURIComponent 解码路径后
        # 再按编码后 base 长度切片，长度错位导致子页面 404。课程归属老师记录在 course.json。
        env["VP_BASE"] = f"/course/{course_id}/"
        env["VP_TITLE"] = meta.get("name") or "课程"
        cmd = [NODE_EXE, "node_modules/vitepress/bin/vitepress.js", "build", "docs"]
        log("=== 开始渲染 ===")
        log("命令: " + " ".join(cmd))
        proc = subprocess.run(
            cmd, cwd=TEMPLATE_DIR, env=env,
            capture_output=True, text=True, timeout=_BUILD_TIMEOUT,
            encoding="utf-8", errors="replace",  # vitepress 输出为 UTF-8，避免中文 Windows 按 GBK 解码报错
        )
        log(proc.stdout or "")
        if proc.stderr:
            log("--- stderr ---")
            log(proc.stderr)
        if proc.returncode != 0:
            raise RuntimeError(f"vitepress build 失败，退出码 {proc.returncode}")

        out_dir = os.path.join(docs_dir, "out")
        if not os.path.exists(os.path.join(out_dir, "index.html")):
            raise RuntimeError("渲染产物缺失 index.html")

        # 5. 原子替换到正式目录
        final_dir = os.path.join(SITES_DIR, course_id)
        os.makedirs(os.path.dirname(final_dir), exist_ok=True)
        bak = final_dir + ".bak"
        if os.path.exists(final_dir):
            shutil.rmtree(bak, ignore_errors=True)
            os.rename(final_dir, bak)
        try:
            os.rename(out_dir, final_dir)
        except Exception:
            if os.path.exists(bak) and not os.path.exists(final_dir):
                os.rename(bak, final_dir)
            raise
        shutil.rmtree(bak, ignore_errors=True)

        publish.update({
            "status": "success", "error": None, "log": log_path,
            "published_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        log("=== 渲染成功 ===")
    except Exception as e:
        publish.update({
            "status": "failed", "error": str(e), "log": log_path,
            "failed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        log(f"=== 渲染失败: {e} ===")
    finally:
        meta = _read_course_meta(course_path)
        meta["publish"] = publish
        _write_course_meta(course_path, meta)
        logf.close()
        # 构建后清理临时工作区，保持模板干净（源文件在 data 里，随时可重新构建）
        try:
            _clear_workspace_docs()
        except Exception:
            pass


def _build_sidebar(docs_dir: str, order_map: dict = None, rel_prefix: str = "") -> list:
    """递归生成 vitepress sidebar：
    - 目录 = 分组；若目录内有 index.md，则目录标题链接到该目录首页
    - md = 页面；所有 index.md 都跳过（首页不作为侧边栏条目）
    - _order.json 有定义则按它排，否则自然排序
    """
    if order_map is None:
        order_map = {}

    def walk(dirpath: str, rel_prefix: str = "") -> list:
        items = []
        try:
            all_names = [n for n in os.listdir(dirpath)
                         if not n.startswith(".") and n != "course.json"]
        except OSError:
            return items
        custom_order = order_map.get(rel_prefix)
        if custom_order:
            order_index = {name: i for i, name in enumerate(custom_order)}
            all_names.sort(key=lambda n: order_index.get(n, 9999))
        else:
            all_names.sort(key=_natural_sort_key)
        names = all_names
        for name in names:
            if name.startswith(".") or name == "course.json":
                continue
            fp = os.path.join(dirpath, name)
            rel = f"{rel_prefix}/{name}" if rel_prefix else name
            if os.path.isdir(fp):
                has_index = os.path.exists(os.path.join(fp, "index.md"))
                sub = walk(fp, rel)
                if has_index or sub:
                    item = {"text": name, "collapsible": True, "collapsed": False}
                    if has_index:
                        item["link"] = f"/{rel}/"
                    if sub:
                        item["items"] = sub
                    items.append(item)
            elif name.endswith(".md") and name != "index.md":
                items.append({"text": name[:-3], "link": f"/{rel[:-3]}"})
        return items

    return walk(docs_dir)
