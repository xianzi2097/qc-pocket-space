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
    lineNumbers: true,
    math: true,
  },

  title: process.env.VP_TITLE || 'CodeNova',
  description: '课程文档',

  // 关键修复：Windows 下 fs.realpathSync 会把盘符 C: 变成小写 c:，
  // 导致 resolvePageImports 的 srcPath(c:/...) 与 chunk facadeModuleId(C:/...) 严格比较失败，
  // 报 "Cannot read properties of undefined (reading 'imports')"。
  // 开启 preserveSymlinks 让 vitepress 跳过 realpathSync，路径大小写保持一致。
  vite: {
    resolve: {
      preserveSymlinks: true,
    },
  },

  mermaid: {},
  mermaidPlugin: {
    class: 'mermaid my-class',
  },

  themeConfig: {
    // 文章目录停靠左侧（与主文档站一致）；单页可 frontmatter 覆盖
    aside: 'left',
    logoLink: { link: '/../../', target: '_self' },
    nav: [
      { text: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-3px;margin-right:4px"><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg> 首页', link: '/../../', target: '_self' },
      { text: '<svg xmlns="http://www.w3.org/2000/svg" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-3px;margin-right:4px"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg> 课程广场', link: '/../../?view=courses', target: '_self' },
    ],
    sidebar,
  },
})
