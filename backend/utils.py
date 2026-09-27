"""共享工具函数 + Auth 辅助 + Pydantic 表单"""
import os
import re
import json
import hashlib
import secrets
import threading
import time
import shutil
from collections import defaultdict
from datetime import datetime
from fastapi import HTTPException, Request, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from models import SessionLocal, UserModel, TrashModel, USERS_DIR

# ==========================================
# 密码哈希
# ==========================================
def hash_password(password: str) -> str:
    """生成 sha256$salt$hash 格式的密码哈希"""
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${h}"

def verify_password(password: str, stored: str) -> bool:
    """验证密码（sha256$salt$hash 格式）"""
    if stored and stored.startswith("sha256$"):
        parts = stored.split("$", 2)
        if len(parts) == 3:
            salt, h = parts[1], parts[2]
            return hashlib.sha256((salt + password).encode()).hexdigest() == h
    return False

# ==========================================
# 原子写入
# ==========================================
# 单进程多线程：每路径一把可重入锁，串行化同文件的「读→改→写」和写临时文件。
# （RLock：持锁做 RMW 时再调 _atomic_save 不会自锁死）
_LOCKS: dict[str, threading.RLock] = defaultdict(threading.RLock)
_LOCKS_GUARD = threading.Lock()


def _file_lock(fp: str) -> threading.RLock:
    """返回路径对应的进程内锁；调用方可 `with _file_lock(fp):` 包住读-改-写。"""
    key = os.path.abspath(fp)
    # defaultdict 的建锁不是线程安全的，双重检查 + 短锁
    lock = _LOCKS.get(key)
    if lock is None:
        with _LOCKS_GUARD:
            lock = _LOCKS.get(key)
            if lock is None:
                lock = threading.RLock()
                _LOCKS[key] = lock
    return lock


def _tmp_name(fp: str) -> str:
    """临时文件名带 pid+tid，即使有旁路写入也不会抢同一个 .tmp。"""
    return f"{fp}.{os.getpid()}.{threading.get_ident()}.tmp"


def _atomic_save(fp: str, data):
    """原子写入 JSON：先写临时文件再 rename，断电/崩溃不会损坏原文件；
    同路径进程内互斥，防两线程写坏同一个 .tmp。"""
    with _file_lock(fp):
        tmp = _tmp_name(fp)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, fp)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _atomic_write_file(fp: str, content: str):
    """原子写入文本文件：先写临时文件再 rename，断电/崩溃不会损坏原文件。"""
    with _file_lock(fp):
        tmp = _tmp_name(fp)
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, fp)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

# ==========================================
# 路径常量
# ==========================================
TRASH_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "trash"))
COURSE_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "courses"))
RESOURCE_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "resources"))
COURSE_SITES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "course_vitepress_dist"))
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(COURSE_BASE, exist_ok=True)
os.makedirs(RESOURCE_BASE, exist_ok=True)

# ==========================================
# Token 认证
# ==========================================
# 内存 token 存活跃会话。24h 过期 + 顶号 + 退出登录清 token。重启丢失（可接受）。
ACTIVE_TOKENS: dict[str, dict] = {}

def generate_token(username: str, role: str) -> str:
    # ⛔ 踢掉此用户的旧 token（顶号）
    old = [t for t, info in ACTIVE_TOKENS.items() if info["username"] == username]
    for t in old:
        del ACTIVE_TOKENS[t]
    now = time.time()
    token = secrets.token_hex(32)
    ACTIVE_TOKENS[token] = {
        "username": username,
        "role": role,
        "created_at": now,
        "expires_at": now + 86400,  # 24h
    }
    return token

