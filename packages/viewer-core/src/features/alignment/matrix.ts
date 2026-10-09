import { Matrix4, Quaternion, Vector3, type Object3D } from 'three'
import type { BimAlignmentResult, ModelPair } from '@/api/backend-alignment'

/**
 * 配准矩阵换算：BIM 模型与扫描点云之间的刚体变换。
 *
 * 后端只保存「扫描坐标系 → BIM 坐标系」的刚体变换，用三个基准点对表达
 * （`/alignments/bim` 的 `modelPairs`）。前端负责两个方向的换算：
 *
 * - **保存**：由场景里 BIM / 点云各自的世界矩阵算出刚体变换，再展开成点对；
 * - **恢复**：由后端返回的矩阵反推 BIM 应该摆在哪里。
 *
 * 两个方向必须严格互逆，否则配准会静默产生整体偏移。这里把数学集中成纯函数
 * （不触碰场景对象），由 `matrix.test.ts` 用往返一致性保证这一点。
 */

/** 居中 pivot 在 `userData` 上记录的归一化偏移键名。 */
export const VIEWER_NORMALIZATION_CENTER = '__viewerNormalizationCenter'
export const VIEWER_NORMALIZATION_MODE = '__viewerNormalizationMode'

/** 表达刚体变换所用的三个基准点（扫描坐标系）：原点 + X 轴单位点 + Y 轴单位点。 */
export const CALIBRATION_SAMPLE_POINTS: ReadonlyArray<readonly [number, number, number]> = [
  [0, 0, 0],
  [1, 0, 0],
  [0, 1, 0],
]

/**
 * 记录归一化偏移。查看器为了让 BIM 绕自身中心旋转，会把几何整体平移 `-center`
 * 并放大到 pivot 上；换算回配准坐标系时要把这段偏移加回去。
 */
export function recordNormalizationOffset(
  target: Object3D | null,
  center: Vector3 | null,
  mode: 'self' | 'child',
): void {
  if (!target || !center) return
  target.userData = target.userData ?? {}
  target.userData[VIEWER_NORMALIZATION_CENTER] = center.clone()
  target.userData.__viewerNormalizationTranslation = center.clone().multiplyScalar(-1)
  target.userData[VIEWER_NORMALIZATION_MODE] = mode
}

/**
 * 供配准换算使用的「原始」世界矩阵：把居中 pivot 引入的平移还原掉，
 * 使不同归一化实现（居中 pivot / 直接居中 root）得到同一套坐标语义。
 */
export function rawWorldMatrixForAlignment(obj: Object3D | null): Matrix4 {
  const matrix = new Matrix4().copy(obj?.matrixWorld ?? new Matrix4())
  const center = obj?.userData?.[VIEWER_NORMALIZATION_CENTER] as Vector3 | undefined
  const mode = obj?.userData?.[VIEWER_NORMALIZATION_MODE] as 'self' | 'child' | undefined
  if (!center || mode !== 'child') return matrix

  matrix.multiply(
    new Matrix4().makeTranslation(-(center.x ?? 0), -(center.y ?? 0), -(center.z ?? 0)),
  )
  return matrix
}

/** 去掉缩放，只保留刚体部分：配准只描述旋转 + 平移。 */
export function rigidPartOf(matrix: Matrix4): Matrix4 {
  const position = new Vector3()
  const quaternion = new Quaternion()
  matrix.decompose(position, quaternion, new Vector3())
  return new Matrix4().compose(position, quaternion, new Vector3(1, 1, 1))
}

/**
 * BIM 相对点云的刚体变换，即把扫描坐标系的点变换到 BIM 坐标系的矩阵。
 * 与后端 `modelPairs` 的语义一致。
 */
export function scanToBimRigidTransform(
  bimRawWorld: Matrix4,
  pointcloudRawWorld: Matrix4,
): Matrix4 {
  return rigidPartOf(new Matrix4().copy(bimRawWorld).invert().multiply(pointcloudRawWorld))
}

/** 由刚体变换展开成后端入参格式的三个点对。 */
export function modelPairsFromRigidTransform(rigid: Matrix4): ModelPair[] {
  return CALIBRATION_SAMPLE_POINTS.map(([x, y, z]) => {
    const bimPoint = new Vector3(x, y, z).applyMatrix4(rigid)
    return {
      modelScanX: x,
      modelScanY: y,
      modelScanZ: z,
      modelBimX: bimPoint.x,
      modelBimY: bimPoint.y,
      modelBimZ: bimPoint.z,
    }
  })
}

/**
 * 后端结果 → 场景使用的 4x4 刚体矩阵。
 * 优先用 `modelMatrix`，缺失时退回四元数 + 平移字段；缩放一律归一为 1。
 */
export function alignmentMatrixFromResult(alignment: BimAlignmentResult): Matrix4 {
  const rawMatrix = new Matrix4()
  if (Array.isArray(alignment.modelMatrix) && alignment.modelMatrix.length === 16) {
    rawMatrix.fromArray(alignment.modelMatrix)
  } else {
    rawMatrix.compose(
      new Vector3(
        alignment.modelTranslationX,
        alignment.modelTranslationY,
        alignment.modelTranslationZ,
      ),
      new Quaternion(
        alignment.modelRotationQx,
        alignment.modelRotationQy,
        alignment.modelRotationQz,
        alignment.modelRotationQw,
      ),
      new Vector3(1, 1, 1),
    )
  }
  return rigidPartOf(rawMatrix)
}

/**
 * 由配准矩阵反推 BIM 的目标世界矩阵（与 `scanToBimRigidTransform` 互逆）。
 *
 * `bimNormalizationCenter` 是 BIM 几何被居中时的原始中心，恢复时要把 BIM 摆回
 * 「中心落在目标位置」的姿态。
 */
export function desiredBimWorldMatrix(params: {
  pointcloudRawWorld: Matrix4
  alignmentMatrix: Matrix4
  bimNormalizationCenter?: Vector3 | null
}): Matrix4 {
  const { pointcloudRawWorld, alignmentMatrix, bimNormalizationCenter } = params
  const center = bimNormalizationCenter ?? new Vector3(0, 0, 0)
  return new Matrix4()
    .copy(pointcloudRawWorld)
    .multiply(new Matrix4().copy(alignmentMatrix).invert())
    .multiply(new Matrix4().makeTranslation(center.x, center.y, center.z))
}

/** 把世界矩阵转换到父级的局部矩阵。 */
export function toLocalMatrix(worldMatrix: Matrix4, parentWorldMatrix?: Matrix4 | null): Matrix4 {
  if (!parentWorldMatrix) return worldMatrix.clone()
  return new Matrix4().multiplyMatrices(new Matrix4().copy(parentWorldMatrix).invert(), worldMatrix)
}
