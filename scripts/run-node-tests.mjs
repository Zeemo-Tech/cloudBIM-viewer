// Runs the Node-based verification suites. Each file is spawned separately so
// platform-specific suites can skip cleanly (see the SKIP guards) without
// aborting the rest of the run.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))

// Keep this list explicit: only files that are maintained and green.
const suites = [
  'list-state.test.ts',
  'navigation.test.ts',
  'test_alignment_transform_state.mjs',
  'test_c2m_search_settings.mjs',
  'test_c2m_colormap.mjs',
  'test_ground_grid.mjs',
  'test_rebar_debug_scene.mjs',
  'test_bim_remesh_preview.mjs',
  'test_pointcloud_instance_palette.mjs',
  'test_pointcloud_table_toggle.mjs',
  'test_pointcloud_rebar_extension_viewer.mjs',
  'test_edl_color_output.mjs',
  'test_edl_browser.mjs',
]

function run(file) {
  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      ['--experimental-strip-types', join(here, file)],
      { stdio: 'inherit' },
    )
    child.on('error', () => resolve({ file, code: 1 }))
    child.on('exit', (code) => resolve({ file, code: code ?? 1 }))
  })
}

const results = []
for (const file of suites) {
  console.log(`\n=== ${file} ===`)
  results.push(await run(file))
}

const failed = results.filter((result) => result.code !== 0)
console.log('\n===== summary =====')
for (const result of results) {
  console.log(`${result.code === 0 ? 'PASS' : 'FAIL'}  ${result.file}`)
}
if (failed.length) {
  console.error(`\n${failed.length} suite(s) failed.`)
  process.exit(1)
}
console.log('\nAll node verification suites passed (platform-gated suites may have skipped).')
