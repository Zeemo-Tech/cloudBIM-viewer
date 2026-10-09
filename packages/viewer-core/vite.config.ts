import { createLibConfig, peerExternals } from '../../scripts/lib-vite.config.mjs'

export default createLibConfig(import.meta.url, {
  external: [...peerExternals, 'axios'],
})
