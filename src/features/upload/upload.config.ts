import type { UploadFileConfig } from './upload.types'

export const BIM_UPLOAD_CONFIG: UploadFileConfig = {
  kind: 'bim',
  title: 'BIM 模型文件',
  subtitle: '上传建筑模型文件',
  description: '仅支持 IFC 格式文件。',
  accept: '.ifc',
  extensions: ['ifc'],
  placeholder: '拖拽 BIM 模型到这里，或点击选择文件',
}

export const POINT_CLOUD_UPLOAD_CONFIG: UploadFileConfig = {
  kind: 'pointcloud',
  title: '扫描文件 / 点云文件',
  subtitle: '上传扫描成果文件',
  description: '仅支持 LAS 格式文件。',
  accept: '.las',
  extensions: ['las'],
  placeholder: '拖拽点云文件到这里，或点击选择文件',
}
