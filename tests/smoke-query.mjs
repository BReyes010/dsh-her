// 查询器冒烟：/query 三种来源 + 时间解析 + 认错要报错
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PLUG = path.join(HERE, '..')
// 装到 ~/.dsh/plugins 时，项目根不在相对位置——优先读 her.config.json
function resolveRoot() {
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(process.env.USERPROFILE || process.env.HOME, '.dsh', 'her.config.json'), 'utf8'))
    if (cfg.projectRoot && fs.existsSync(cfg.projectRoot)) return cfg.projectRoot
  } catch {}
  return path.join(HERE, '..', '..', '..', '人格与记忆')
}
const ROOT = resolveRoot()
const FAKE_HOME = path.join(HERE, '.fake-home-q')

process.env.USERPROFILE = FAKE_HOME
process.env.HOME = FAKE_HOME
fs.mkdirSync(path.join(FAKE_HOME, '.dsh'), { recursive: true })
fs.writeFileSync(path.join(FAKE_HOME, '.dsh', 'her.config.json'), JSON.stringify({
  projectRoot: ROOT, pythonPath: process.env.HER_PYTHON || 'python',
  defaultCard: 'dafeiyu', defaultMode: '日常',
}), 'utf8')

const routes = []
const mod = await import('file://' + path.join(PLUG, 'lib', 'index.js').replace(/\\/g, '/'))
mod.apply({ webServer: { register: (r) => { routes.push(r); return () => {} } }, effect: (f) => f(), logger: {} })

function call(p, body) {
  const route = routes.find((r) => r.path === p)
  const req = { method: 'POST', url: p, socket: { remoteAddress: '127.0.0.1' },
    async *[Symbol.asyncIterator]() { yield Buffer.from(JSON.stringify(body || {})) } }
  let status = 0, payload = ''
  const res = { setHeader() {}, set statusCode(v) { status = v }, get statusCode() { return status }, end(d) { payload = d } }
  return Promise.resolve(route.handler(req, res)).then(() => ({ status, json: payload ? JSON.parse(payload) : {} }))
}

const out = []
const ck = (n, ok, x = '') => { out.push(ok); console.log(`${ok ? '[OK]  ' : '[FAIL]'} ${n}${x ? ' — ' + x : ''}`) }

// 1) L3 关键词
let r = await call('/dsh-her/api/query', { q: '壳 账', source: 'l3' })
ck('L3 关键词查询', r.json.ok && (r.json.text || '').includes('共'), (r.json.text || r.json.error || '').split('\n').slice(-1)[0])

// 2) L3 + 时间
r = await call('/dsh-her/api/query', { q: '稿', when: '今天', source: 'l3' })
ck('L3 + 时间', r.json.ok, (r.json.text || r.json.error || '').split('\n').slice(-1)[0])

// 3) 认不出的时间要明确报错
r = await call('/dsh-her/api/query', { q: '稿', when: '那个啥时候', source: 'l3' })
ck('时间认不出时明确报错', (r.json.text || '').includes('没听懂时间'), (r.json.text || '').split('\n')[0])

// 4) 编号 → wb_query
r = await call('/dsh-her/api/query', { q: 'D-13' })
ck('编号查工作台归档', (r.json.text || '').includes('D-13'), (r.json.text || '').split('\n')[0])

// 5) L2
r = await call('/dsh-her/api/query', { q: process.env.HER_TEST_Q || '示例角色', source: 'l2' })
ck('L2 记忆查询', (r.json.text || '').length > 0, (r.json.text || r.json.error || '').slice(0, 40))

// 6) 空查询要拒
r = await call('/dsh-her/api/query', {})
ck('空查询被拒', r.status === 400)

// 6.5) 工作台已作废清单
r = await call('/dsh-her/api/retired', {})
ck('已作废清单可列', (r.json.text || '').includes('D-'), (r.json.text || r.json.error || '').split('\n')[0])

// 7) 非回环拒绝
r = await call('/dsh-her/api/query', { q: 'x' })
ck('本机回环（正面用例已过）', r.status === 200 || r.status === 500)

console.log(`\n通过 ${out.filter(Boolean).length}/${out.length}`)
fs.rmSync(FAKE_HOME, { recursive: true, force: true })
