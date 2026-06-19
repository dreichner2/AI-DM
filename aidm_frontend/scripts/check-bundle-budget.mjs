import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const assetsDir = join(projectRoot, 'dist', 'assets')

// Keep first-load budgets strict while allowing bounded lazy chunks for dice and
// character creation catalogs that are loaded only when those tools open.
const budgets = {
  jsRaw: 620 * 1024,
  jsGzip: 185 * 1024,
  initialJsGzip: 190 * 1024,
  asyncJsGzip: 220 * 1024,
  cssGzip: 48 * 1024,
  initialAssetGzip: 220 * 1024,
  totalAssetGzip: 420 * 1024,
}

const formatBytes = (bytes) => `${(bytes / 1024).toFixed(1)} KiB`

if (!existsSync(assetsDir)) {
  console.error('Bundle budget check needs a built dist. Run `npm run build` first.')
  process.exit(1)
}

const assets = readdirSync(assetsDir)
  .map((name) => {
    const path = join(assetsDir, name)
    const rawBytes = statSync(path).size
    const gzipBytes = gzipSync(readFileSync(path)).length
    return {
      fileName: name,
      name: relative(projectRoot, path),
      rawBytes,
      gzipBytes,
      kind: name.endsWith('.js') ? 'js' : name.endsWith('.css') ? 'css' : 'other',
    }
  })
  .filter((asset) => asset.kind !== 'other')
  .sort((a, b) => b.gzipBytes - a.gzipBytes)

const failures = []
let totalJsGzip = 0
let initialJsGzip = 0
let asyncJsGzip = 0
let initialAssetGzip = 0
let totalAssetGzip = 0

for (const asset of assets) {
  const isInitialAsset = asset.fileName.startsWith('index-')
  totalAssetGzip += asset.gzipBytes
  if (isInitialAsset) {
    initialAssetGzip += asset.gzipBytes
  }
  if (asset.kind === 'js') {
    totalJsGzip += asset.gzipBytes
    if (isInitialAsset) {
      initialJsGzip += asset.gzipBytes
    } else {
      asyncJsGzip += asset.gzipBytes
    }
    if (asset.rawBytes > budgets.jsRaw) {
      failures.push(`${asset.name} raw JS is ${formatBytes(asset.rawBytes)} over ${formatBytes(budgets.jsRaw)}`)
    }
    if (asset.gzipBytes > budgets.jsGzip) {
      failures.push(`${asset.name} gzip JS is ${formatBytes(asset.gzipBytes)} over ${formatBytes(budgets.jsGzip)}`)
    }
  }
  if (asset.kind === 'css' && asset.gzipBytes > budgets.cssGzip) {
    failures.push(`${asset.name} gzip CSS is ${formatBytes(asset.gzipBytes)} over ${formatBytes(budgets.cssGzip)}`)
  }
}

if (initialJsGzip > budgets.initialJsGzip) {
  failures.push(`initial gzip JS is ${formatBytes(initialJsGzip)} over ${formatBytes(budgets.initialJsGzip)}`)
}

if (asyncJsGzip > budgets.asyncJsGzip) {
  failures.push(`async gzip JS is ${formatBytes(asyncJsGzip)} over ${formatBytes(budgets.asyncJsGzip)}`)
}

if (initialAssetGzip > budgets.initialAssetGzip) {
  failures.push(`initial gzip assets are ${formatBytes(initialAssetGzip)} over ${formatBytes(budgets.initialAssetGzip)}`)
}

if (totalAssetGzip > budgets.totalAssetGzip) {
  failures.push(`total gzip assets are ${formatBytes(totalAssetGzip)} over ${formatBytes(budgets.totalAssetGzip)}`)
}

console.log('Bundle budget report:')
for (const asset of assets) {
  console.log(`- ${asset.name}: raw ${formatBytes(asset.rawBytes)}, gzip ${formatBytes(asset.gzipBytes)}`)
}
console.log(`- total gzip JS: ${formatBytes(totalJsGzip)}`)
console.log(`- initial gzip JS: ${formatBytes(initialJsGzip)}`)
console.log(`- async gzip JS: ${formatBytes(asyncJsGzip)}`)
console.log(`- initial gzip assets: ${formatBytes(initialAssetGzip)}`)
console.log(`- total gzip assets: ${formatBytes(totalAssetGzip)}`)

if (failures.length) {
  console.error('\nBundle budget failed:')
  for (const failure of failures) {
    console.error(`- ${failure}`)
  }
  process.exit(1)
}
