import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import test from 'node:test'
import axios from 'axios'
import ts from 'typescript'

// Exercise the real response interceptor and request wrapper with Axios binary
// responses, without requiring browser auth storage or Vite aliases.
const source = readFileSync(new URL('./backend-http.ts', import.meta.url), 'utf8')
const ast = ts.createSourceFile('http.ts', source, ts.ScriptTarget.Latest, true)
function clientFor(data: unknown) {
  const client = axios.create({ adapter: async config => {
    throw new axios.AxiosError('Request failed with status code 409', 'ERR_BAD_REQUEST', config, undefined,
      { data, status: 409, statusText: 'Conflict', headers: { 'content-type': 'application/json' }, config })
  } })
  const code = ast.statements.filter(node =>
    (ts.isFunctionDeclaration(node) && ['createApiError', 'decodeApiError', 'extractErrorMessage', 'backendRequestRaw'].includes(node.name?.text ?? '')) ||
    (ts.isExpressionStatement(node) && node.getText(ast).startsWith('backendClient.interceptors.response.use(')),
  ).map(node => node.getText(ast).replace(/^export /, '')).join('\n')
  return runInNewContext(ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText + '\nbackendRequestRaw',
    { axios, backendClient: client, Blob, ArrayBuffer, TextDecoder, Error })
}

for (const format of ['json', 'blob', 'arraybuffer']) test(`preserve server conflict reason in ${format} responses`, async () => {
  const body = { code: 409, msg: 'C2M 结果版本已变化，请刷新后重试' }
  const data = format === 'blob' ? new Blob([JSON.stringify(body)], { type: 'application/json' })
    : format === 'arraybuffer' ? new TextEncoder().encode(JSON.stringify(body)).buffer : body
  await assert.rejects(clientFor(data)('/artifact', { responseType: format }), (error: any) => {
    assert.equal(error.message, body.msg)
    assert.equal(error.response.status, 409)
    assert.equal(error.response.data.msg, body.msg)
    return true
  })
})

test('non-JSON download failure retains HTTP status and fallback message', async () => {
  await assert.rejects(clientFor(new Blob(['unavailable']))('/artifact'), (error: any) => {
    assert.equal(error.response.status, 409)
    assert.equal(error.message, 'Request failed with status code 409')
    return true
  })
})
