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
    # 这个脚本放在 caddy/ 目录下
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 项目根目录 = caddy/ 的上一级
    project_root = os.path.dirname(script_dir)
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

    # 读模板
    template_path = os.path.join(script_dir, "Caddyfile.template")
    if not os.path.exists(template_path):
        print(f"[错误] 找不到模板文件: {template_path}")
        sys.exit(1)

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 注入绝对路径和域名
    content = content.replace("{{SITE_PATH}}", site_path)
    content = content.replace("{{DATA_PATH}}", data_path)
    content = content.replace("{{SITE_DOMAIN}}", site_domain)
    content = content.replace("{{SITE_DOMAIN_LIST}}", site_domain_list)
    content = content.replace("{{FASTAPI_PORT}}", fastapi_port)
    content = content.replace("{{WWW_REDIRECT}}", "")

    # 写出最终的 Caddyfile（覆盖旧的）
    caddyfile_path = os.path.join(script_dir, "Caddyfile")
    with open(caddyfile_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[OK] Caddyfile 已生成")
    print(f"     路径   = {site_path}")
    print(f"     域名   = {site_domain}")
    print(f"     文件   = {caddyfile_path}")


if __name__ == "__main__":
    main()
