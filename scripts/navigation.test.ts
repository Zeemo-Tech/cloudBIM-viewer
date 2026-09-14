import assert from 'node:assert/strict'
import test from 'node:test'
import {
  getRouteInstanceKey,
  getViewerReturnLocation,
  navigateViewerBack,
  readNavigationRouteState,
} from '../src/router/navigation.ts'

test('normalizes malformed and legacy deep-link asset IDs', () => {
  const state = readNavigationRouteState('/alignment', {
    bimFileId: '42',
    pointcloudAssetId: 'not-a-number',
    projectId: '0',
  })

  assert.equal(state.bimAssetId, 42)
  assert.equal(state.pointcloudAssetId, null)
  assert.equal(state.projectId, null)
})

test('instance identity ignores display and return query changes', () => {
  const before = readNavigationRouteState('/preview/asset', {
    assetId: '7', previewType: 'bim', displayName: 'before.ifc', returnTo: '/design/bim?projectId=3',
  })
  const after = readNavigationRouteState('/preview/asset', {
    assetId: '7', previewType: 'bim', displayName: 'after.ifc', returnTo: '/survey?projectId=3',
  })

  assert.equal(getRouteInstanceKey(before), getRouteInstanceKey(after))
})

test('return context accepts only known internal workspace routes', () => {
  const accepted = readNavigationRouteState('/preview/asset', {
    projectId: '3', returnTo: '/design/overview?projectId=3',
  })
  const rejected = readNavigationRouteState('/preview/asset', {
    projectId: '3', returnTo: '/design/overview?projectId=4',
  })

  assert.equal(getViewerReturnLocation(accepted), '/design/overview?projectId=3')
  assert.deepEqual(getViewerReturnLocation(rejected), {
    path: '/design/bim', query: { projectId: '3' },
  })
})

test('missing viewer parameters and refreshed deep links replace to a safe fallback', () => {
  const missing = readNavigationRouteState('/preview/asset', { assetId: '0' })
  const refreshed = readNavigationRouteState('/preview/asset', {
    projectId: '8', previewType: 'pointcloud', assetId: '22', returnTo: '/survey?projectId=8',
  })
  const calls: string[] = []
  const router = {
    options: { history: { state: { back: '/unrelated' } } },
    back: () => calls.push('back'),
    replace: (target: unknown) => calls.push(`replace:${String(target)}`),
    resolve: (target: unknown) => ({ fullPath: String(target) }),
  } as never

  assert.equal(missing.assetId, null)
  assert.equal(getViewerReturnLocation(missing), '/projects')
  navigateViewerBack(router, refreshed)
  assert.deepEqual(calls, ['replace:/survey?projectId=8'])
})

test('closing a viewer uses the actual source history entry', () => {
  const state = readNavigationRouteState('/preview/split', {
    projectId: '2', returnTo: '/survey?projectId=2',
  })
  const calls: string[] = []
  const router = {
    options: { history: { state: { back: '/survey?projectId=2' } } },
    back: () => calls.push('back'),
    replace: () => calls.push('replace'),
  } as never
  navigateViewerBack(router, state)
  assert.deepEqual(calls, ['back'])
})

test('external, cross-project and fragment return URLs cannot override fallback', () => {
  for (const returnTo of ['https://example.com', '//example.com', '/survey?projectId=3', '/survey', '/survey?projectId=2#x', '/alignment?projectId=2']) {
    const state = readNavigationRouteState('/upload', { view: 'asset-preview', projectId: '2', returnTo })
    assert.equal(state.returnTo, undefined)
    assert.deepEqual(getViewerReturnLocation(state), {path:'/design/bim',query:{projectId:'2'}})
  }
  for (const assetId of ['-1', '1.2', '1e3', '9007199254740992', '0']) {
    assert.equal(readNavigationRouteState('/preview/asset', {assetId}).assetId, null)
  }
})
