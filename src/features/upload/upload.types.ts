export type UploadKind = 'bim' | 'pointcloud'

export interface UploadFileConfig {
  kind: UploadKind
  title: string
  subtitle: string
  description: string
  accept: string
  extensions: string[]
  placeholder: string
}