def verify_token(authorization: str | None) -> dict:
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    now = time.time()
    # 惰性清理所有过期 token
    expired = [t for t, info in list(ACTIVE_TOKENS.items())
               if info.get("expires_at", 0) < now]
    for t in expired:
        del ACTIVE_TOKENS[t]
    token = parts[1]
    user_info = ACTIVE_TOKENS.get(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    if user_info.get("expires_at", 0) < now:
        del ACTIVE_TOKENS[token]
        raise HTTPException(status_code=401, detail="登录已过期")
    return user_info

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user(request: Request) -> dict:
    user = request.state.user
    if user["role"] == "guest":
        raise HTTPException(status_code=401, detail="未登录，请先登录")
    return user

def require_role(request: Request, allowed_roles: list) -> dict:
    user = request.state.user
    if user["role"] not in allowed_roles:
        raise HTTPException(status_code=403, detail="你没有权限执行此操作")
    return user

# ==========================================
# 路径工具
# ==========================================
def _safe_path(base: str, *parts: str) -> str:
    fp = os.path.normpath(os.path.join(base, *parts))
    if os.path.commonpath([os.path.normpath(base), fp]) != os.path.normpath(base):
        raise HTTPException(status_code=403, detail="路径不安全")
    return fp


def _valid_new_name(new_name: str) -> str:
    """校验并清洗重命名目标：非空、不含路径分隔符"""
    new_name = (new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        raise HTTPException(status_code=400, detail="名称无效（不能为空或含 / \\）")
    return new_name

def get_user_dir(user: UserModel) -> str:
    return os.path.join(USERS_DIR, user.role + "s", str(user.id))

def _resolve_user_dir(username: str) -> str | None:
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == username).first()
        return get_user_dir(user) if user else None
    finally:
        db.close()

def _resolve_user_by_username(username: str) -> UserModel | None:
    db = SessionLocal()
    try:
        return db.query(UserModel).filter(UserModel.username == username).first()
    finally:
        db.close()

# ==========================================
# 用户目录与 info.json
# ==========================================
def write_info_json(user: UserModel):
    user_dir = get_user_dir(user)
    os.makedirs(user_dir, exist_ok=True)
    try:
        descriptors = json.loads(user.qc_descriptors or "{}")
    except json.JSONDecodeError:
        descriptors = {}
    info = {
        "username": user.username,
        "role": user.role,
        "avatar_path": user.avatar_path,
        "descriptors": descriptors,
    }
    _atomic_save(os.path.join(user_dir, "info.json"), info)

def _ensure_user(db: Session, username: str, role: str, avatar: str, descriptors: dict) -> UserModel:
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if user:
        return user
    user = UserModel(
        username=username,
        password=hash_password("111111"),
        role=role,
        avatar_path=avatar,
        qc_descriptors=json.dumps(descriptors, ensure_ascii=False)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    ensure_user_dirs(user)
    return user

def ensure_user_dirs(user: UserModel):
    user_dir = get_user_dir(user)
    os.makedirs(user_dir, exist_ok=True)
    if user.role == "student":
        for sub in ["sb3_projects", "notes", "messages"]:
            os.makedirs(os.path.join(user_dir, sub), exist_ok=True)
            os.makedirs(os.path.join(user_dir, "messages", "收件箱"), exist_ok=True)
    write_info_json(user)


def self_check_user_dirs():
    """启动时自检：扫描 DB 里的用户，确保目录和子目录都存在。
    断电/崩溃导致 DB 写成功但目录没建的情况，这里补建。
    只建目录不改数据，是初始化逻辑，不是防丢失。"""
    db = SessionLocal()
    try:
        users = db.query(UserModel).all()
        fixed = 0
        for user in users:
            user_dir = get_user_dir(user)
            if not os.path.exists(user_dir):
                ensure_user_dirs(user)
                fixed += 1
            elif user.role == "student":
                # 补建学生子目录（旧版本升级可能缺少）
                for sub in ["sb3_projects", "notes", "messages"]:
                    os.makedirs(os.path.join(user_dir, sub), exist_ok=True)
                os.makedirs(os.path.join(user_dir, "messages", "收件箱"), exist_ok=True)
        if fixed:
            print(f"[startup] self-check: 补建了 {fixed} 个用户目录")
        else:
            print("[startup] self-check: 所有用户目录正常")
    finally:
        db.close()


# ==========================================
# 软删除 / 恢复用户
# ==========================================
def move_to_trash(user: UserModel, db: Session) -> dict:
    src = os.path.normpath(get_user_dir(user))
    root = os.path.normpath(USERS_DIR)
    if os.path.commonpath([root, src]) != root:
        raise HTTPException(status_code=400, detail="路径不安全")
    write_info_json(user)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_folder_name = f"{user.role}_{user.username}_{timestamp}"
    dst = os.path.join(TRASH_DIR, trash_folder_name)
    os.makedirs(TRASH_DIR, exist_ok=True)
    if os.path.exists(src):
        shutil.move(src, dst)
    else:
        os.makedirs(dst, exist_ok=True)
    db_snapshot = {
        "username": user.username, "password": user.password, "role": user.role,
        "avatar_path": user.avatar_path, "qc_descriptors": user.qc_descriptors,
    }
    trash = TrashModel(
        username=user.username, role=user.role,
        original_path=src, trash_folder=trash_folder_name,
        db_snapshot=json.dumps(db_snapshot, ensure_ascii=False),
        trashed_at=timestamp
    )
    db.add(trash)
    # 清除该用户的所有 token
    tokens_to_remove = [t for t, info in ACTIVE_TOKENS.items() if info["username"] == user.username]
    for t in tokens_to_remove:
        del ACTIVE_TOKENS[t]
    db.delete(user)
    db.commit()
    return {
        "id": trash.id, "username": trash.username, "role": trash.role,
        "trash_folder": trash.trash_folder, "trashed_at": trash.trashed_at
    }

def restore_from_trash(trash_id: int, db: Session) -> dict:
    trash = db.query(TrashModel).filter(TrashModel.id == trash_id).first()
    if not trash:
        raise HTTPException(status_code=404, detail="垃圾桶中未找到该记录")
    trash_path = os.path.join(TRASH_DIR, trash.trash_folder)
    info_path = os.path.join(trash_path, "info.json")
    if os.path.exists(info_path):
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        username = info["username"]
        role = info["role"]
        avatar_path = info["avatar_path"]
        qc_descriptors = json.dumps(info.get("descriptors", {}), ensure_ascii=False)
        db_snap = json.loads(trash.db_snapshot or "{}")
        password = db_snap.get("password", hash_password("111111"))
    else:
        db_snap = json.loads(trash.db_snapshot or "{}")
        username = db_snap["username"]
        role = db_snap["role"]
        avatar_path = db_snap["avatar_path"]
        qc_descriptors = db_snap.get("qc_descriptors", "{}")
        password = db_snap.get("password", "111111")
    if db.query(UserModel).filter(UserModel.username == username).first():
        raise HTTPException(status_code=400, detail=f"用户 [{username}] 已存在，无法恢复")
    user = UserModel(
        username=username, password=password,
        role=role, avatar_path=avatar_path, qc_descriptors=qc_descriptors
    )
    db.add(user)
    db.commit()
    target_dir = get_user_dir(user)
    if os.path.exists(trash_path):
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        shutil.move(trash_path, target_dir)
    db.delete(trash)
    db.commit()
    return {"status": "success", "message": f"用户 [{username}] 已恢复！", "username": username, "role": role}

# ==========================================
# 班级工具
# ==========================================
def _get_classes_path(teacher_username: str) -> str:
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == teacher_username).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"老师 [{teacher_username}] 不存在")
        return os.path.join(get_user_dir(user), "classes.json")
    finally:
        db.close()

