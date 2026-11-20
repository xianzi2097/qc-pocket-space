// 客户端 Scratch 积木渲染：把 <pre class="blocks"> 占位符替换为 SVG。
// 与本地维基 vitepress-dev theme/scratch.ts 同款（D39 移植，2026-11-20），此处为 JS 版。
// 注意：scratchblocks 的浏览器入口在模块顶层执行 init(window)，
// 因此必须在 onMounted 里动态 import，绝不能在构建期（SSG/SSR）加载。

let loaded = null

async function ensureLoaded() {
  if (!loaded) {
    loaded = (async () => {
      const [{ default: scratchblocks }, { default: zhcn }] = await Promise.all([
        import('scratchblocks'),
        import('scratchblocks/locales/zh-cn.json'),
      ])
      scratchblocks.loadLanguages({ 'zh-cn': zhcn })
      // ESM 入口导入时已自动注入样式；若缺失（如换了构建入口）则补一次。
      const stylesInjected = [...document.head.querySelectorAll('style')].some(
        (s) => (s.textContent || '').includes('sb3-label'),
      )
      if (!stylesInjected) scratchblocks.appendStyles()
    })()
  }
  await loaded
  return (await import('scratchblocks')).default
}

/**
 * 渲染页面上所有尚未渲染的 Scratch 积木占位符。
 * 已渲染的元素带 data-sb-rendered 标记，重复调用会跳过，
 * 避免把已生成的 SVG 又当代码解析一遍。
 */
export async function renderScratchBlocks() {
  if (typeof document === 'undefined') return
  const scratchblocks = await ensureLoaded()

  const options = {
    style: 'scratch3',
    languages: ['zh-cn', 'en'],
  }

  const mark = (replace) =>
    function (el, svg, doc, opts) {
      replace(el, svg, doc, opts)
      el.setAttribute('data-sb-rendered', '')
    }

  // 块级：```scratch 围栏
  scratchblocks.renderMatching('pre.blocks:not([data-sb-rendered])', {
    ...options,
    replace: mark(scratchblocks.replace),
  })

  // 行内：<code class="b">…</code>
  scratchblocks.renderMatching('code.b:not([data-sb-rendered])', {
    ...options,
    inline: true,
    replace: mark(scratchblocks.replace),
  })
}
