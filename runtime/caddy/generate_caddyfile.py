"""
青橙 Studio - Caddyfile 生成器
自动检测项目路径，注入绝对路径到 Caddyfile。
零依赖，可直接用系统 Python 运行。
用法: python generate_caddyfile.py local|server [域名] [fastapi端口] [caddy端口] [judge0上游] [agent域名] [comfyui域名]
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

    # 模式：local 或 server，后面可跟自定义域名
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"
    if len(sys.argv) > 2:
        site_domain = sys.argv[2]
    elif mode == "server":
        site_domain = "codenova.cn"
    else:
        site_domain = "localhost"
    fastapi_port = sys.argv[3] if len(sys.argv) > 3 else "8001"
    caddy_port = sys.argv[4] if len(sys.argv) > 4 else ""
    judge0_upstream = sys.argv[5].strip() if len(sys.argv) > 5 and sys.argv[5].strip() else ""

    # Agent 站点（如：qwen.codenova.cn）留空不生成，仅 server 模式；裸域名自动领证，http:// 前缀=不用 TLS
    agent_site = sys.argv[6].strip() if len(sys.argv) > 6 and sys.argv[6].strip() else ""

    # 生图模型站点（如：comfyui.codenova.cn）留空不生成，仅 server 模式
    comfy_site = sys.argv[7].strip() if len(sys.argv) > 7 and sys.argv[7].strip() else ""

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

    # Judge0: reverse proxy to remote instance (handle_path strips prefix)
    judge0_block = ""
    if judge0_upstream:
        judge0_block = f"""
    handle_path /judge0/* {{
        reverse_proxy {judge0_upstream}
    }}"""

    # Agent 站点：反代到 SSH 隧道 localhost:25553
    agent_block = ""
    if agent_site and mode == "server":
        agent_block = f"""
# ================= Agent 站点 =================
{agent_site} {{
    reverse_proxy localhost:25553
}}"""

    # 生图模型站点（ComfyUI）：反代到 SSH 隧道 localhost:25554（朋友那台 Windows 机本机地址）
    comfy_block = ""
    if comfy_site and mode == "server":
        comfy_block = f"""
# ================= 生图模型站点（ComfyUI） =================
{comfy_site} {{
    # 网站前端跨域调用（ComfyUI 自身默认不带 CORS 头）
    header {{
        Access-Control-Allow-Origin "*"
        Access-Control-Allow-Methods "GET, POST, PUT, DELETE, OPTIONS"
        Access-Control-Allow-Headers "Content-Type, Authorization"
    }}
    @options {{
        method OPTIONS
    }}
    respond @options 204

    reverse_proxy localhost:25554
}}"""

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
{judge0_block}
    # 0.1 宣传落地页
    handle /landing* {{
        root * "{site_path}/landing"
        try_files {{path}} /index.html
        file_server
    }}

    # 1. 前端 SPA（所有路由统一 try_files 兜底）
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

    # 2. 站点维基（原 /docs，2026-11-18 改路由）：映射到 VitePress 打包目录
    handle_path /wiki/* {{
        root * "{site_path}/vitepress_dist"
        file_server
    }}

    # 2.1 电子书课程站点：运行时 vitepress 渲染产物（只读，学生浏览）
    handle_path /course/* {{
        root * "{site_path}/course_vitepress_dist"
        file_server
    }}

    # 6. Scratch 在线编辑器前端
    handle_path /Scratch/* {{
        root * "{site_path}/scratch_dist"
        try_files {{path}} /index.html
        file_server
    }}

}}{agent_block}{comfy_block}"""

    # 写出最终的 Caddyfile（覆盖旧的）
    caddyfile_path = os.path.join(script_dir, "Caddyfile")
    with open(caddyfile_path, "w", encoding="utf-8") as f:
        f.write(caddyfile)

    print(f"[OK] Caddyfile 已生成")
    print(f"     路径   = {site_path}")
    print(f"     域名   = {site_domain}")
    if agent_block:
        print(f"     Agent  = {agent_site} → localhost:25553")
    if comfy_block:
        print(f"     ComfyUI = {comfy_site} → localhost:25554")
    print(f"     文件   = {caddyfile_path}")


if __name__ == "__main__":
    main()