def _load_classes(teacher_username: str) -> list:
    path = _get_classes_path(teacher_username)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_classes(teacher_username: str, classes: list):
    path = _get_classes_path(teacher_username)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _atomic_save(path, classes)

# ==========================================
# 课堂记录工具
# ==========================================
def _get_session_path(username: str, class_name: str, month: str) -> str:
    user_dir = _resolve_user_dir(username)
    if not user_dir:
        root = os.path.join(USERS_DIR, "teachers", username, "sessions")
    else:
        root = os.path.join(user_dir, "sessions")
    # class_name、month 均来自用户输入（month = form.date[:7] 也可含 ../）→ 统一过 _safe_path
    # base 取 sessions 目录本身：class_name=".." 时会越出 base 直接 403
    return _safe_path(root, class_name, f"{month}.json")

def _load_sessions(username: str, class_name: str, month: str) -> list:
    fp = _get_session_path(username, class_name, month)
    if not os.path.exists(fp):
        return []
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_sessions(username: str, class_name: str, month: str, sessions: list):
    fp = _get_session_path(username, class_name, month)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    _atomic_save(fp, sessions)

# ==========================================
# 课程工具
# ==========================================
def _get_teacher_key(teacher_username: str) -> str:
    """课程目录/URL 中使用的老师标识（去掉 (t) 和空格，兼容 seed 数据）"""
    return teacher_username.replace("(t)", "").replace(" ", "")


def _get_teacher_course_dir(teacher_username: str) -> str:
    return os.path.join(COURSE_BASE, f"{_get_teacher_key(teacher_username)}_course")


