import { createApp } from 'vue'
import ElementPlus from 'element-plus'

// Element Plus 样式必须先于包样式加载：包内样式含设计令牌（--color-primary 等），需要能覆盖它。
import 'element-plus/dist/index.css'
import './cloudbim/setup'

import App from './App.vue'

createApp(App).use(ElementPlus).mount('#app')
