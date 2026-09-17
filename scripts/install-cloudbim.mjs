#!/usr/bin/env node
/**
 * CloudBIM 查看器包 · 一键安装
 * ============================================================================
 * 在宿主项目根目录执行：
 *
 *   node /path/to/dist-packages/install.mjs
 *   sh   /path/to/dist-packages/install.sh      # 等价包装
 *
 * 脚本依次完成：
 *   1. 读取 manifest.json（包清单 + peer 版本范围，无需解析 tarball）
 *   2. 探测包管理器（npm / pnpm / yarn）
 *   3. 把整套交付物落到 <项目>/.cloudbim/，让依赖可重定位、可重装
 *   4. 对齐 peer 依赖版本（缺失或超出范围时按声明范围安装）
 *   5. 一次性安装选中的 @cloudbim 包（同批次安装才能解析内部依赖）
 *   6. 校验 node_modules 下的产物入口
 *   7. 部署 three 的 Draco 解码器到静态目录
 *   8. 生成 src/cloudbim/setup.ts，并把入口 import 接入 main.ts
 *
 * 选项：
 *   --from <dir>           交付目录（默认：本脚本所在目录）
 *   --project <dir>        宿主项目根目录（默认：当前工作目录）
 *   --pm <npm|pnpm|yarn>   指定包管理器（默认自动探测）
 *   --features <list>      只安装指定功能（逗号分隔，例如 denoise 或 core,denoise）
 *                          依赖的前置包会自动补全；不传则安装全部包
 *   --list-features        列出交付目录内可用的功能后退出
 *   --no-peer              跳过 peer 依赖对齐
 *   --no-draco             跳过 Draco 解码器部署
 *   --no-patch             只生成初始化文件，不改动 main.ts
 *   --no-vendor            不复制交付物，直接用源目录路径安装
 *   --dry-run              只打印将要执行的命令
 *   --force                覆盖已存在的初始化文件
 *   -h, --help             显示帮助
 */

import { execFileSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const VENDOR_DIRNAME = '.cloudbim'

/* ------------------------------------------------------------------ 输出 --- */

const useColor = Boolean(process.stdout.isTTY) && !process.env.NO_COLOR
const paint = (code, text) => (useColor ? `\u001b[${code}m${text}\u001b[0m` : String(text))
const dim = (text) => paint('2', text)
const bold = (text) => paint('1', text)
const green = (text) => paint('32', text)
const yellow = (text) => paint('33', text)
const red = (text) => paint('31', text)
const cyan = (text) => paint('36', text)

let stepIndex = 0
const step = (text) => console.log(`\n${bold(cyan(`[${(stepIndex += 1)}]`))} ${bold(text)}`)
const info = (text) => console.log(`    ${text}`)
const ok = (text) => console.log(`    ${green('✓')} ${text}`)
const warn = (text) => console.log(`    ${yellow('!')} ${text}`)
const bad = (text) => console.log(`    ${red('✗')} ${text}`)

function fail(message) {
  console.error(`\n${red('安装中止：')}${message}\n`)
  process.exit(1)
}

/* -------------------------------------------------------------- 参数解析 --- */

const options = {
  from: scriptDir,
  project: process.cwd(),
  pm: null,
  features: null,
  listFeatures: false,
  peer: true,
  draco: true,
  patch: true,
  vendor: true,
  dryRun: false,
  force: false,
}

/** `--features denoise,core` / `--features=denoise` 都接受。 */
const parseFeatureList = (text) => String(text).split(/[,\s]+/).map((item) => item.trim()).filter(Boolean)

for (let i = 0; i < process.argv.slice(2).length; i += 1) {
  const flag = process.argv[2 + i]
  const value = () => {
    const next = process.argv[3 + i]
    if (next === undefined) fail(`${flag} 需要一个参数`)
    i += 1
    return next
  }
  switch (flag) {
    case '--from': options.from = resolve(value()); break
    case '--project': options.project = resolve(value()); break
    case '--pm':
    case '--package-manager': options.pm = value(); break
    case '--features': options.features = parseFeatureList(value()); break
    case '--list-features': options.listFeatures = true; break
    case '--no-peer': options.peer = false; break
    case '--no-draco': options.draco = false; break
    case '--no-patch': options.patch = false; break
    case '--no-vendor': options.vendor = false; break
    case '--dry-run': options.dryRun = true; break
    case '--force': options.force = true; break
    case '-h':
    case '--help': printHelp(); process.exit(0); break
    default:
      if (flag.startsWith('--features=')) {
        options.features = parseFeatureList(flag.slice('--features='.length))
        break
      }
      fail(`未知参数 ${flag}（用 --help 查看用法）`)
  }
}

function printHelp() {
  const text = readFileSync(fileURLToPath(import.meta.url), 'utf8')
  console.log(text.slice(text.indexOf('/**') + 3, text.indexOf('*/')).replace(/^ ?\*?/gm, '').trim())
}

/* ------------------------------------------------------------ 版本比较 --- */

function parseVersion(value) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)/.exec(String(value ?? '').trim())
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null
}

