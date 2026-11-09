import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from '@playwright/test'

// The real-account specs must use the account that setup_local.cmd actually
// bootstrapped. That password lives in the git-ignored root .env, so read it
// here instead of hard-coding a value that drifts from local_setup.py.
// Playwright loads this file as CommonJS, so __dirname is available.
const configDir = typeof __dirname === 'undefined' ? process.cwd() : __dirname
const rootDir = resolve(configDir, '..')
const envPath = resolve(rootDir, '.env')

function readLocalEnv(path) {
  if (!existsSync(path)) return {}
  const values = {}
  for (const line of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$/)
    if (match) values[match[1]] = match[2].trim()
  }
  return values
}

const localEnv = readLocalEnv(envPath)
process.env.TEST_USERNAME ||= localEnv.LOCAL_ADMIN_USERNAME || 'admin'
process.env.TEST_PASSWORD ||= localEnv.LOCAL_ADMIN_PASSWORD || ''

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
      ? { launchOptions: { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } }
      : {}),
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 120000,
  },
})
