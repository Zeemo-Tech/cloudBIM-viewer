import type { RebarComparisonBar, RebarProfileSample } from '@cloudbim/viewer-core'

type DiagnosticLine = { label: string; text: string; detail?: string }
export interface OperatorDiagnosis { title: string; lines: DiagnosticLine[]; coverage?: string; reference?: string }
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)
const vector = (v: unknown): v is [number, number, number] => Array.isArray(v) && v.length === 3 && v.every(finite)
const mm = (metres: number) => (Math.abs(metres) * 1000).toFixed(1)

// Fixed to this viewer's untransformed PLY and canonical top view: screen right
// is +X, screen down is +Z, raised is +Y. Do not apply AlignmentPage's separate
// engineeringRoot rotation. The direction is observed minus design, not a
// correction instruction; cross-section fitting does not measure axial slip.
export function operatorOffset(sample: RebarProfileSample): [number, number, number] | null {
  if (sample.quality !== 'supported' || !vector(sample.designCenterM) || !vector(sample.observedCenterM)) return null
  if (vector(sample.offsetVectorM)) return sample.offsetVectorM
  return sample.observedCenterM.map((value, i) => value - sample.designCenterM[i]!) as [number, number, number]
}
export function operatorOffsetText(offset: [number, number, number]) {
  const [x, y, z] = offset
  const planar: string[] = []
  // Below the displayed tenth-millimetre precision, don't invent a direction.
  if (Math.abs(x) >= .00005) planar.push(`${x > 0 ? '向右' : '向左'} ${mm(x)} mm`)
  if (Math.abs(z) >= .00005) planar.push(`${z > 0 ? '向下' : '向上'} ${mm(z)} mm`)
  const height = Math.abs(y) < .00005 ? '高度差小于0.1 mm' : `${y > 0 ? '抬高' : '降低'} ${mm(y)} mm`
  return `俯视${planar.length ? planar.join('、') : '平面偏差小于0.1 mm'}；${height}`
}

export function operatorDiagnosis(bar: RebarComparisonBar, state: string): OperatorDiagnosis {
  if (state === 'missing') return { title: '缺测，需补扫', lines: [
    { label: '现场检查', text: '核对钢筋是否在位，清理遮挡后补扫。' },
  ] }
  if (state === 'review') return { title: '测量结果待复核', lines: [
    { label: '现场检查', text: '请质量人员核对测量结果，安排补扫。' },
  ] }
  const measurement = bar.measurement, profile = measurement?.longitudinalProfile ?? []
  const supported = profile.flatMap(sample => {
    const offset = operatorOffset(sample)
    return offset ? [{ sample, offset, distance: Math.hypot(...offset) }] : []
  })
  const worst = supported.reduce<(typeof supported)[number] | null>((a, b) => !a || b.distance > a.distance ? b : a, null)
  const lines: DiagnosticLine[] = [{ label: '位置 · 局部最大偏移', text: worst ? operatorOffsetText(worst.offset) : '待补扫' }]
  const diameter = supported.map(({ sample }) => sample).filter(sample => finite(sample.radiusM) && sample.radiusM > 0 && finite(sample.radiusDeltaM) && sample.radiusM - sample.radiusDeltaM > 0)
    .reduce<RebarProfileSample | null>((a, b) => !a || Math.abs(b.radiusDeltaM!) > Math.abs(a.radiusDeltaM!) ? b : a, null)
  if (diameter) {
    const delta = diameter.radiusDeltaM! * 2, actual = diameter.radiusM! * 2, design = actual - delta
    const difference = Math.abs(delta) < .00005 ? '直径差小于0.1 mm' : `较设计${delta > 0 ? '粗' : '细'} ${mm(delta)} mm`
    lines.push({ label: '直径 · 局部最大差', text: `约 ${mm(actual)} mm（设计 ${mm(design)} mm），${difference}。` })
  } else lines.push({ label: '直径', text: '待补扫' })
  const bending = measurement?.bending
  if (bending?.quality === 'supported' && finite(bending.residualBowM) && bending.residualBowM >= 0)
    lines.push({ label: '弯曲', text: `弯曲偏离 ${mm(bending.residualBowM)} mm。` })
  else lines.push({ label: '弯曲', text: '待补扫' })
  return {
    title: state === 'outlier' ? '表面偏差超阈值' : '表面偏差在阈值内', lines,
    coverage: profile.length ? `有效截面 ${supported.length} / ${profile.length}` : '截面数据待补充',
    reference: '左右、上下参照俯视；抬高、降低表示高度方向。',
  }
}
