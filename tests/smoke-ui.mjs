// 验证两处改动：①id 自动生成 ②出差包 task 自动取工作台
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
const FAKE_HOME = path.join(HERE, '.fake-home-2')

function cleanup() {
  for (const p of [path.join(ROOT, 'cards'), path.join(ROOT, 'memory')]) {
    for (const d of fs.readdirSync(p)) {
      if (d.startsWith('测试') || /^card-|^smoke-/.test(d)) fs.rmSync(path.join(p, d), { recursive: true, force: true })
    }
    const f = path.join(p, '_registry.json')
    const j = JSON.parse(fs.readFileSync(f, 'utf8').replace(/^\uFEFF/, ''))
    const key = j.cards ? 'cards' : 'kits'
    j[key] = j[key].filter((x) => !/^card-|^smoke-|^测试/.test(x.id))
    fs.writeFileSync(f, JSON.stringify(j, null, 2), 'utf8')
  }
  const trips = path.join(ROOT, '出差包')
  if (fs.existsSync(trips)) for (const f of fs.readdirSync(trips)) fs.rmSync(path.join(trips, f), { force: true })
}
cleanup()

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

function call(p, { method = 'GET', body = null, ip = '127.0.0.1' } = {}) {
  const route = routes.find((r) => r.path === p.split('?')[0])
  const req = { method, url: p, socket: { remoteAddress: ip },
    async *[Symbol.asyncIterator]() { if (body) yield Buffer.from(JSON.stringify(body)) } }
  let status = 0, payload = ''
  const res = { setHeader() {}, set statusCode(v) { status = v }, get statusCode() { return status }, end(d) { payload = d } }
  return Promise.resolve(route.handler(req, res)).then(() => ({ status, json: payload ? JSON.parse(payload) : {} }))
}

const out = []
const ck = (n, ok, x = '') => { out.push(ok); console.log(`${ok ? '[OK]  ' : '[FAIL]'} ${n}${x ? ' — ' + x : ''}`) }

// ① 只给名字，id 自动
let r = await call('/dsh-her/api/newcard', { method: 'POST', body: { name: '测试阿布', from: 'dafeiyu-template' } })
ck('只给名字能建卡', r.status === 200 && r.json.ok, 'id=' + r.json.id)
const madeId = r.json.id
ck('生成的 id 合法且存在', /^[a-zA-Z0-9_-]{2,40}$/.test(madeId || '') && fs.existsSync(path.join(ROOT, 'cards', madeId)), madeId)

// 同名字再建一次 → id 自动加后缀，不撞
r = await call('/dsh-her/api/newcard', { method: 'POST', body: { name: '测试阿布', from: 'dafeiyu-template' } })
ck('同名再建不撞 id', r.json.ok && r.json.id !== madeId, `${madeId} → ${r.json.id}`)

// 空名字要拒
r = await call('/dsh-her/api/newcard', { method: 'POST', body: {} })
ck('空名字被拒', r.status === 400)

// ② 出差包：不给 task 也能生成
r = await call('/dsh-her/api/outbound', { method: 'POST', body: { mode: '工作' } })
ck('不给 task 能生成出差包', r.status === 200 && r.json.ok, (r.json.out || r.json.error || '').split('\n')[0])

const trips = path.join(ROOT, '出差包')
const files = fs.existsSync(trips) ? fs.readdirSync(trips) : []
ck('出差包落到目录', files.length === 1, files.join(','))
if (files.length) {
  const t = fs.readFileSync(path.join(trips, files[0]), 'utf8')
  ck('含回程模板', t.includes('# 回程'))
  const desk = fs.readFileSync(path.join(ROOT, '入口', '工作台.md'), 'utf8')
  const nowLine = (desk.match(/##\s*现在\s*\n+([^\n]+)/) || [])[1] || ''
  ck('任务自动取自工作台「现在」', nowLine ? t.includes(nowLine.slice(0, 12)) : true,
    nowLine ? nowLine.slice(0, 30) : '（工作台无「现在」）')
}

console.log(`\n通过 ${out.filter(Boolean).length}/${out.length}`)
cleanup()
fs.rmSync(FAKE_HOME, { recursive: true, force: true })
console.log('已清理测试卡与出差包')
