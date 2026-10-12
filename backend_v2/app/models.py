"""数据表：14 张表各长什么样（V2 结构化数据全部入库，大文件留磁盘）

本文件包含（按业务分组）：
  ┌─────────┬──────────────────────────────────────────────┐
  │ 分组     │ 表                                          │
  ├─────────┼──────────────────────────────────────────────┤
  │ 账号     │ accounts 账号 / auth_sessions 登录态 /        │
  │         │ login_attempts 限流计数                       │
  │ 班级     │ classes 班级 / class_members 班内学生          │
  │ 内容     │ assets 资产（课程/书架/资料/软件四类合表      │
  │         │ D16：bucket 分类）/                            │
  │         │ file_order 文件树顺序（原 _order.json）        │
  │ 沟通     │ messages 站内信 / message_folders 文件夹       │
  │ 个人     │ notebooks 笔记本 / notes 笔记 / settings 设置  │
  │ 其他     │ class_records 课堂记录 / trash 统一回收站       │
  └─────────┴──────────────────────────────────────────────┘
  只存"信息"（文字/数字），.sb3、课件等文件本体仍在磁盘，库里最多记路径。

  另有两个小工具：new_id() 生成 12 位随机 id，now_str() 生成当前时间串。

规范：docs/backend-v2-api-spec.md §2；上线前 schema 允许推翻，上线后只增不改（D7）。
"""
import secrets
from datetime import datetime

from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

_ID_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


def new_id(length: int = 12) -> str:
    """不可猜测的短 id：资源不拿显示名当主键"""
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)  # admin|teacher|student
    avatar_path: Mapped[str] = mapped_column(String(255), default="avatar_00")
    descriptors: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|deleted
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    # 档案字段（2026-10-12 定稿，规范 §3.2）：备注 + 入学/入职时间（name 姓名列已移除，显示名即账号名）
    remark: Mapped[str] = mapped_column(String(255), default="")
    enrolled_at: Mapped[str] = mapped_column(String(19), default="")  # 入学/入职 YYYY-MM-DD，≠created_at


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(12), index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)
    expires_at: Mapped[str] = mapped_column(String(19), index=True)


class LoginAttempt(Base):
    """登录梯度限流计数（按真实 IP，重启不丢）"""
    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ip: Mapped[str] = mapped_column(String(45), unique=True, index=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    last_fail_at: Mapped[str] = mapped_column(String(19), default=now_str)


class ClassRoom(Base):
    __tablename__ = "classes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64))
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)  # 每周排课
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_class_owner_name"),)


class ClassMember(Base):
    __tablename__ = "class_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    class_id: Mapped[str] = mapped_column(String(12), index=True)
    account_id: Mapped[str] = mapped_column(String(12), index=True)
    joined_at: Mapped[str] = mapped_column(String(19), default=now_str)

    __table_args__ = (UniqueConstraint("class_id", "account_id", name="uq_member"),)


class Asset(Base):
    """资产（D16 合表）：课程/书架/资料/软件四类共用一张表，bucket 分类
    type/format/publish 为课程族专用（bucket=courses|books 时用，其余为空）
    """
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(12), index=True)
    bucket: Mapped[str] = mapped_column(String(16), index=True)  # courses|books|reference|download
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    type: Mapped[str] = mapped_column(String(32), default="")     # 课程族：scratch|python|cpp…
    format: Mapped[str] = mapped_column(String(32), default="")   # 课程族：scratch|folder|ebook
    publish: Mapped[dict] = mapped_column(JSON, default=dict)     # 课程族：发布队列状态
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_asset_owner_name"),)


class FileOrder(Base):
    """文件树自定义顺序（原各目录下的 _order.json）"""
    __tablename__ = "file_order"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(12), index=True)
    parent_path: Mapped[str] = mapped_column(String(500), default="")
    name: Mapped[str] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, default=0)


class MessageFolder(Base):
    __tablename__ = "message_folders"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64))
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)  # 内置收件箱不可改删

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_folder_owner_name"),)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    sender_id: Mapped[str] = mapped_column(String(12), index=True)
    receiver_id: Mapped[str] = mapped_column(String(12), index=True)
    folder_id: Mapped[str] = mapped_column(String(12), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)


class Notebook(Base):
    __tablename__ = "notebooks"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_notebook_owner_name"),)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    notebook_id: Mapped[str] = mapped_column(String(12), index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str)
    created_at: Mapped[str] = mapped_column(String(19), default=now_str)


class ClassRecord(Base):
    """课堂记录：同班同日唯一，保存即覆盖（幂等）"""
    __tablename__ = "class_records"

    class_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD
    content: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(19), default=now_str)


class Setting(Base):
    __tablename__ = "settings"

    account_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    theme: Mapped[str] = mapped_column(String(16), default="")     # D20：''=橙
    hue: Mapped[int] = mapped_column(Integer, default=95)
    font_size: Mapped[int] = mapped_column(Integer, default=16)
    sidebar: Mapped[bool] = mapped_column(Boolean, default=True)
    recent_opens: Mapped[list] = mapped_column(JSON, default=list)  # 上限 config.RECENT_OPENS_LIMIT


class Trash(Base):
    """统一回收站（原 V1 的用户 DB 版 + 内容文件版两套合一）"""
    __tablename__ = "trash"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # account|class|asset
    owner_id: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # 恢复所需快照
    disk_path: Mapped[str] = mapped_column(String(500), default="")  # 内容类被移动的目录
    deleted_at: Mapped[str] = mapped_column(String(19), default=now_str)
