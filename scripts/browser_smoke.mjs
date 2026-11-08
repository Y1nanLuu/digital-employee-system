// 可选浏览器冒烟：npm install --prefix work/browser-driver puppeteer-core
// 设置PUPPETEER_MODULE和BROWSER_EXECUTABLE后，在项目根目录运行node scripts/browser_smoke.mjs。
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer } from 'node:net'
import http from 'node:http'
import { mkdtemp, mkdir, open } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)
const puppeteer = require(process.env.PUPPETEER_MODULE || join(root, 'work/browser-driver/node_modules/puppeteer-core'))
const executablePath = process.env.BROWSER_EXECUTABLE
if (!executablePath) throw new Error('请设置BROWSER_EXECUTABLE，指向隔离测试浏览器')
await mkdir(join(root, 'work'), { recursive: true })
const scratch = await mkdtemp(join(root, 'work/browser-smoke-'))
const log = await open(join(root, 'work/browser-smoke-services.log'), 'w')
const children = []
let browser
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
async function port() {
  const server = createServer()
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const number = server.address().port
  await new Promise(resolve => server.close(resolve))
  return number
}
function call(base, path, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const request = http.request(base + path, { method: body ? 'POST' : 'GET', headers: {
      'Content-Type': 'application/json', ...headers,
    } }, response => {
      let text = ''
      response.on('data', chunk => { text += chunk })
      response.on('end', () => {
        try { resolve({ status: response.statusCode, body: JSON.parse(text) }) } catch (error) { reject(error) }
      })
    })
    request.setTimeout(15000, () => request.destroy(new Error('本机请求超时')))
    request.on('error', reject)
    request.end(body ? JSON.stringify(body) : undefined)
  })
}
async function ready(url, child) {
  for (let count = 0; count < 100; count++) {
    if (child.exitCode !== null) throw new Error('本机测试服务提前退出，请查看work/browser-smoke-services.log')
    try { await new Promise((resolve, reject) => http.get(url, response => { response.resume(); resolve() }).on('error', reject)); return } catch {}
    await pause(200)
  }
  throw new Error('本机测试服务启动超时')
}
function launch(command, args, cwd, env) {
  const child = spawn(command, args, { cwd, env, stdio: ['ignore', log.fd, log.fd] })
  children.push(child)
  return child
}
try {
  const financePort = await port(), backendPort = await port(), webPort = await port()
  const backend = 'http://127.0.0.1:' + backendPort, web = 'http://127.0.0.1:' + webPort
  const python = process.env.PROJECT_PYTHON || join(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
  const env = { ...process.env, DATABASE_URL: 'sqlite:///' + join(scratch, 'tasks.db'),
    FINANCE_DATABASE_URL: 'sqlite:///' + join(scratch, 'finance.db'), FINANCE_URL: 'http://127.0.0.1:' + financePort,
    FINANCE_KEY: 'local-finance-key', DEMO_PASSWORD: 'demo123', AI_MODULE_ENABLED: 'false', DEV_API_TARGET: backend }
  await ready(env.FINANCE_URL + '/health', launch(python, ['-m','uvicorn','finance.main:app','--host','127.0.0.1','--port',String(financePort)], join(root,'backend'), env))
  await ready(backend + '/health', launch(python, ['-m','uvicorn','app.main:app','--host','127.0.0.1','--port',String(backendPort)], join(root,'backend'), env))
  await ready(web, launch(process.execPath, [join(root,'frontend/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',String(webPort),'--strictPort'], join(root,'frontend'), env))
  browser = await puppeteer.launch({ executablePath, headless: true, args: ['--no-sandbox','--disable-dev-shm-usage'] })
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  page.setDefaultTimeout(15000)
  let abortNextSubmission = false, delayDetails = false
  await page.setRequestInterception(true)
  page.on('request', async request => {
    try {
      if (abortNextSubmission && request.method() === 'POST' && request.url().endsWith('/api/tasks')) {
        abortNextSubmission = false
        await request.abort(); return
      }
      if (delayDetails && /\/api\/tasks\/[^/]+$/.test(request.url()) && request.method() === 'GET') await pause(300)
      const url = new URL(request.url())
      if (['127.0.0.1','localhost'].includes(url.hostname) || ['data:','about:'].includes(url.protocol)) await request.continue()
      else await request.abort()
    } catch {}
  })
  const api = (path, body, key) => call(backend, path, body, { Authorization: 'Basic ' + Buffer.from('employee:demo123').toString('base64'),
    ...(key ? { 'Idempotency-Key': key } : {}) })
  const clickText = async text => {
    await page.waitForFunction(text => [...document.querySelectorAll('button')].some(button => button.textContent.includes(text) && !button.disabled), {}, text)
    return page.evaluate(text => {
    const element = [...document.querySelectorAll('button')].find(button => button.textContent.includes(text) && !button.disabled)
    if (!element) throw new Error('找不到可用按钮：' + text)
    element.click()
  }, text)
  }
  const labelControl = (text, value) => page.evaluate((text, value) => {
    const label = [...document.querySelectorAll('label')].find(label => label.textContent.includes(text))
    const element = label?.querySelector('input,textarea,select')
    if (!element) throw new Error('找不到字段：' + text)
    if (element.type === 'checkbox') { if (element.checked !== value) element.click() }
    else { element.value = value; element.dispatchEvent(new Event('input', { bubbles:true })); element.dispatchEvent(new Event('change', { bubbles:true })) }
  }, text, value)
  async function login(role) {
    await page.waitForSelector('.login-form')
    await page.select('.login-form select', role)
    await page.click('.login-form button')
    await page.waitForSelector('.hero')
    await page.waitForFunction(() => document.querySelector('.account button')?.disabled === false)
  }
  async function fillClaim(amount, description, category='交通费') {
    await clickText('发起报销')
    await page.waitForSelector('.form-section')
    await labelControl('报销类别', category)
    await labelControl('报销金额', amount)
    await labelControl('报销说明', description)
  }
  await page.goto(web)
  await login('employee')
  await fillClaim('1e3','客户现场往返交通费1000元')
  await labelControl('我已核对原始说明',true)
  await clickText('提交并开始初审')
  await page.waitForSelector('[role=alert]')
  assert.equal((await api('/api/tasks')).body.length, 0)
  await labelControl('报销金额','300')
  await labelControl('报销说明','客户现场交通费500元')
  await labelControl('我已核对原始说明',true)
  await clickText('提交并开始初审')
  await page.waitForFunction(() => document.querySelector('[role=alert]')?.textContent.includes('字段冲突'))
  assert.equal(await page.evaluate(() => localStorage.getItem('employee-pending:employee')),null)
  await labelControl('存在金额或类别差异',true)
  await labelControl('具体确认说明','其中200元属于其他项目，本次仅申报300元')
  await clickText('提交并开始初审')
  await page.waitForSelector('.task-layout')
  assert.equal((await api('/api/tasks')).body.length,1)
  console.log('PASS: browser strict money, conflict confirmation, validation-error correction')

  await fillClaim('1500','客户现场采购办公用品1500元','办公用品')
  await clickText('查看字段提取建议')
  await page.waitForFunction(() => document.querySelector('.parse-result')?.textContent.includes('提取模式'))
  await labelControl('我已核对原始说明',true)
  await clickText('提交并开始初审')
  await page.waitForSelector('.task-layout')
  const waiting = (await api('/api/tasks')).body.find(task => task.status === 'awaiting_approval')
  assert.ok(waiting.parse_snapshot.parse_id)
  await clickText('退出'); await login('approver'); await clickText('任务与审批')
  await page.waitForSelector('.task-layout')
  delayDetails = true
  await clickText(waiting.id)
  await page.waitForFunction(() => [...document.querySelectorAll('.task-card')].every(card => card.disabled))
  await page.waitForSelector('.approval')
  delayDetails = false
  await labelControl('通过前补正报销字段',true)
  await labelControl('金额','900.00')
  await labelControl('补正原因','扣除个人承担部分')
  await labelControl('我已核对当前有效字段',true)
  await labelControl('审批意见','已核对原始说明及金额')
  await clickText('通过并继续')
  await page.waitForFunction(() => !document.querySelector('.approval'))
  const amended = (await api('/api/tasks/' + waiting.id)).body
  assert.equal(amended.status,'succeeded')
  assert.equal(amended.payload.amount,'900.00')
  assert.equal(amended.submission_payload.amount,'1500.00')
  assert.equal(amended.amendments.length,1)
  console.log('PASS: browser parse linkage, detail loading lock, atomic approval amendment and audit')

  await clickText('退出'); await login('employee')
  await fillClaim('200','客户现场交通费200元')
  await labelControl('我已核对原始说明',true)
  abortNextSubmission = true
  await clickText('提交并开始初审')
  await page.waitForFunction(() => document.querySelector('[role=alert]')?.textContent.includes('无法连接'))
  const pending = await page.evaluate(() => JSON.parse(localStorage.getItem('employee-pending:employee')))
  assert.ok(pending.key)
  const accepted = await api('/api/tasks', pending.payload, pending.key)
  assert.equal(accepted.status,201)
  const count = (await api('/api/tasks')).body.length
  await page.reload(); await login('employee'); await clickText('发起报销')
  await clickText('重试上次提交')
  await page.waitForSelector('.task-layout')
  assert.equal((await api('/api/tasks')).body.length,count)
  assert.equal(await page.evaluate(() => localStorage.getItem('employee-pending:employee')),null)
  assert.deepEqual(errors,[])
  await page.screenshot({ path: join(root,'work/browser-smoke.png'), fullPage:true })
  console.log('PASS: browser unknown-result persistence, refresh retry with original key, no duplicate task, no page errors')
} finally {
  if (browser) await browser.close()
  for (const child of children.reverse()) {
    if (child.exitCode === null) { child.kill('SIGTERM'); await Promise.race([new Promise(resolve => child.once('exit',resolve)),pause(3000)]); if (child.exitCode === null) child.kill('SIGKILL') }
  }
  await log.close()
}
