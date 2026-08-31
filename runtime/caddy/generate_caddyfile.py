"""
青橙 Studio - Caddyfile 生成器
自动检测项目路径，注入绝对路径到 Caddyfile。
零依赖，可直接用系统 Python 运行。
用法: python generate_caddyfile.py local [域名] [端口]    (本地测试)
      python generate_caddyfile.py server [域名] [端口]   (线上生产)
"""
import os
import sys


def main():
    # 这个脚本放在 runtime/caddy/ 目录下
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 项目根目录 = runtime/caddy/ 的上两级（qc-pocket-space/）
    project_root = os.path.dirname(os.path.dirname(script_dir))
    # Caddy 需要正斜杠
    site_path = os.path.join(project_root, "frontend").replace("\\", "/")
    data_path = os.path.join(os.path.dirname(project_root), "data").replace("\\", "/")

    # 模式：local 或 server，后面可跟自定义域名
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"
    if len(sys.argv) > 2:
        site_domain = sys.argv[2]
    elif mode == "server":
        site_domain = "codenova.cn"
    else:
        site_domain = "localhost"
    fastapi_port = sys.argv[3] if len(sys.argv) > 3 else "8000"
    caddy_port = sys.argv[4] if len(sys.argv) > 4 else ""

    # Judge0 判题机上游地址
    #   local 模式：i3 局域网本机（Docker 2358）
    #   server 模式：frpc 隧道 → i3 的 Judge0
    if mode == "server":
        judge0_upstream = "38.76.216.46:10654"
    else:
        judge0_upstream = "192.168.2.158:2358"

    # Caddy 地址：本地模式用 `:端口`（纯HTTP），域名模式用 `域名:端口`
    if mode == "local":
        port = caddy_port if caddy_port else "80"
        site_domain = f":{port}"
    elif caddy_port and caddy_port != "443":
        site_domain = f"{site_domain}:{caddy_port}"
    # 443 时不追加端口，Caddy 自动启用 HTTPS + Let's Encrypt

    # 合并域名列表（www + 主域名共用同一块，一张证书）
    if mode == "server":
        suffix = site_domain.split(":", 1)[1] if ":" in site_domain else ""
        www_domain = f"www.{site_domain.split(':')[0]}"
        if suffix:
            www_domain = f"{www_domain}:{suffix}"
        site_domain_list = f"{site_domain}, {www_domain}"
    else:
        site_domain_list = site_domain

    # 构建 Caddyfile 内容（f-string 直接生成，无需模板文件）
    caddyfile = f"""# ================= 你的网站配置 =================
{site_domain_list} {{
    # www → 主域名 301 跳转
    @www {{
        host www.{site_domain}
    }}
    redir @www https://{site_domain}{{uri}}
    encode gzip zstd

    # 0.后端大脑：把所有 /api/ 开头的流量，拦截下来交给 FastAPI
    handle /api/* {{
        reverse_proxy localhost:{fastapi_port}
    }}

    # 0.2 判题机 Judge0：/judge0/* 反向代理到远程 Judge0 实例
    handle /judge0/* {{
        reverse_proxy {judge0_upstream}
    }}

    # 0.1 宣传落地页
    handle /landing* {{
        root * "{site_path}/landing"
        try_files {{path}} /index.html
        file_server
    }}

    # 1. 前端 SPA（所有路由 / /courses /dashboard 等统一 try_files 兜底）
    handle {{
        root * "{site_path}/dashboard"
        try_files {{path}} /index.html
        file_server
    }}

    # ================= 跨域配置（局域网部署，仅允许本地域名） =================
    header {{
        Access-Control-Allow-Origin "{site_domain}"
        Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
        Access-Control-Allow-Headers "Content-Type, Authorization"
        Access-Control-Expose-Headers "Content-Type"
    }}

    @options {{
        method OPTIONS
    }}
    respond @options 200

    # 2. 学习手册：映射到 VitePress 打包目录
    handle_path /docs/* {{
        root * "{site_path}/vitepress_dist"
        file_server
    }}

    # 2.1 电子书课程站点：运行时 vitepress 渲染产物（只读，学生浏览）
    handle_path /course/* {{
        root * "{site_path}/course_vitepress_dist"
        file_server
    }}

    # 3. 核心素材共享（data/courses/）—— 只读：学生浏览/下载课件；写走网页后台上传（/api）
    handle_path /share/* {{
        root * "{data_path}/courses"
        file_server browse
    }}

    # 5. 电子教室路由
    handle_path /classroom/* {{
        root * "{site_path}/classroom"
        file_server
    }}

    # 6. Scratch 在线编辑器前端
    handle_path /Scratch/* {{
        root * "{site_path}/scratch_dist"
        try_files {{path}} /index.html
        file_server
    }}

}}"""

    # 写出最终的 Caddyfile（覆盖旧的）
    caddyfile_path = os.path.join(script_dir, "Caddyfile")
    with open(caddyfile_path, "w", encoding="utf-8") as f:
        f.write(caddyfile)

    print(f"[OK] Caddyfile 已生成")
    print(f"     路径   = {site_path}")
    print(f"     域名   = {site_domain}")
    print(f"     文件   = {caddyfile_path}")


if __name__ == "__main__":
    main()
