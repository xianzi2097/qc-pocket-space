# pip 包管理

> pip 是 Python 的官方包管理工具，用于安装、卸载和管理第三方库。电子协会四级考试中经常考查 pip 的基本使用和常见命令。

## 一、什么是 pip？

pip 的全称是 **"Pip Installs Packages"**，是 Python 默认自带的包管理工具。从 Python 3.4 开始，pip 已经内置在 Python 安装包中，无需单独安装。

通过 pip，我们可以：

- 安装 PyPI（Python Package Index）上的第三方库
- 卸载不再需要的库
- 查看已安装的库列表
- 管理库的版本

> [!tip]
> PyPI（Python Package Index）是 Python 的官方第三方软件仓库，就像手机上的应用商店一样，里面有数十万个开源的 Python 包供我们使用。

## 二、查看 pip 版本

使用 pip 之前，可以先确认当前环境中 pip 的版本：

```bash
pip --version
```

输出示例：

```
pip 24.0 from C:\Python\Lib\site-packages\pip (python 3.12)
```

如果想知道 pip 有哪些可用命令，可以使用：

```bash
pip help
```

## 三、安装包

### 1. 基本安装

```bash
pip install 包名
```

例如安装 `requests` 库：

```bash
pip install requests
```

### 2. 指定版本安装

```bash
pip install 包名==版本号
```

例如安装指定版本的 `numpy`：

```bash
pip install numpy==1.26.0
```

### 3. 安装大于/小于某个版本

```bash
pip install "包名>=版本号"
pip install "包名<版本号"
```

### 4. 升级包到最新版本

```bash
pip install --upgrade 包名
```

或简写：

```bash
pip install -U 包名
```

## 四、卸载包

```bash
pip uninstall 包名
```

卸载时会提示确认，输入 `y` 确认即可。如果想跳过确认直接卸载：

```bash
pip uninstall -y 包名
```

## 五、查看已安装的包

### 1. 列出所有已安装的包

```bash
pip list
```

输出示例：

```
Package        Version
-------------- -------
pip            24.0
requests       2.31.0
numpy          1.26.0
```

### 2. 查看某个包的详细信息

```bash
pip show 包名
```

输出包含版本号、作者、依赖关系等信息：

```
Name: requests
Version: 2.31.0
Summary: Python HTTP for Humans.
Author: Kenneth Reitz
Requires: certifi, charset-normalizer, idna, urllib3
```

### 3. 查看过期（有更新版本）的包

```bash
pip list --outdated
```

## 六、搜索包

```bash
pip search 关键词
```

> [!warning]
> 新版本 pip 已移除 `pip search` 功能，建议直接在 PyPI 官网（https://pypi.org）搜索。

## 七、导出与安装依赖

### 1. 导出当前环境的包列表

```bash
pip freeze > requirements.txt
```

这会将当前环境所有已安装的包及其版本号写入 `requirements.txt` 文件中，文件内容类似：

```
certifi==2024.2.2
charset-normalizer==3.3.2
idna==3.6
numpy==1.26.0
requests==2.31.0
urllib3==2.2.1
```

### 2. 从 requirements.txt 安装依赖

拿到别人项目的 `requirements.txt` 后，可以用以下命令一键安装所有依赖：

```bash
pip install -r requirements.txt
```

> [!tip]
> 这是最常见的协作方式——开发者把项目依赖导出到 `requirements.txt`，其他人拿到后一条命令就能安装好所有需要的库。

## 八、常用命令速查表

| 命令                                | 说明             |
| ----------------------------------- | ---------------- |
| `pip --version`                   | 查看 pip 版本    |
| `pip help`                        | 查看帮助         |
| `pip install 包名`                | 安装包           |
| `pip install 包名==版本`          | 安装指定版本的包 |
| `pip install -U 包名`             | 升级包           |
| `pip uninstall 包名`              | 卸载包           |
| `pip list`                        | 列出已安装的包   |
| `pip show 包名`                   | 查看包详情       |
| `pip list --outdated`             | 查看可更新的包   |
| `pip freeze`                      | 导出包列表       |
| `pip install -r requirements.txt` | 从文件安装依赖   |
