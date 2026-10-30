// 运行：node tests/smoke.mjs（需要能 spawn python；沙箱里要先放开管道 stdio）
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUG = path.join(HERE, '..')
function resolveRoot() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(process.env.USERPROFILE || process.env.HOME, '.dsh', 'her.config.json'), 'utf8'))
    if (cfg.projectRoot && fs.existsSync(cfg.projectRoot)) return cfg.projectRoot
  } catch {}
  return path.join(HERE, '..', '..', '..', '人格与记忆')
}
const ROOT = resolveRoot()
const FAKE_HOME = path.join(HERE, '.fake-home')

function dropSmoke() {
  for (const p of [path.join(ROOT, 'cards', 'smoke-card'), path.join(ROOT, 'memory', 'smoke-card')]) {
    fs.rmSync(p, { recursive: true, force: true })
  }
  for (const [f, key] of [[path.join(ROOT, 'cards', '_registry.json'), 'cards'],
                          [path.join(ROOT, 'memory', '_registry.json'), 'kits']]) {
    const j = JSON.parse(fs.readFileSync(f, 'utf8').replace(/^\uFEFF/, ''))
    j[key] = j[key].filter((x) => x.id !== 'smoke-card')
    fs.writeFileSync(f, JSON.stringify(j, null, 2), 'utf8')
  }
  const trips = path.join(ROOT, '出差包')
  if (fs.existsSync(trips)) {
    for (const f of fs.readdirSync(trips)) {
      const p = path.join(trips, f)
      if (fs.readFileSync(p, 'utf8').includes('冒烟测试任务')) fs.rmSync(p, { force: true })
    }
  }
}
dropSmoke()

process.env.USERPROFILE = FAKE_HOME
process.env.HOME = FAKE_HOME
fs.mkdirSync(path.join(FAKE_HOME, '.dsh'), { recursive: true })
fs.writeFileSync(path.join(FAKE_HOME, '.dsh', 'her.config.json'), JSON.stringify({
  projectRoot: ROOT,
  pythonPath: process.env.HER_PYTHON || 'python',
  defaultCard: 'dafeiyu',
  defaultMode: '日常',
}, null, 2), 'utf8')

const routes = []
const ctx = {
  webServer: { register: (r) => { routes.push(r); return () => {} } },
  effect: (fn) => fn(),
  logger: { info: () => {}, warn: () => {}, error: () => {} },
}

const mod = await import('file://' + path.join(PLUG, 'lib', 'index.js').replace(/\\/g, '/'))
mod.apply(ctx)
console.log('注册路由：', routes.map((r) => r.path).join(' '))

function call(pathname, { method = 'GET', body = null, ip = '127.0.0.1' } = {}) {
  const pathOnly = pathname.split('?')[0]
  const route = routes.find((r) => r.path === pathOnly)
  if (!route) return Promise.resolve({ status: 0, json: { ok: false, error: 'no route ' + pathOnly } })
  const req = {
    method, url: pathname, socket: { remoteAddress: ip },
    async *[Symbol.asyncIterator]() { if (body) yield Buffer.from(JSON.stringify(body), 'utf8') },
  }
  let status = 0
  let payload = ''
  const res = {
    setHeader() {},
    set statusCode(v) { status = v },
    get statusCode() { return status },
    end(d) { payload = d },
  }
  return Promise.resolve(route.handler(req, res)).then(() => ({
    status, json: payload ? JSON.parse(payload) : {},
  }))
}

const results = []
function check(name, cond, extra = '') {
  results.push([name, !!cond])
  console.log(`${cond ? '[OK]  ' : '[FAIL]'} ${name}${extra ? ' — ' + extra : ''}`)
}

let r = await call('/dsh-her/api/state')
check('GET /state', r.status === 200 && r.json.ok && r.json.cards.length >= 2,
  `cards=${r.json.cards?.length} modes=${r.json.modes?.join('/')}`)

r = await call('/dsh-her/api/pack?card=dafeiyu&mode=' + encodeURIComponent('工作'))
check('GET /pack 现役·工作', r.json.ok && r.json.han > 3000 && (r.json.text || '').includes('口播投影'),
  r.json.error ? 'ERR: ' + r.json.error : `${r.json.han} 汉字 desk=${r.json.desk}`)

r = await call('/dsh-her/api/pack?card=dafeiyu-template&mode=' + encodeURIComponent('日常'))
const cardName = process.env.HER_TEST_NAME || '示例角色'
const pu = (r.json.text || '').split(cardName).length - 1
check('GET /pack 模板·日常（无私名）', r.json.ok && pu === 0,
  r.json.error ? 'ERR: ' + r.json.error : `${r.json.han} 汉字 ${cardName}=${pu}`)

r = await call('/dsh-her/api/pack?card=dafeiyu&mode=' + encodeURIComponent('完整'))
check('GET /pack 完整带工作台', r.json.ok && (r.json.text || '').includes('## 工作台'),
  r.json.error ? 'ERR: ' + r.json.error : `${r.json.han} 汉字`)

r = await call('/dsh-her/api/newcard', { method: 'POST', body: { id: 'smoke-card', name: '冒烟角色', from: 'dafeiyu-template' } })
check('POST /newcard', r.status === 200 && r.json.ok, (r.json.out || r.json.error || '').split('\n')[0])

r = await call('/dsh-her/api/newcard', { method: 'POST', body: { id: 'x', name: 'y' }, ip: '10.1.2.3' })
check('POST /newcard 非回环拒绝', r.status === 403, `status=${r.status}`)

r = await call('/dsh-her/api/outbound', { method: 'POST', body: { to: '冒烟目的地', task: '冒烟测试任务', mode: '工作' } })
check('POST /outbound', r.status === 200 && r.json.ok, (r.json.out || r.json.error || '').split('\n')[0])

r = await call('/dsh-her/api/config', { method: 'POST', body: { defaultMode: '完整' } })
check('POST /config', r.json.ok && r.json.config.defaultMode === '完整', String(r.json.config?.defaultMode))

const slotRegs = []
globalThis.window = { __ModuleLoader__: { load: (m) => { globalThis.__her = m } } }
const ReactStub = {
  createElement: () => ({}), useState: (v) => [v, () => {}], useEffect: () => {},
  useCallback: (f) => f, useRef: () => ({ current: null }), Fragment: 'frag',
}
await import('file://' + path.join(PLUG, 'lib', 'client.js').replace(/\\/g, '/'))
const clientMod = globalThis.__her.factory((n) => (n === 'react' ? ReactStub : {}))
clientMod.apply({
  slots: {
    inject: (name, fn) => { fn(); return () => {} },
    register: (opts) => { slotRegs.push(opts.name); return () => {} },
  },
})
check('client 注册两个槽', slotRegs.includes('settings.section') && slotRegs.includes('conversation.composer.dock'),
  slotRegs.join(' + '))

console.log(`\n通过 ${results.filter((x) => x[1]).length}/${results.length}`)

dropSmoke()
fs.rmSync(FAKE_HOME, { recursive: true, force: true })
console.log('已清理：smoke-card / 冒烟出差包 / 临时 home')
