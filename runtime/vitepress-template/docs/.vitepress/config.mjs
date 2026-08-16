import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'
import { sidebar } from './gen/sidebar.mjs'

// 课程模板 config（静态引擎部分，Python 不修改此文件）
// - base：由环境变量 VP_BASE 注入（Python 每次构建时按课程设置）
// - sidebar：由 Python 生成到 ./gen/sidebar.mjs
// - outDir：相对「项目根目录」（即运行 vitepress 的 cwd），Python 构建后原子替换到正式目录
export default withMermaid({
  base: process.env.VP_BASE || '/course/test/',
  outDir: 'out',
  ignoreDeadLinks: true,

  markdown: {
    lineNumbers: false,
    math: true,
  },

  title: process.env.VP_TITLE || 'CodeNova',
  description: '课程文档',

  mermaid: {},
  mermaidPlugin: {
    class: 'mermaid my-class',
  },

  themeConfig: {
    nav: [],
    sidebar,
  },
})
