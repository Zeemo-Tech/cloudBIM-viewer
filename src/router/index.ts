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
      path: '/projects',
      name: 'projects',
      component: RouteShell,
    },
    {
      path: '/survey',
      name: 'survey',
      component: RouteShell,
    },
    {
      path: '/design/bim',
      name: 'design-bim',
      component: RouteShell,
    },
    {
      path: '/design/cad',
      name: 'design-cad',
      component: RouteShell,
    },
    {
      path: '/design/overview',
      name: 'design-overview',
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
      path: '/alignment',
      name: 'alignment-page',
      component: RouteShell,
    },
    {
      path: '/alignment/model',
      name: 'alignment',
      component: RouteShell,
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/projects',
    },
  ],
})

export default router