function compare(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1
  }
  return 0
}

function bump(tuple, index) {
  const next = [...tuple]
  next[index] += 1
  for (let i = index + 1; i < 3; i += 1) next[i] = 0
  return next
}

function matchComparator(version, comparator) {
  if (!comparator || comparator === '*' || comparator === 'x' || comparator === 'latest') return true
  const match = /^(\^|~|>=|<=|>|<|=)?\s*v?(\d+)(?:\.(\d+|[x*]))?(?:\.(\d+|[x*]))?/.exec(comparator)
  if (!match) return true // 无法识别的写法：放行，避免误拦
  const operator = match[1] ?? '='
  const major = Number(match[2])
  const minor = match[3] === undefined || /[x*]/.test(match[3]) ? null : Number(match[3])
  const patch = match[4] === undefined || /[x*]/.test(match[4]) ? null : Number(match[4])
  const lower = [major, minor ?? 0, patch ?? 0]

  if (operator === '>=') return compare(version, lower) >= 0
  if (operator === '>') return compare(version, lower) > 0
  if (operator === '<') return compare(version, lower) < 0
  if (operator === '<=') return compare(version, lower) <= 0

  // ^ ~ = ：0.x 的 caret 语义特殊，只需递增对应位
  let upper
  if (operator === '^') {
    if (major > 0) upper = bump([major, 0, 0], 0)
    else if (minor === null) upper = [1, 0, 0]
    else upper = bump([0, minor, 0], 1)
  } else if (operator === '~') {
    upper = minor === null ? bump([major, 0, 0], 0) : bump([major, minor, 0], 1)
  } else if (minor === null) {
    upper = bump([major, 0, 0], 0)
  } else if (patch === null) {
    upper = bump([major, minor, 0], 1)
  } else {
    upper = bump(lower, 2)
  }
  return compare(version, lower) >= 0 && compare(version, upper) < 0
}

function satisfies(version, range) {
  const parsed = parseVersion(version)
  if (!parsed) return false
  return String(range)
    .split('||')
    .some((branch) =>
      branch
        .trim()
        .split(/\s+/)
        .filter(Boolean)
        .every((comparator) => matchComparator(parsed, comparator)),
    )
}

/* -------------------------------------------------------------- 小工具 --- */

function readJson(file) {
  if (!existsSync(file)) return null
  try {
    return JSON.parse(readFileSync(file, 'utf8'))
  } catch {
    return null
  }
}

function fileSizeText(bytes) {
  if (!bytes) return ''
  return bytes > 1024 * 1024
    ? `${(bytes / 1024 / 1024).toFixed(1)}MB`
    : `${Math.round(bytes / 1024)}KB`
}

function detectPackageManager(projectDir) {
  if (options.pm) {
    if (!['npm', 'pnpm', 'yarn'].includes(options.pm)) fail(`不支持的包管理器 ${options.pm}`)
    return options.pm
  }
  const declared = String(readJson(join(projectDir, 'package.json'))?.packageManager ?? '')
  const name = declared.split('@')[0]
  if (['npm', 'pnpm', 'yarn'].includes(name)) return name
  if (existsSync(join(projectDir, 'pnpm-lock.yaml'))) return 'pnpm'
  if (existsSync(join(projectDir, 'yarn.lock'))) return 'yarn'
  return 'npm'
}

