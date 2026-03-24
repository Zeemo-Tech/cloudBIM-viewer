import type { UploadFileConfig } from './upload.types'

export const BIM_UPLOAD_CONFIG: UploadFileConfig = {
  kind: 'bim',
  title: 'BIM 模型文件',
  accept: '.ifc',
  extensions: ['ifc'],
  placeholder: '拖拽 BIM 模型到这里，或点击选择文件',
}

export const POINT_CLOUD_UPLOAD_CONFIG: UploadFileConfig = {
  kind: 'pointcloud',
  title: '点云文件',
  accept: '.las',
  extensions: ['las'],
  placeholder: '拖拽点云文件到这里，或点击选择文件',
}
