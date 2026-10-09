import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
const compile = path => ts.transpileModule(fs.readFileSync(new URL(path, import.meta.url), 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
const storage = { exports: {} }
vm.runInNewContext(compile('../src/features/auth/auth.storage.ts'), storage)
for (const fail of [false, true]) {
  storage.exports.setMemoryAccessToken('test-session-token')
  const logoutFailure = new Error('network unavailable')
  let calls = 0
  const service = { exports: {}, require: name => {
    if (name === './auth.storage') return storage.exports
    if (name === '@/api/backend-auth') return { logout: async () => { calls++; if (fail) throw logoutFailure } }
    throw new Error(`Unexpected import ${name}`)
  } }
  vm.runInNewContext(compile('../src/features/auth/auth.service.ts'), service)
  if (fail) await assert.rejects(service.exports.logoutCurrentSession(), error => error === logoutFailure)
  else await service.exports.logoutCurrentSession()
  assert.equal(calls, 1)
  assert.equal(storage.exports.getStoredAccessToken(), '', 'logout must clear the actual in-memory access token even when the network fails')
}
console.log('PASS: successful and failed logout both clear the in-memory bearer token')
