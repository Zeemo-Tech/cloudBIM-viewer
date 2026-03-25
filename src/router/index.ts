import { createRouter, createWebHistory } from 'vue-router'
import { h } from 'vue'

const RouteShell = {
  name: 'RouteShell',
  render: () => h('div'),
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'root',
      component: RouteShell,
    },
    {
      path: '/upload',
      name: 'upload',
      component: RouteShell,
    },
    {
      path: '/preview/asset',
      name: 'asset-preview',
      component: RouteShell,
    },
    {
      path: '/preview/split',
      name: 'split-preview',
      component: RouteShell,
    },
    {
      path: '/alignment/model',
      name: 'alignment',
      component: RouteShell,
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/upload',
    },
  ],
})

export default router
