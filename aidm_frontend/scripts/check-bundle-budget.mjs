import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative } from 'node:path'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const projectRoot = fileURLToPath(new URL('..', import.meta.url))
const distDir = join(projectRoot, 'dist')
const assetsDir = join(distDir, 'assets')
const mebibyte = 1024 * 1024

// Keep first-load budgets strict while allowing bounded lazy chunks for dice,
// character creation catalogs, and modal-only management tools. Distribution
// budgets intentionally accommodate the current long-form soundtrack while
// preventing additional unbounded media or image growth.
const budgets = {
  jsRaw: 620 * 1024,
  jsGzip: 185 * 1024,
  // The paired initial/async ceilings total 410 KiB. Keep that aggregate
  // allowance fixed while reserving more of it for intentionally lazy code.
  initialJsGzip: 170 * 1024,
  asyncJsGzip: 240 * 1024,
  cssGzip: 48 * 1024,
  initialAssetGzip: 220 * 1024,
  // HEAD was already at 419.9 KiB. Allow the bounded at-a-glance, recovery,
  // and combat UX while retaining the existing initial and async ceilings.
  totalAssetGzip: 424 * 1024,
  distRaw: 190 * mebibyte,
  staticRaw: 188 * mebibyte,
  largestStaticRaw: 175 * mebibyte,
  imageRaw: 15 * mebibyte,
  mediaRaw: 175 * mebibyte,
}

const codeExtensions = new Set(['.css', '.js', '.map'])
const imageExtensions = new Set(['.avif', '.gif', '.jpeg', '.jpg', '.png', '.svg', '.webp'])
const mediaExtensions = new Set(['.aac', '.flac', '.m4a', '.mp3', '.mp4', '.ogg', '.wav', '.webm'])
const toPosixPath = (path) => path.replaceAll('\\', '/')
const formatBytes = (bytes) =>
  bytes >= mebibyte
    ? `${(bytes / mebibyte).toFixed(2)} MiB`
    : `${(bytes / 1024).toFixed(1)} KiB`

const collectFiles = (directory) =>
  readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? collectFiles(path) : [path]
  })

if (!existsSync(assetsDir)) {
  console.error('Bundle budget check needs a built dist. Run `npm run build` first.')
  process.exit(1)
}

const distFiles = collectFiles(distDir).map((path) => ({
  extension: extname(path).toLowerCase(),
  name: toPosixPath(relative(projectRoot, path)),
  path,
  rawBytes: statSync(path).size,
}))

const assets = distFiles
  .filter((file) => file.path.startsWith(`${assetsDir}/`) || file.path.startsWith(`${assetsDir}\\`))
  .map((file) => ({
    ...file,
    fileName: toPosixPath(relative(assetsDir, file.path)),
    gzipBytes: gzipSync(readFileSync(file.path)).length,
    kind: file.extension === '.js' ? 'js' : file.extension === '.css' ? 'css' : 'other',
  }))
  .filter((asset) => asset.kind !== 'other')
  .sort((a, b) => b.gzipBytes - a.gzipBytes)

const failures = []
const distributionReports = []
let totalJsGzip = 0
let initialJsGzip = 0
let asyncJsGzip = 0
let initialAssetGzip = 0
let totalAssetGzip = 0

const checkDistributionBudget = (label, rawBytes, limit) => {
  distributionReports.push({ label, limit, rawBytes })
  if (rawBytes > limit) {
    failures.push(`${label} is ${formatBytes(rawBytes)} over ${formatBytes(limit)}`)
  }
}

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

const staticFiles = distFiles.filter((file) => !codeExtensions.has(file.extension))
const totalDistRaw = distFiles.reduce((sum, file) => sum + file.rawBytes, 0)
const totalStaticRaw = staticFiles.reduce((sum, file) => sum + file.rawBytes, 0)
const totalImageRaw = distFiles
  .filter((file) => imageExtensions.has(file.extension))
  .reduce((sum, file) => sum + file.rawBytes, 0)
const totalMediaRaw = distFiles
  .filter((file) => mediaExtensions.has(file.extension))
  .reduce((sum, file) => sum + file.rawBytes, 0)
const largestStaticFile = staticFiles.toSorted((a, b) => b.rawBytes - a.rawBytes)[0]

checkDistributionBudget('total dist payload', totalDistRaw, budgets.distRaw)
checkDistributionBudget('non-code static payload', totalStaticRaw, budgets.staticRaw)
checkDistributionBudget(
  'largest static file',
  largestStaticFile?.rawBytes ?? 0,
  budgets.largestStaticRaw,
)
checkDistributionBudget('image payload', totalImageRaw, budgets.imageRaw)
checkDistributionBudget('audio/video payload', totalMediaRaw, budgets.mediaRaw)

console.log('Bundle budget report:')
for (const asset of assets) {
  console.log(`- ${asset.name}: raw ${formatBytes(asset.rawBytes)}, gzip ${formatBytes(asset.gzipBytes)}`)
}
console.log(`- total gzip JS: ${formatBytes(totalJsGzip)}`)
console.log(`- initial gzip JS: ${formatBytes(initialJsGzip)}`)
console.log(`- async gzip JS: ${formatBytes(asyncJsGzip)}`)
console.log(`- initial gzip assets: ${formatBytes(initialAssetGzip)}`)
console.log(`- total gzip assets: ${formatBytes(totalAssetGzip)}`)

console.log('\nDistribution budget report:')
for (const report of distributionReports) {
  const usage = report.limit ? ((report.rawBytes / report.limit) * 100).toFixed(1) : '0.0'
  console.log(
    `- ${report.label}: ${formatBytes(report.rawBytes)} / ${formatBytes(report.limit)} (${usage}%)`,
  )
}
if (largestStaticFile) {
  console.log(`- largest static asset: ${largestStaticFile.name} (${formatBytes(largestStaticFile.rawBytes)})`)
}

if (failures.length) {
  console.error('\nBundle budget failed:')
  for (const failure of failures) {
    console.error(`- ${failure}`)
  }
  process.exit(1)
}
