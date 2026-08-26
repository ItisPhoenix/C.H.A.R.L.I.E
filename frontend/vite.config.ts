import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, relative } from 'node:path'
import { defineConfig, type Plugin } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const BUILD_MARKER = '__CHARLIE_BUILD_IDENTITY__'

function buildOutputDir(): string {
  const configured = process.env.CHARLIE_FRONTEND_OUT_DIR
  if (configured) return configured

  const canonical = resolve(process.cwd(), 'dist')
  try {
    readdirSync(canonical, { withFileTypes: true })
    return 'dist'
  } catch {
    return join(tmpdir(), `charlie-runtime-build-direct-${process.pid}-${Date.now()}`)
  }
}

const configuredBuildOutputDir = buildOutputDir()

function gitIdentity(root: string): { git_sha: string | null; dirty: boolean | null } {
  try {
    const git_sha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim()
    const status = execFileSync('git', ['status', '--porcelain', '--untracked-files=no'], { cwd: root, encoding: 'utf8' })
    return { git_sha, dirty: status.trim().length > 0 }
  } catch {
    return { git_sha: null, dirty: null }
  }
}

function bundleFingerprint(distDir: string): string {
  const digest = createHash('sha256')
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) visit(path)
      else if (entry.name !== 'charlie-build.json') {
        digest.update(relative(distDir, path).replaceAll('\\', '/'))
        digest.update(readFileSync(path))
      }
    }
  }
  visit(distDir)
  return digest.digest('hex')
}

function inputFingerprint(frontendRoot: string): string {
  const digest = createHash('sha256')
  const repoRoot = resolve(frontendRoot, '..')
  const files: string[] = []
  const collect = (directory: string) => {
    if (!existsSync(directory)) return
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) collect(path)
      else files.push(path)
    }
  }
  collect(resolve(frontendRoot, 'src'))
  collect(resolve(frontendRoot, 'public'))
  for (const name of ['index.html', 'package.json', 'package-lock.json']) {
    const path = resolve(frontendRoot, name)
    if (existsSync(path)) files.push(path)
  }
  for (const pattern of ['vite.config.ts', 'vite.config.js', 'vite.config.mjs', 'tsconfig.json', 'tsconfig.app.json', 'tsconfig.node.json']) {
    const path = resolve(frontendRoot, pattern)
    if (existsSync(path)) files.push(path)
  }
  for (const name of ['event_contract.json', 'presentation_contract.json']) {
    const path = resolve(repoRoot, 'shared', name)
    if (existsSync(path)) files.push(path)
  }
  for (const path of files.sort()) {
    digest.update(relative(repoRoot, path).replaceAll('\\', '/'))
    digest.update('\0')
    digest.update(readFileSync(path))
    digest.update('\0')
  }
  return digest.digest('hex')
}

function buildIdentityPlugin(): Plugin {
  let root = process.cwd()
  let outDir = resolve(root, configuredBuildOutputDir)
  return {
    name: 'charlie-build-identity',
    configResolved(config) {
      root = config.root
      outDir = resolve(config.root, config.build.outDir)
    },
    closeBundle() {
      const identity = gitIdentity(resolve(root, '..'))
      const build_id = bundleFingerprint(outDir)
      const manifest = {
        build_id,
        input_fingerprint: inputFingerprint(root),
        git_sha: identity.git_sha,
        dirty: identity.dirty,
        built_at: new Date().toISOString(),
      }
      const indexPath = resolve(outDir, 'index.html')
      const html = readFileSync(indexPath, 'utf8')
        .replaceAll(BUILD_MARKER, JSON.stringify(manifest))
        .replaceAll('__CHARLIE_BUILD_ID__', build_id)
      writeFileSync(indexPath, html, 'utf8')
      writeFileSync(resolve(outDir, 'charlie-build.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8')
    },
  }
}

// base '/' keeps asset URLs absolute -- QWebEngineView loads this from /surface/<id>, not the root.
export default defineConfig({
  plugins: [react(), tailwindcss(), buildIdentityPlugin()],
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
  build: {
    outDir: configuredBuildOutputDir,
    chunkSizeWarningLimit: 1800,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (id.includes('node_modules/react') || id.includes('node_modules/zustand')) {
            return 'vendor';
          }
          if (id.includes('node_modules/@xterm')) {
            return 'xterm';
          }
          if (id.includes('node_modules/@phosphor-icons')) {
            return 'icons';
          }
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test-setup.ts'],
  },
})
