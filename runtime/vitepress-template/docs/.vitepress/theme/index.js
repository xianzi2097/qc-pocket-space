import DefaultTheme from 'vitepress/theme'
import './custom.css'
import { Home, BookOpen } from '@lucide/vue'

export default {
  extends: DefaultTheme,
  enhanceApp({ app }) {
    app.component('IconHome', Home)
    app.component('IconBookOpen', BookOpen)
  }
}
