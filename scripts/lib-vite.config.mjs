import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import vue from '@vitejs/plugin-vue'
import dts from 'vite-plugin-dts'

/**
 * Shared Vite library preset for every `packages/*` workspace.
 *
 * Runtime libraries (vue, three, 3d-tiles-renderer, element-plus, other
 * `@cloudbim/*` packages) are always external: the host application installs
 * them once, which avoids duplicate Vue/Three instances and duplicate themes.
 */
export function createLibConfig(packageUrl, options = {}) {
  const root = dirname(fileURLToPath(packageUrl))
  const { external = [], assetsInlineLimit = 4096 } = options

  return {
    root,
    resolve: {
      // Package sources use the same `@/` root alias as the host app, which keeps
      // the migration of existing files mechanical.
      alias: { '@': resolve(root, 'src') },
    },
    plugins: [
      vue(),
      dts({
        tsconfigPath: resolve(root, 'tsconfig.json'),
        entryRoot: resolve(root, 'src'),
        outDir: resolve(root, 'dist'),
        include: ['src/**/*.ts', 'src/**/*.vue'],
        exclude: ['src/**/*.test.ts', 'src/**/*.spec.ts'],
        // 手写的 .d.ts（例如 instancePalette.d.ts）要一并复制，否则 d.ts 里的引用会悬空。
        copyDtsFiles: true,
      }),
    ],
    build: {
      target: 'esnext',
      outDir: resolve(root, 'dist'),
      emptyOutDir: true,
      sourcemap: true,
      cssCodeSplit: false,
      minify: false,
      // 小图标内联为 data URI，避免发布后的包在运行时依赖宿主的静态资源路径。
      assetsInlineLimit,
      lib: {
        entry: resolve(root, 'src/index.ts'),
        formats: ['es'],
        fileName: () => 'index.js',
        cssFileName: 'style',
      },
      rollupOptions: {
        external: (id) =>
          // 任何 `@cloudbim/*` 包都必须在宿主侧单实例化：打包进页面包会造成重复的
          // Vue/Three 实例与重复样式。注意包内 `@/` 别名（指向本包 src）不受影响。
          /^@cloudbim\//.test(id) ||
          external.some((name) => id === name || id.startsWith(`${name}/`)),
        output: {
          entryFileNames: 'index.js',
          chunkFileNames: 'chunks/[name]-[hash].js',
        },
      },
    },
  }
}

/** Runtime dependencies that must never be bundled into a package. */
export const peerExternals = [
  'vue',
  'vue-router',
  'three',
  '3d-tiles-renderer',
  'element-plus',
  '@element-plus/icons-vue',
]

export const coreExternal = '@cloudbim/viewer-core'
