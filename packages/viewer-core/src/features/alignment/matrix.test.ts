import assert from 'node:assert/strict'
import test from 'node:test'
import { Euler, Matrix4, Object3D, Quaternion, Vector3 } from 'three'
// @ts-ignore Node 的 strip-types 运行器需要显式源码扩展名。
import {
  alignmentMatrixFromResult,
  desiredBimWorldMatrix,
  modelPairsFromRigidTransform,
  rawWorldMatrixForAlignment,
  recordNormalizationOffset,
  rigidPartOf,
  scanToBimRigidTransform,
  toLocalMatrix,
} from './matrix.ts'

const near = (actual: Matrix4, expected: Matrix4, tolerance = 1e-9) => {
  const a = actual.toArray()
  const b = expected.toArray()
  for (let i = 0; i < 16; i += 1) {
    assert.ok(
      Math.abs(a[i] - b[i]) < tolerance,
      `元素 ${i} 不一致：${a[i]} vs ${b[i]}`,
    )
  }
}

test('rigidPartOf 去掉缩放只保留旋转平移', () => {
  const matrix = new Matrix4().compose(
    new Vector3(1.5, -2, 3),
    new Quaternion().setFromEuler(new Euler(0.3, -0.7, 1.1)),
    new Vector3(2.5, 2.5, 2.5),
  )
  const rigid = rigidPartOf(matrix)
  const scale = new Vector3()
  rigid.decompose(new Vector3(), new Quaternion(), scale)
  assert.deepEqual(scale.toArray().map((v) => Number(v.toFixed(9))), [1, 1, 1])
})

test('modelPairsFromRigidTransform 输出三个基准点对且语义为 scan → bim', () => {
  // 纯平移 (10, 20, 30)：三个点对应整体平移。
  const rigid = new Matrix4().makeTranslation(10, 20, 30)
  const pairs = modelPairsFromRigidTransform(rigid)
  assert.equal(pairs.length, 3)
  assert.deepEqual(
    pairs.map((pair) => [pair.modelScanX, pair.modelScanY, pair.modelScanZ]),
    [
      [0, 0, 0],
      [1, 0, 0],
      [0, 1, 0],
    ],
  )
  assert.deepEqual(
    pairs.map((pair) => [pair.modelBimX, pair.modelBimY, pair.modelBimZ]),
    [
      [10, 20, 30],
      [11, 20, 30],
      [10, 21, 30],
    ],
  )
})

test('90° 绕 Y 轴旋转的点对能还原出同一个刚体变换', () => {
  const rigid = new Matrix4().compose(
    new Vector3(1, 2, 3),
    new Quaternion().setFromEuler(new Euler(0, Math.PI / 2, 0)),
    new Vector3(1, 1, 1),
  )
  const pairs = modelPairsFromRigidTransform(rigid)
  // 由点对重建：原点对应平移，另两点给出 X/Y 基向量。
  const [origin, axisX, axisY] = pairs
  const rebuilt = new Matrix4().makeBasis(
    new Vector3(axisX.modelBimX - origin.modelBimX, axisX.modelBimY - origin.modelBimY, axisX.modelBimZ - origin.modelBimZ),
    new Vector3(axisY.modelBimX - origin.modelBimX, axisY.modelBimY - origin.modelBimY, axisY.modelBimZ - origin.modelBimZ),
    new Vector3(0, 0, 1).applyMatrix4(rigid).sub(new Vector3(origin.modelBimX, origin.modelBimY, origin.modelBimZ)),
  )
  rebuilt.setPosition(new Vector3(origin.modelBimX, origin.modelBimY, origin.modelBimZ))
  // 旋转分量一致即可（Z 基向量由叉积确定，此处只比对前两列与平移）。
  near(rigidPartOf(rebuilt), rigid, 1e-9)
})

