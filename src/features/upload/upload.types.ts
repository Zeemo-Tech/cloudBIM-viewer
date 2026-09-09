export type UploadKind = 'bim' | 'cad' | 'pointcloud'

export interface UploadFileConfig {
  kind: UploadKind
  title: string
  accept: string
  extensions: string[]
  placeholder: string
}
