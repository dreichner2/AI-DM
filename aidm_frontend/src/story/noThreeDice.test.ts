/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const FRONTEND = resolve(__dirname, '../..')
const REMOVED = ['three', '@types/three', 'cannon-es']

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name)
    return statSync(path).isDirectory() ? sourceFiles(path) : /\.(ts|tsx)$/.test(name) ? [path] : []
  })
}

describe('rolls are cards, never a 3D die', () => {
  it('depends on neither three.js nor cannon-es', () => {
    const manifest = JSON.parse(readFileSync(join(FRONTEND, 'package.json'), 'utf8')) as Record<string, Record<string, string> | undefined>
    const declared = Object.keys({ ...manifest.dependencies, ...manifest.devDependencies })
    expect(declared.filter((name) => REMOVED.includes(name))).toEqual([])
  })

  it('imports neither anywhere in the app', () => {
    const importer = /(?:from\s+|import\s*\(\s*|require\s*\(\s*)['"](three|cannon-es)(?:\/[^'"]*)?['"]/
    const offenders = sourceFiles(join(FRONTEND, 'src')).filter((path) => importer.test(readFileSync(path, 'utf8')))
    expect(offenders).toEqual([])
  })
})