def _gen_course_id() -> str:
    """生成课程文件夹 id：c + 时间戳 + 4 位随机（唯一、URL 安全、永不复用）"""
    return "c" + datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)


def _course_id_taken(course_id: str) -> bool:
    """课程 id 是否已被占用。URL/渲染产物不含老师层，课程 id 是全局命名空间，
    因此需要跨所有老师课程目录 + 全局渲染产物两层查重。"""
    if os.path.exists(os.path.join(COURSE_SITES_DIR, course_id)):
        return True
    try:
        for d in os.listdir(COURSE_BASE):
            if d.endswith("_course") and os.path.isdir(os.path.join(COURSE_BASE, d)):
                if os.path.exists(os.path.join(COURSE_BASE, d, course_id)):
                    return True
    except OSError:
        pass
    return False


def _gen_unique_course_id() -> str:
    """生成全局唯一的课程 id：撞号自动换号重试（同一秒创建时靠随机位兜底）。"""
    for _ in range(50):
        cid = _gen_course_id()
        if not _course_id_taken(cid):
            return cid
    raise HTTPException(status_code=500, detail="课程 ID 生成失败，请重试")


def _resolve_course_format(course_type: str, fmt: str) -> str:
    """推断打开方式：显式 format 优先；否则 scratch→scratch，python/cpp→folder"""
    if fmt in ("scratch", "folder", "ebook"):
        return fmt
    return "scratch" if course_type == "scratch" else "folder"


def _natural_sort_key(name: str):
    """自然排序键：L1 L2 L10 按数字顺序"""
    import re as _re
    return [int(c) if c.isdigit() else c.lower() for c in _re.split(r'(\d+)', name)]