test('保存 → 恢复往返闭环：恢复后的 BIM 世界矩阵与原始一致（含居中归一化）', () => {
  // 点云世界矩阵：模拟查看器 wrapper 的 -90° 姿态修正 + 平移。
  const pointcloudWorld = new Matrix4().compose(
    new Vector3(-4, 1.25, 8),
    new Quaternion().setFromEuler(new Euler(-Math.PI / 2, 0, 0)),
    new Vector3(1, 1, 1),
  )

  // BIM：几何被居中后挂在 pivot 上（matrixWorld 含居中平移，userData 记录原始中心）。
  const normalizationCenter = new Vector3(12.5, -3.25, 6.75)
  const bimPivot = new Object3D()
  bimPivot.matrix.copy(
    new Matrix4().compose(
      new Vector3(2, 0.5, -1),
      new Quaternion().setFromEuler(new Euler(0.12, 0.9, -0.35)),
      new Vector3(1, 1, 1),
    ),
  )
  bimPivot.matrixAutoUpdate = false
  bimPivot.updateMatrixWorld(true)
  recordNormalizationOffset(bimPivot, normalizationCenter, 'child')

  // 1) 保存：算出 scan → bim 刚体变换，并展开成后端入参。
  const rigid = scanToBimRigidTransform(
    rawWorldMatrixForAlignment(bimPivot),
    rawWorldMatrixForAlignment(pointcloudWorld),
  )
  const pairs = modelPairsFromRigidTransform(rigid)

  // 后端回显：用点对还原的矩阵作为 modelMatrix 返回。
  const [origin, axisX, axisY] = pairs
  const originVector = new Vector3(origin.modelBimX, origin.modelBimY, origin.modelBimZ)
  const basisX = new Vector3(axisX.modelBimX, axisX.modelBimY, axisX.modelBimZ).sub(originVector)
  const basisY = new Vector3(axisY.modelBimX, axisY.modelBimY, axisY.modelBimZ).sub(originVector)
  const basisZ = new Vector3().crossVectors(basisX, basisY)
  const restored = new Matrix4().makeBasis(basisX, basisY, basisZ)
  restored.setPosition(originVector)

  const alignmentMatrix = alignmentMatrixFromResult({
    modelMatrix: restored.toArray(),
  } as any)

  // 2) 恢复：反推 BIM 的世界矩阵。
  const desiredWorld = desiredBimWorldMatrix({
    pointcloudRawWorld: rawWorldMatrixForAlignment(pointcloudWorld),
    alignmentMatrix,
    bimNormalizationCenter: normalizationCenter,
  })

  // 闭环：应当回到 pivot 实际的世界矩阵（含居中偏移）。
  near(desiredWorld, bimPivot.matrixWorld, 1e-8)
})

test('rawWorldMatrixForAlignment 只对带 child 归一化标记的对象还原偏移', () => {
  const plain = new Object3D()
  plain.matrix.copy(new Matrix4().makeTranslation(3, 4, 5))
  plain.matrixAutoUpdate = false
  plain.updateMatrixWorld(true)
  near(rawWorldMatrixForAlignment(plain), plain.matrixWorld)

  const centered = new Object3D()
  centered.matrix.copy(new Matrix4().makeTranslation(3, 4, 5))
  centered.matrixAutoUpdate = false
  centered.updateMatrixWorld(true)
  recordNormalizationOffset(centered, new Vector3(1, 1, 1), 'child')
  near(
    rawWorldMatrixForAlignment(centered),
    new Matrix4().makeTranslation(2, 3, 4),
  )

  // self 模式不参与还原。
  const selfMode = new Object3D()
  selfMode.matrix.copy(new Matrix4().makeTranslation(3, 4, 5))
  selfMode.matrixAutoUpdate = false
  selfMode.updateMatrixWorld(true)
  recordNormalizationOffset(selfMode, new Vector3(1, 1, 1), 'self')
  near(rawWorldMatrixForAlignment(selfMode), selfMode.matrixWorld)
})

test('toLocalMatrix 在父级矩阵下往返一致', () => {
  const parentWorld = new Matrix4().compose(
    new Vector3(1, 2, 3),
    new Quaternion().setFromEuler(new Euler(0.4, 0, -0.2)),
    new Vector3(1, 1, 1),
  )
  const world = new Matrix4().compose(
    new Vector3(-5, 0.25, 7),
    new Quaternion().setFromEuler(new Euler(0, 0.6, 0)),
    new Vector3(1, 1, 1),
  )
  const local = toLocalMatrix(world, parentWorld)
  near(new Matrix4().multiplyMatrices(parentWorld, local), world, 1e-9)
})