function runAdd(pm, specs, projectDir) {
  if (!specs.length) return
  const bin = process.platform === 'win32' ? `${pm}.cmd` : pm
  const args = pm === 'npm' ? ['install', '--save', ...specs, '--no-audit', '--no-fund'] : ['add', ...specs]
  info(dim(`$ ${bin} ${args.join(' ')}`))
  if (options.dryRun) return
  execFileSync(bin, args, { cwd: projectDir, stdio: 'inherit' })
}

function installedVersion(projectDir, name) {
  const file = join(projectDir, 'node_modules', ...name.split('/'), 'package.json')
  return readJson(file)?.version ?? null
}

function detectPublicDir(projectDir) {
  for (const name of ['vite.config.ts', 'vite.config.mts', 'vite.config.js', 'vite.config.mjs']) {
    const file = join(projectDir, name)
    if (!existsSync(file)) continue
    const text = readFileSync(file, 'utf8')
    if (/publicDir\s*:\s*false/.test(text)) return null
    const match = /publicDir\s*:\s*['"`]([^'"`]+)['"`]/.exec(text)
    if (match) return resolve(projectDir, match[1])
  }
  if (existsSync(join(projectDir, 'public'))) return join(projectDir, 'public')
  if (existsSync(join(projectDir, 'src'))) return join(projectDir, 'public')
  return null
}

/* ------------------------------------------------------------ 初始化文件 --- */

function buildSetupSource(packages) {
  const styled = packages.filter((pkg) => pkg.hasStyle)
  const core = styled.find((pkg) => pkg.name === '@cloudbim/viewer-core')
  const others = styled.filter((pkg) => pkg.name !== '@cloudbim/viewer-core')
  const styleLines = [
    ...(core ? [`import '${core.name}/style.css'`] : []),
    ...others.map((pkg) => `import '${pkg.name}/style.css'`),
  ].join('\n')

  return `/**
 * CloudBIM 查看器包 · 运行时初始化
 * 由 \`dist-packages/install.mjs\` 生成，可自行修改。
 *
 * 只需在应用入口引入一次即可（安装脚本已自动接入 main.ts）：
 *   import './cloudbim/setup'
 * 并确认已注册 Element Plus：
 *   app.use(ElementPlus)
 */
import { configureCloudBim } from '@cloudbim/viewer-core'

// 库模式构建会把 SFC 样式抽取为独立 style.css，包不会自动注入，必须显式引入，
// 否则组件渲染正常但完全没有样式。viewer-core 的样式里含设计令牌（--color-primary 等），
// 需排在 Element Plus 之后加载，因此本文件应位于入口的最后一条 import。
${styleLines}

const env = ((import.meta as any).env ?? {}) as Record<string, string | boolean | undefined>

type CloudBimRuntimeOptions = Partial<Parameters<typeof configureCloudBim>[0]>

/** 再次调用可覆盖默认配置（例如切换后端地址或接入宿主登录态）。 */
export function setupCloudBim(overrides: CloudBimRuntimeOptions = {}): void {
  configureCloudBim({
    baseUrl: (env.VITE_API_BASE_URL as string) ?? '', // 留空 = 与宿主同源
    getAccessToken: () => globalThis.sessionStorage?.getItem('cloudbim_token') ?? '', // 返回空则由后端 Cookie 鉴权
    dracoDecoderPath: '/draco/', // 宿主静态目录，由安装脚本部署
    debug: Boolean(env.DEV),
    ...overrides,
  })
}

setupCloudBim()
`
}

function writeSetupFile(projectDir, packages) {
  const srcDir = join(projectDir, 'src')
  if (!existsSync(srcDir)) {
    warn('未找到 src/ 目录，跳过初始化文件生成')
    return null
  }
  const targetDir = join(srcDir, 'cloudbim')
  const target = join(targetDir, 'setup.ts')
  if (existsSync(target) && !options.force) {
    warn(`已存在 ${dim('src/cloudbim/setup.ts')}，保留原文件（--force 可覆盖）`)
    return target
  }
  if (options.dryRun) {
    info(dim(`$ write src/cloudbim/setup.ts`))
    return target
  }
  mkdirSync(targetDir, { recursive: true })
  writeFileSync(target, buildSetupSource(packages), 'utf8')
  ok(`生成 ${dim('src/cloudbim/setup.ts')}`)
  return target
}

function patchMainEntry(projectDir) {
  const candidates = ['src/main.ts', 'src/main.js', 'src/main.mts', 'src/main.mjs']
  const entry = candidates.map((name) => join(projectDir, name)).find((file) => existsSync(file))
  if (!entry) {
    warn('未找到 src/main.ts，请手动在应用入口引入：import \'./cloudbim/setup\'')
    return false
  }
  const relative = entry.slice(projectDir.length + 1)
  const source = readFileSync(entry, 'utf8')
  if (source.includes('cloudbim/setup')) {
    ok(`${dim(relative)} 已接入初始化文件`)
    return true
  }
  if (options.dryRun) {
    info(dim(`$ patch ${relative}`))
    return true
  }

  // 插到最后一条 import 之后：ESM 按声明顺序求值，包样式必须排在 Element Plus 之后。
  const lines = source.split('\n')
  let insertAt = -1
  lines.forEach((line, index) => {
    const trimmed = line.trim()
    if (!trimmed) return
    if (/^import\b/.test(trimmed) || /^}\s*from\s+['"]/.test(trimmed) || /^from\s+['"]/.test(trimmed)) {
      insertAt = index
    }
  })
  const patched = [...lines]
  patched.splice(insertAt + 1, 0, "import './cloudbim/setup'")
  writeFileSync(entry, patched.join('\n'), 'utf8')

  ok(
    `已接入 ${dim(relative)}：在第 ${insertAt + 2} 行新增 ${dim("import './cloudbim/setup'")}` +
      `${insertAt < 0 ? '（文件无 import，已置于顶部）' : ''}`,
  )
  if (!/element-plus/.test(source)) {
    warn('未检测到 Element Plus，请确认已安装并在入口注册：app.use(ElementPlus)')
  }
  return true
}

function ensureGitignore(projectDir) {
  const file = join(projectDir, '.gitignore')
  const line = `${VENDOR_DIRNAME}/`
  const current = existsSync(file) ? readFileSync(file, 'utf8') : ''
  if (current.split('\n').some((row) => row.trim() === line)) return
  if (options.dryRun) {
    info(dim(`$ append .gitignore: ${line}`))
    return
  }
  writeFileSync(file, `${current && !current.endsWith('\n') ? `${current}\n` : current}${line}\n`, 'utf8')
}

/* ------------------------------------------------------------------ 包选择 --- */

const availableFeatures = (packages) => [
  ...new Set(packages.map((pkg) => pkg.feature ?? pkg.name)),
]

/** `--features` 指定的功能 → 包集合（含 @cloudbim/* 依赖闭包），保持 manifest 顺序。 */
function selectPackages(manifest, features) {
  const all = manifest.packages ?? []
  if (!features?.length) return all

  const byKey = new Map()
  for (const pkg of all) {
    byKey.set(pkg.name, pkg)
    if (pkg.feature) byKey.set(pkg.feature, pkg)
  }
  const unknown = features.filter((feature) => !byKey.has(feature))
  if (unknown.length) {
    fail(
      `未知功能：${unknown.join('、')}\n可用功能：${availableFeatures(all).join('、')}`,
    )
  }

  const selected = new Set()
  const include = (pkg) => {
    if (!pkg || selected.has(pkg.name)) return
    selected.add(pkg.name)
    // 功能包可能依赖前置包（例如 alignment → denoise → viewer-core），一并补全。
    for (const dep of pkg.dependencies ?? []) include(all.find((item) => item.name === dep))
  }
  for (const feature of features) include(byKey.get(feature))
  return all.filter((pkg) => selected.has(pkg.name))
}

/* ------------------------------------------------------------------ 主流程 --- */

console.log(bold(`\nCloudBIM 查看器包 · 一键安装`))
console.log(dim(`交付目录：${options.from}`))
console.log(dim(`目标项目：${options.project}`))
if (options.dryRun) console.log(yellow('（dry-run：只打印命令，不做任何改动）'))

step('读取交付清单')
const manifest = readJson(join(options.from, 'manifest.json'))
if (!manifest?.packages?.length) {
  fail(
    `未找到 ${join(options.from, 'manifest.json')}。\n` +
      '请先在包源码仓库执行 npm run pack:packages，再把整个 dist-packages 目录交付给宿主。',
  )
}
ok(`共 ${manifest.packages.length} 个包 · 版本 ${manifest.version}`)

if (options.listFeatures) {
  console.log('')
  for (const pkg of manifest.packages) {
    console.log(`    ${cyan(pkg.feature ?? pkg.name)}  ${dim(pkg.name)}  ${dim(pkg.description ?? '')}`)
  }
  console.log('')
  process.exit(0)
}

const selectedPackages = selectPackages(manifest, options.features)
if (options.features) {
  ok(
    `按功能安装：${options.features.join('、')} → ${selectedPackages.length} 个包` +
      dim(`（${selectedPackages.map((pkg) => pkg.name).join('、')}）`),
  )
}

const projectPkgPath = join(options.project, 'package.json')
if (!existsSync(projectPkgPath)) fail(`${options.project} 下没有 package.json，请用 --project 指定前端项目根目录`)
ok(`宿主项目 ${readJson(projectPkgPath).name ?? '（未命名）'}`)

const missingTarballs = selectedPackages.filter((pkg) => !existsSync(join(options.from, pkg.file)))
if (missingTarballs.length) fail(`交付目录缺少 tarball：${missingTarballs.map((pkg) => pkg.file).join(', ')}`)

const pm = detectPackageManager(options.project)
ok(`包管理器：${pm}${options.pm ? '（手动指定）' : '（自动探测）'}`)

step(`复制交付物到 ${VENDOR_DIRNAME}/`)
let installFrom = options.from
if (options.vendor) {
  const vendorDir = join(options.project, VENDOR_DIRNAME)
  if (options.dryRun) {
    info(dim(`$ copy ${options.from} -> ${vendorDir}`))
  } else {
    mkdirSync(vendorDir, { recursive: true })
    for (const pkg of manifest.packages) {
      cpSync(join(options.from, pkg.file), join(vendorDir, pkg.file))
    }
    cpSync(join(options.from, 'manifest.json'), join(vendorDir, 'manifest.json'))
    cpSync(fileURLToPath(import.meta.url), join(vendorDir, 'install.mjs'))
    if (manifest.draco?.files?.length) {
      for (const file of manifest.draco.files) {
        const source = join(options.from, manifest.draco.dir, file)
        if (!existsSync(source)) continue
        mkdirSync(join(vendorDir, manifest.draco.dir), { recursive: true })
        cpSync(source, join(vendorDir, manifest.draco.dir, file))
      }
    }
  }
  installFrom = vendorDir
  ok(`已落盘 ${dim(`${VENDOR_DIRNAME}/`)}（可随时用 ${dim(`node ${VENDOR_DIRNAME}/install.mjs`)} 重装）`)
  ensureGitignore(options.project)
} else {
  info(dim('已跳过（--no-vendor）'))
}

const toSpec = (file) => {
  const absolute = resolve(installFrom, file)
  if (!options.vendor) return `file:${absolute}`
  return `file:./${VENDOR_DIRNAME}/${file}`
}
const packageSpecs = selectedPackages.map((pkg) => toSpec(pkg.file))

if (options.peer) {
  step('对齐 peer 依赖')
  const peers = Object.entries(manifest.peers ?? {})
  if (!peers.length) info(dim('清单未声明 peer 依赖，跳过'))
  const toInstall = []
  for (const [name, range] of peers) {
    const current = installedVersion(options.project, name)
    if (!current) {
      toInstall.push(`${name}@${range}`)
      info(`${name} ${dim('未安装')} → 安装 ${cyan(range)}`)
    } else if (!satisfies(current, range)) {
      toInstall.push(`${name}@${range}`)
      warn(`${name}@${current} 不满足 ${cyan(range)} → 调整为该范围（0.x 的 caret 范围很窄，务必确认兼容）`)
    } else {
      ok(`${name}@${current}`)
    }
  }
  if (toInstall.length) runAdd(pm, toInstall, options.project)
  else info(dim('peer 依赖已全部满足'))
} else {
  step('对齐 peer 依赖')
  info(dim('已跳过（--no-peer）'))
}

step(`安装 ${selectedPackages.length} 个 @cloudbim 包`)
info(dim('必须同批次安装：功能包依赖 @cloudbim/viewer-core，registry 中没有该包，靠同批 tarball 解析'))
runAdd(pm, packageSpecs, options.project)

step('校验安装结果')
if (options.dryRun) {
  info(dim('dry-run 跳过校验'))
} else {
  const failures = []
  for (const pkg of selectedPackages) {
    const dir = join(options.project, 'node_modules', ...pkg.name.split('/'))
    const entry = join(dir, 'dist', 'index.js')
    const types = join(dir, 'dist', 'index.d.ts')
    if (!existsSync(entry)) failures.push(`${pkg.name} 缺少 dist/index.js`)
    else if (!existsSync(types)) failures.push(`${pkg.name} 缺少 dist/index.d.ts`)
    else ok(`${pkg.name}@${installedVersion(options.project, pkg.name) ?? pkg.version}`)
  }
  if (failures.length) fail(`校验失败：\n  - ${failures.join('\n  - ')}`)
}

if (options.draco) {
  step('部署 Draco 解码器')
  const files = manifest.draco?.files ?? []
  const publicDir = detectPublicDir(options.project)
  if (!files.length) {
    warn('交付清单中没有 Draco 文件，跳过')
  } else if (!publicDir) {
    warn(`未找到静态目录，请手动把 ${manifest.draco.dir}/ 放到 ${manifest.dracoPath ?? '/draco/'}`)
  } else {
    const targetDir = join(publicDir, manifest.dracoPath ?? 'draco')
    if (options.dryRun) {
      info(dim(`$ copy ${manifest.draco.dir}/ -> ${targetDir}`))
    } else {
      mkdirSync(targetDir, { recursive: true })
      for (const file of files) {
        const source = join(options.from, manifest.draco.dir, file)
        if (existsSync(source)) cpSync(source, join(targetDir, file))
      }
    }
    ok(`${files.length} 个文件 → ${dim(`${targetDir.slice(options.project.length + 1)}/`)}`)
  }
} else {
  step('部署 Draco 解码器')
  info(dim('已跳过（--no-draco）'))
}

step('生成运行时初始化文件')
writeSetupFile(options.project, selectedPackages)
if (options.patch) patchMainEntry(options.project)
else info(dim('已跳过 main.ts 接入（--no-patch）'))

/* ------------------------------------------------------------------ 收尾 --- */

console.log(`\n${bold(green('安装完成'))}`)
console.log(`
接入示例（props 进、事件出，包内部不使用 vue-router）：

  ${dim("import { AlignmentPage } from '@cloudbim/alignment'")}
  ${dim('<AlignmentPage :bim-asset-id="49" :pointcloud-asset-id="60" :initial-step="1"')}
  ${dim('  :bim-display-name="\'model.glb\'" :pointcloud-display-name="\'scan.las\'"')}
  ${dim('  @back="router.push(\'/survey\')" @step-change="(s) => ..." />')}

仍需人工确认的三件事：
  1. Element Plus 已安装并在入口注册：${dim('app.use(ElementPlus)')}
  2. 后端实现了包所需接口（响应包络 {code,msg,data}）：
${selectedPackages
  .filter((pkg) => pkg.endpoints?.length)
  .map((pkg) => `       ${pkg.name}：${pkg.endpoints.join('、')}`)
  .join('\n')}
  3. 部署时 ${dim(`${manifest.dracoPath ?? 'draco'}/`)} 需随静态资源一起发布

只想要部分功能时，可在本目录重跑安装并指定功能，例如：
  ${dim(`node ${VENDOR_DIRNAME}/install.mjs --features=denoise`)}
可用功能：${dim(availableFeatures(manifest.packages).join('、'))}

详细用法见交付目录的 ${dim('README.md')} 或仓库 docs/development/packages-usage.md
`)
