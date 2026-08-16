# 🌿 Vitepress Spring Theme

一款令人如沐春风的 Vitepress 主题，灵感来源于 Typora 主题商店的 [Spring 主题](https://github.com/SprInec/typora-spring-theme)。

## ✨ 特性

- 🟢 **Spring 配色** — 柔和绿色调，护眼舒适，适合长时间阅读
- 💻 **Mac 风格代码块** — 三圆点（红黄绿）+ 悬停变 Mac 彩色
- 🎯 **灵动交互** — 标题悬停放大、下划线展开、列表标记跳动
- 🌓 **亮/暗双模式** — 自动适配，暗色模式不刺眼
- 📊 **Mermaid 流程图** — 原生渲染，不做修改
- 📦 **零配置** — 放入主题文件夹即用，无需改 config

## 📸 预览

（请在此处添加截图）

## 🚀 安装

将 `theme` 文件夹复制到你的 Vitepress 项目的 `docs/.vitepress/` 目录下：

```
your-project/
└── docs/
    └── .vitepress/
        └── theme/
            ├── index.js
            └── custom.css
```

无需修改 `config.mjs`，Vitepress 会自动加载。

## 📁 文件说明

| 文件 | 说明 |
|:--|:--|
| `theme/index.js` | 主题入口，导入自定义样式 |
| `theme/custom.css` | 所有 Spring 风格样式（亮/暗双模式） |

## 📋 设计元素

| 元素 | 效果 |
|:--|:--|
| H1 标题 | 居中 + 悬停放大 + 绿色渐变背景 |
| H2 标题 | 居中 + 悬停展开绿色下划线 |
| 代码块 | Mac 三圆点（默认绿色 → 悬停红黄绿）|
| 内联代码 | 浅绿底 + 绿色字 |
| 引用块 | 绿色左边框 + 浅绿背景 + 悬停圆角动画 |
| 提示框 | tip 绿 / warning 黄 / danger 红 / info 蓝 |
| 表格 | 绿色表头 + 行悬停高亮 |
| 列表 | 绿色标记 + 悬停跳动动画 |
| 图片 | 圆角 + 悬停微放大 |
| 分隔线 | 绿色渐变 |

## 🙏 致谢

本主题的设计灵感来源于 **[SprInec/typora-spring-theme](https://github.com/SprInec/typora-spring-theme)**，一款为 Typora 设计的灵动 Spring 主题。

我们将其视觉风格适配到了 Vitepress，感谢原作者 SprInec 的精美设计。

- [Typora Themes Gallery](https://theme.typora.io/)
- [Spring Theme for Typora](https://github.com/SprInec/typora-spring-theme)

## 📄 License

MIT
