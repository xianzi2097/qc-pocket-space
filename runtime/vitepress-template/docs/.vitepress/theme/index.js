import DefaultTheme from 'vitepress/theme'
import './custom.css'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    // 全局注册可视化组件，.md 里可直接使用
    // app.component('ComponentName', Component)
  }
}
