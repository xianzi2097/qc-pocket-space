import DefaultTheme from 'vitepress/theme'
import { defineComponent, h, onMounted, watch, nextTick } from 'vue'
import { useRoute, onContentUpdated } from 'vitepress'
import { renderScratchBlocks } from './scratch.js'
import './custom.css'
import { Home, BookOpen } from '@lucide/vue'

export default {
  extends: DefaultTheme,
  // Scratch 积木渲染触发器（D39 移植自本地维基，同款三触发：挂载/内容更新/路由切换）
  Layout: defineComponent({
    setup() {
      const route = useRoute()
      onMounted(() => {
        renderScratchBlocks()
      })
      // HMR / 切页 / frontmatter 变化都会触发；缺了这个 HMR 后会变纯文本！
      onContentUpdated(() => {
        renderScratchBlocks()
      })
      // 兜底：SPA 路由切换（watch 到 path 变化后再等一帧，DOM 更新完）
      watch(
        () => route.path,
        () => nextTick(() => renderScratchBlocks()),
      )
      return () => h(DefaultTheme.Layout)
    },
  }),
  enhanceApp({ app }) {
    app.component('IconHome', Home)
    app.component('IconBookOpen', BookOpen)
  },
}