def _read_order_file(course_path: str) -> dict:
    """读取 _order.json；不存在返回空 dict，格式错误返回空 dict"""
    order_path = os.path.join(course_path, "_order.json")
    if not os.path.exists(order_path):
        return {}
    try:
        with open(order_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def check_order_file(course_path: str) -> str | None:
    """检查 _order.json 是否有问题，返回警告消息或 None"""
    order_path = os.path.join(course_path, "_order.json")
    if not os.path.exists(order_path):
        return None
    try:
        with open(order_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "_order.json 格式错误：应为字典 {\"\": [...], ...}，已忽略"
        return None
    except json.JSONDecodeError as e:
        return f"_order.json 解析失败（{e.msg} 第{e.lineno}行），已按自然排序兜底"
    except OSError as e:
        return f"_order.json 读取失败（{e}），已按自然排序兜底"


def _read_course_meta(course_path: str) -> dict:
    """读取 course.json；不存在/损坏返回空 dict（不写盘）"""
    meta_path = os.path.join(course_path, "course.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            meta = {}
    return meta


def _write_course_meta(course_path: str, meta: dict) -> None:
    _atomic_save(os.path.join(course_path, "course.json"), meta)


# ── 资源 meta（resource.json，语义区分课程） ──

def _read_resource_meta(resource_path: str) -> dict:
    meta_path = os.path.join(resource_path, "resource.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            meta = {}
    return meta


def _write_resource_meta(resource_path: str, meta: dict) -> None:
    _atomic_save(os.path.join(resource_path, "resource.json"), meta)


# ==========================================
# 消息工具
# ==========================================
MESSAGES_DIR_NAME = "messages"

def _get_msgs_dir(username: str) -> str:
    user_dir = _resolve_user_dir(username)
    if not user_dir:
        raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")
    return os.path.join(user_dir, MESSAGES_DIR_NAME)

def _write_msg(username: str, folder: str, msg_id: str, sender: str, subject: str, body: str):
    folder_dir = os.path.join(_get_msgs_dir(username), folder)
    os.makedirs(folder_dir, exist_ok=True)
    msg = {
        "id": msg_id, "sender": sender, "subject": subject, "body": body,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "is_read": False
    }
    _atomic_save(os.path.join(folder_dir, f"{msg_id}.json"), msg)

def _find_msg_file(username: str, folder: str | None, msg_id: str) -> str | None:
    base = _get_msgs_dir(username)
    if not os.path.exists(base):
        return None
    if folder:
        # folder 来自查询参数（mark_read 透传）→ 过 _safe_path，防 ../ 定位/回写消息目录之外的 JSON
        path = _safe_path(base, folder, f"{msg_id}.json")
        return path if os.path.exists(path) else None
    for fd in os.listdir(base):
        path = os.path.join(base, fd, f"{msg_id}.json")
        if os.path.exists(path):
            return path
    return None

# ==========================================
# 统一回收站工具
# ==========================================
def _move_to_trash(source_path: str, trash_type: str, owner: str, original_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = os.path.basename(source_path.rstrip("/\\"))
    trash_folder_name = f"{trash_type}__{owner}__{name}__{timestamp}"
    dst = os.path.join(TRASH_DIR, trash_folder_name)
    os.makedirs(TRASH_DIR, exist_ok=True)
    os.makedirs(dst, exist_ok=True)
    shutil.move(source_path, os.path.join(dst, name))
    meta = {
        "type": trash_type, "owner": owner,
        "original_path": original_path,
        "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_save(os.path.join(dst, ".trash.json"), meta)
    return trash_folder_name

def _list_trash_items(owner: str = "") -> list:
    if not os.path.exists(TRASH_DIR):
        return []
    items = []
    for folder_name in sorted(os.listdir(TRASH_DIR), reverse=True):
        folder_path = os.path.join(TRASH_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        meta_path = os.path.join(folder_path, ".trash.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if owner and meta.get("owner") != owner:
            continue
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for fn in filenames:
                if fn == ".trash.json":
                    continue
                try:
                    total_size += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    pass
        # 课程/资源：从内层业务 meta 补充可读名称（回收站弹窗展示用）
        if meta.get("type") in ("course", "resource"):
            for fn in sorted(os.listdir(folder_path)):
                inner = os.path.join(folder_path, fn)
                if not os.path.isdir(inner):
                    continue
                for mf in ("course.json", "resource.json"):
                    mp = os.path.join(inner, mf)
                    if os.path.exists(mp):
                        try:
                            with open(mp, "r", encoding="utf-8") as f:
                                meta["name"] = json.load(f).get("name") or fn
                        except (json.JSONDecodeError, OSError):
                            meta["name"] = fn
                        break
                if "name" in meta:
                    break
        meta["_folder"] = folder_name
        meta["_size"] = total_size
        items.append(meta)
    return items

# ==========================================
# Pydantic 表单（多个模块共用）
# ==========================================
class RegisterForm(BaseModel):
    username: str
    password: str
    role: str

class LoginForm(BaseModel):
    username: str
    password: str

class ChangePasswordForm(BaseModel):
    username: str
    old_password: str
    new_password: str

class AvatarUpdateForm(BaseModel):
    username: str
    avatar_path: str

class DescriptorsUpdateForm(BaseModel):
    qc_descriptors: str

class SendMessageForm(BaseModel):
    recipient: str
    folder: str = "收件箱"
    subject: str
    body: str

class NotebookForm(BaseModel):
    name: str

class NoteSaveForm(BaseModel):
    filename: str
    content: str

class ClassForm(BaseModel):
    name: str
    schedule: dict = {}  # {"days": ["周三","周五"], "time": "14:00", "location": "301教室"}

class StudentForm(BaseModel):
    name: str
    account: str = ""
    remark: str = ""
    existing_id: str = ""
    schedule: dict | None = None  # 可选的个人上课日，如 {"days": ["周六"]}

class SessionStudentRecord(BaseModel):
    name: str
    remark: str = ""
    status: str = "present"
    content: str = ""
    next_plan: str = ""

class SessionForm(BaseModel):
    date: str
    students: list[SessionStudentRecord]

class CourseCreateForm(BaseModel):
    teacher: str
    name: str = ""  # ebook 课程可为空，由上传文件名决定
    course_type: str = "scratch"
    format: str = ""  # scratch | folder | ebook；空则按 course_type 推断
    tags: list[str] = []  # 课程标签
    description: str = ""  # 课程描述

class RenameForm(BaseModel):
    new_name: str

class ResourceCreateForm(BaseModel):
    teacher: str
    name: str = ""
    category: str = "reference"
    description: str = ""
    tags: list[str] = []
    format: str = "folder"


class ResourceUpdateForm(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    format: str = ""


class CourseUpdateForm(BaseModel):
    name: str
    description: str = ""
    tags: list[str] = []
    course_type: str = "scratch"
    format: str = ""

class FeaturedToggleForm(BaseModel):
    featured: bool  # true=精选（显示在「精选课程」栏目），false=普通（显示在「在线课程」）

