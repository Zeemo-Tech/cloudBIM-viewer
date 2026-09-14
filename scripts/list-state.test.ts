import assert from 'node:assert/strict'
import test from 'node:test'
import { decodeListState } from '../src/features/workspace/listState.ts'
const defaults = { keyword: '', status: 'all', dateRange: null as [Date, Date] | null }
test('restores dates, filters and pagination without mutating defaults', () => {
  const result = decodeListState(JSON.stringify({filters:{keyword:'YB-1',dateRange:['2026-09-01T00:00:00Z','2026-09-12T00:00:00Z']},page:3,pageSize:20}), defaults,[10,20,50])
  assert.equal(result.filters.keyword,'YB-1');assert.ok(result.filters.dateRange?.[0] instanceof Date)
  assert.equal(result.page,3);assert.equal(result.pageSize,20);assert.equal(defaults.keyword,'')
})
test('corrupt preferences, invalid dates and page sizes cannot break a deep link', () => {
  for (const raw of ['broken','null',JSON.stringify({filters:{dateRange:['invalid','2026-01-01']},page:-1,pageSize:999})]) {
    assert.deepEqual(decodeListState(raw,defaults,[10,20,50]),{filters:defaults,page:1,pageSize:10})
  }
})
