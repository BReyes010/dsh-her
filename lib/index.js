/**
 * dsh-her —— 服务端（DSH 插件）
 *
 * 它不自己实现记忆逻辑：所有活儿都交给「人格与记忆」目录里的 Python 脚本。
 * 这里只做三件事：读配置、跑脚本、把结果用 JSON 递给界面。
 *
 * 路由（同源，前端直接 fetch）：
 *   GET  /dsh-her/api/state                 配置 + 卡列表 + 模式 + 体积估算
 *   GET  /dsh-her/api/pack?card=&mode=      返回该卡该模式的注入包
 *   POST /dsh-her/api/newcard               新建角色卡（id / name / from）
 *   POST /dsh-her/api/outbound              生成出差包（to / task / mode / card）
 *
 * 配置：~/.dsh/her.config.json
 *   { "projectRoot": "D:\\...\\人格与记忆", "pythonPath": "…python.exe",
 *     "defaultCard": "dafeiyu", "defaultMode": "日常" }
 *
 * 安全：只接受本机回环访问；写操作（POST）额外校验 loopback。
 */

import fs from 'node:fs'
import path from 'node:path'
import os from 'node:os'
import { spawn } from 'node:child_process'

const HOME = os.homedir()
const CFG = path.join(HOME, '.dsh', 'her.config.json')
const API = '/dsh-her/api'
const MODES = ['出差包', '写稿', '直播']
const scripts = []

function readConfig() {
  const fallback = {
    projectRoot: '',
    pythonPath: 'python',
    defaultCard: 'dafeiyu',
    defaultMode: '日常',
  }
  try {
    const raw = JSON.parse(fs.readFileSync(CFG, 'utf8'))
    return { ...fallback, ...raw }
  } catch {
    return fallback
  }
}

function writeConfig(patch) {
  const next = { ...readConfig(), ...patch }
  fs.mkdirSync(path.dirname(CFG), { recursive: true })
  fs.writeFileSync(CFG, JSON.stringify(next, null, 2), 'utf8')
  return next
}

function root() {
  const cfg = readConfig()
  if (!cfg.projectRoot) throw new Error('还没配置 projectRoot（设置页第一行填「人格与记忆」目录）')
  return cfg.projectRoot
}

function run(scriptArgs, { timeout = 60000, env = {} } = {}) {
  const cfg = readConfig()
  return new Promise((resolve) => {
    const child = spawn(cfg.pythonPath || 'python', scriptArgs, {
      cwd: root(),
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', ...env },
    })
    let out = ''
    let err = ''
    const timer = setTimeout(() => child.kill(), timeout)
    child.stdout.on('data', (d) => { out += d.toString('utf8') })
    child.stderr.on('data', (d) => { err += d.toString('utf8') })
    child.on('close', (code) => {
      clearTimeout(timer)
      resolve({ code, out, err })
    })
    child.on('error', (e) => {
      clearTimeout(timer)
      resolve({ code: -1, out, err: String(e) })
    })
  })
}

function listCards() {
  const regPath = path.join(root(), 'cards', '_registry.json')
  const reg = JSON.parse(fs.readFileSync(regPath, 'utf8').replace(/^\uFEFF/, ''))
  const cards = []
  for (const c of reg.cards || []) {
    const dir = path.join(root(), 'cards', c.dir || c.id)
    let scenes = []
    let hasCardJson = false
    try {
      const cj = JSON.parse(fs.readFileSync(path.join(dir, 'card.json'), 'utf8').replace(/^\uFEFF/, ''))
      scenes = (cj.scenes || []).map((s) => s.id)
      hasCardJson = true
    } catch {
      scenes = ['（现役：人格/ + 工作/）']
    }
    cards.push({
      id: c.id,
      name: c.name || c.id,
      kind: c.kind || '',
      aliases: c.aliases || [],
      memoryId: c.memory_id || '',
      scenes,
      hasCardJson,
    })
  }
  return { defaultCard: reg.default_card || '', cards }
}

function han(t) {
  const m = t.match(/[\u4e00-\u9fff]/g)
  return m ? m.length : 0
}

function isLoopback(req) {
  const a = req.socket?.remoteAddress || ''
  return a === '127.0.0.1' || a === '::1' || a === '::ffff:127.0.0.1'
}

function json(res, code, obj) {
  res.statusCode = code
  res.setHeader('content-type', 'application/json; charset=utf-8')
  res.setHeader('cache-control', 'no-store')
  res.end(JSON.stringify(obj))
}

async function readBody(req) {
  const chunks = []
  for await (const c of req) chunks.push(c)
  const raw = Buffer.concat(chunks).toString('utf8')
  return raw || '{}'
}

export const inject = ['webServer', 'commands']


function _plugin_log(_root, msg) {
  try {
    const f = path.join(os.tmpdir(), 'dsh-her.log')
    fs.appendFileSync(f, new Date().toISOString() + '  ' + msg + '\n')
  } catch (e) {}
}

function registerLoadCommand(ctx, run, root) {
  // 文档：命令名要小写；注册要在命令服务注入之后做（ctx.inject）
  const handler = async (invocation) => {
    try {
      const raw = String(invocation && invocation.rawInput ? invocation.rawInput : '').trim()
      _plugin_log(root, '/load invoked raw=' + JSON.stringify(raw))
      if (!raw) return { kind: 'error', text: '用法：/load <角色名> [卡名]，例如 /load 我的角色 口播' }
      const m = /^(\S+)(?:\s+(.+))?$/.exec(raw)
      const who = m ? m[1] : raw
      const skill = m && m[2] ? m[2].trim() : ''
      const cmd = skill && skill !== '无卡' ? `载入${who} ${skill}` : `载入${who}`
      const r = await run(['脚本/load_cmd.py', cmd], { timeout: 60000 })
      _plugin_log(root, `/load rc=${r.code} len=${(r.out || '').length} err=${(r.err || '').slice(0, 120)}`)
      if (r.code !== 0) return { kind: 'error', text: (r.err || r.out || '载入失败').slice(0, 500) }
      return { kind: 'success', text: r.out }
    } catch (e) {
      _plugin_log(root, '/load threw: ' + String(e && e.stack ? e.stack : e).slice(0, 300))
      return { kind: 'error', text: '载入失败：' + String(e && e.message ? e.message : e) }
    }
  }

  try {
    ctx.inject(['commands'], (cctx) => {
      cctx.commands.register({
        definitionId: '@dsh-her/load',
        name: 'load',
        description: '载入人格与记忆：/load <角色名> [卡名]',
        input: { hint: '<角色名> [卡名]' },
        handler,
      })
      _plugin_log(root, '/load 注册成功')
      try { ctx.logger?.info?.('[dsh-her] /load 命令已注册') } catch (e) {}
    })
  } catch (e) {
    _plugin_log(root, '/load 注册失败：' + String(e && e.message ? e.message : e))
    try { ctx.logger?.warn?.('[dsh-her] /load 注册失败：' + String(e && e.message ? e.message : e)) } catch (e2) {}
  }
}

export function apply(ctx) {
  registerLoadCommand(ctx, run, root)
  const cfg0 = readConfig()
  if (!cfg0.projectRoot) {
    ctx.logger?.info?.('[dsh-her] 未配置 projectRoot：请到设置 → 人格，填「人格与记忆」目录')
  }

  const route = (kind, p, handler) => {
    scripts.push(ctx.webServer.register({
      kind,
      path: p,
      handler: async (req, res) => {
        try {
          await handler(req, res)
        } catch (e) {
          json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
        }
      },
    }))
  }

  // ── 状态：配置 + 卡 + 模式 ──
  route('exact', `${API}/state`, async (req, res) => {
    const cfg = readConfig()
    let cards = []
    let error = ''
    try {
      cards = listCards().cards
    } catch (e) {
      error = String(e && e.message ? e.message : e)
    }
    json(res, 200, { ok: true, config: cfg, configPath: CFG, modes: MODES, cards, skills: skillsList(), error })
  })

  // ── 取包 ──
  // ── 载入指令：载入<角色名>[ <卡名>] ──
  route('exact', `${API}/load`, async (req, res) => {
    try {
      const u = new URL(req.url, 'http://127.0.0.1')
      const cmd = (u.searchParams.get('cmd') || '').trim()
      if (!cmd) return json(res, 400, { ok: false, error: '要带 cmd，例如「载入我的角色 口播」' })
      const r = await run(['脚本/load_cmd.py', cmd], { timeout: 60000 })
      if (r.code !== 0) return json(res, 400, { ok: false, error: (r.err || r.out).slice(0, 400) })
      json(res, 200, { ok: true, cmd, text: r.out, han: han(r.out), chars: r.out.length })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  function skillsList() {
    const dir = path.join(root(), 'skills')
    const out = []
    try {
      for (const f of fs.readdirSync(dir)) {
        if (!f.endsWith('.md') || f.startsWith('_')) continue
        const txt = fs.readFileSync(path.join(dir, f), 'utf8').replace(/\r\n/g, '\n')
        const m = /^---\n([\s\S]*?)\n---/.exec(txt)
        const fm = m ? m[1] : ''
        const kind = (/^kind:\s*(.+)$/m.exec(fm) || [])[1] || ''
        if (kind !== 'skill') continue          // 素材与子卡（task）都不进选择器
        out.push(f.replace(/\.md$/, ''))
      }
    } catch (e) {}
    return out
  }

  // ── 角色包：导出 / 导入 ──
  route('exact', `${API}/charexport`, async (req, res) => {
    try {
      const u = new URL(req.url, 'http://127.0.0.1')
      const id = (u.searchParams.get('id') || '').trim()
      if (!id) return json(res, 400, { ok: false, error: '要带 id' })
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, '')
      const out = path.join(os.tmpdir(), `${id}-${stamp}.zip`)
      try { fs.unlinkSync(out) } catch (e) {}
      const r = await run(['脚本/her_char.py', 'export', id, '--out', out], { timeout: 60000 })
      if (r.code !== 0) return json(res, 500, { ok: false, error: (r.err || r.out).slice(0, 400) })
      const buf = fs.readFileSync(out)
      res.statusCode = 200
      res.setHeader('content-type', 'application/zip')
      res.setHeader('content-disposition', `attachment; filename="${id}.zip"`)
      res.setHeader('content-length', String(buf.length))
      res.end(buf)
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  route('exact', `${API}/charimport`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    try {
      const b = JSON.parse(await readBody(req) || '{}')
      if (!b.b64) return json(res, 400, { ok: false, error: '要带 b64（zip 内容）' })
      const stamp = Date.now()
      const tmp = path.join(os.tmpdir(), `char-import-${stamp}.zip`)
      fs.writeFileSync(tmp, Buffer.from(String(b.b64), 'base64'))
      const args = ['脚本/her_char.py', 'import', tmp]
      if (b.answersPath) args.push('--answers', String(b.answersPath))
      const r = await run(args, { timeout: 90000 })
      const out = (r.out || '').trim()
      let questions = []
      try {
        const qf = path.join(root(), '提案', '_待导入问题.json')
        if (fs.existsSync(qf)) questions = (JSON.parse(fs.readFileSync(qf, 'utf8')).questions) || []
      } catch (e) {}
      json(res, 200, {
        ok: r.code === 0,
        code: r.code,
        out: out.slice(0, 1500),
        error: r.code === 0 ? undefined : (r.err || out).slice(0, 600),
        questions,
        packPath: tmp,
      })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  // ── 技能卡包：导出 / 导入 ──
  route('exact', `${API}/skillexport`, async (req, res) => {
    try {
      const u = new URL(req.url, 'http://127.0.0.1')
      const id = (u.searchParams.get('id') || '').trim()
      if (!id) return json(res, 400, { ok: false, error: '要带 id' })
      const out = path.join(os.tmpdir(), `skill-${id}.zip`)
      try { fs.unlinkSync(out) } catch (e) {}
      const r = await run(['脚本/her_skill.py', 'export', id, '--out', out], { timeout: 60000 })
      if (r.code !== 0) return json(res, 500, { ok: false, error: (r.err || r.out).slice(0, 400) })
      const buf = fs.readFileSync(out)
      res.statusCode = 200
      res.setHeader('content-type', 'application/zip')
      const asciiId = String(id).replace(/[^\x20-\x7e]/g, '') || 'card'
      res.setHeader('content-disposition',
        `attachment; filename="skill-${asciiId}.zip"; filename*=UTF-8''` + encodeURIComponent(`skill-${id}.zip`))
      res.setHeader('content-length', String(buf.length))
      res.end(buf)
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  route('exact', `${API}/skillimport`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    try {
      const b = JSON.parse(await readBody(req) || '{}')
      if (!b.b64) return json(res, 400, { ok: false, error: '要带 b64（zip 内容）' })
      const tmp = path.join(os.tmpdir(), `skill-import-${Date.now()}.zip`)
      fs.writeFileSync(tmp, Buffer.from(String(b.b64), 'base64'))
      const args = ['脚本/her_skill.py', 'import', tmp]
      if (b.force) args.push('--force')
      const r = await run(args, { timeout: 60000 })
      json(res, 200, {
        ok: r.code === 0, code: r.code,
        out: (r.out || '').trim().slice(0, 800),
        error: r.code === 0 ? undefined : (r.err || r.out).slice(0, 500),
        clash: /已存在同 id/.test(r.out || ''),
      })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  route('exact', `${API}/charuse`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    try {
      const b = JSON.parse(await readBody(req) || '{}')
      const id = String(b.id || '').trim()
      if (!id) return json(res, 400, { ok: false, error: '要带 id' })
      const r = await run(['脚本/her_char.py', 'use', id], { timeout: 20000 })
      json(res, 200, { ok: r.code === 0, out: (r.out || r.err || '').trim().slice(0, 300) })
    } catch (e) {
      json(res, 500, { ok: false, error: String(e && e.message ? e.message : e) })
    }
  })

  // ── 待批准队列 ──
  route('exact', `${API}/pending`, async (req, res) => {
    try {
      let items = []
      const q = path.join(root(), '入口', '_批准.json')
      try {
        const all = JSON.parse(fs.readFileSync(q, 'utf8')).items || []
        items = all.filter((x) => x && x.state === 'pending')
      } catch (e) {}
      json(res, 200, { ok: true, items })
    } catch (e) {
      json(res, 200, { ok: false, items: [], error: String(e && e.message ? e.message : e) })
    }
  })

  route('exact', `${API}/approve`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    try {
      const b = JSON.parse(await readBody(req) || '{}')
      const r = await run(['脚本/approve.py', 'ok', String(b.id || '')], { timeout: 30000 })
      json(res, 200, { ok: r.code === 0, out: (r.out || r.err || '').trim().slice(0, 400) })
    } catch (e) { json(res, 500, { ok: false, error: String(e) }) }
  })

  route('exact', `${API}/reject`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    try {
      const b = JSON.parse(await readBody(req) || '{}')
      const args = ['脚本/approve.py', 'no', String(b.id || '')]
      if (b.why) args.push('--why', String(b.why))
      const r = await run(args, { timeout: 20000 })
      json(res, 200, { ok: r.code === 0, out: (r.out || r.err || '').trim().slice(0, 300) })
    } catch (e) { json(res, 500, { ok: false, error: String(e) }) }
  })

  // ── 审核等级 ──
  route('exact', `${API}/policy`, async (req, res) => {
    const pf = path.join(root(), '规格', '审核等级.json')
    if (req.method === 'POST') {
      if (!isLoopback(req)) return json(res, 403, { ok: false })
      try {
        const b = JSON.parse(await readBody(req) || '{}')
        const r = await run(['脚本/policy.py', 'set', String(b.level || 'strict'),
                             '--why', String(b.why || '设置页切换')], { timeout: 20000 })
        return json(res, 200, { ok: r.code === 0, out: (r.out || r.err || '').trim().slice(0, 300) })
      } catch (e) { return json(res, 500, { ok: false, error: String(e) }) }
    }
    let level = 'strict'
    try { level = JSON.parse(fs.readFileSync(pf, 'utf8')).level || 'strict' } catch (e) {}
    json(res, 200, { ok: true, level })
  })

  route('exact', `${API}/pack`, async (req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1')
    const cfg = readConfig()
    const card = u.searchParams.get('card') || cfg.defaultCard
    const skill = (u.searchParams.get('skill') || '').trim()
    if (skill || u.searchParams.get('skills') !== null) {
      const cmd = skill && skill !== '无卡' ? `载入${card} ${skill}` : `载入${card}`
      const r = await run(['脚本/load_cmd.py', cmd, '--card', card], { timeout: 60000 })
      if (r.code !== 0) return json(res, 500, { ok: false, error: (r.err || r.out).slice(0, 400) })
      return json(res, 200, { ok: true, card, skill: skill || '无卡', text: r.out,
                              han: han(r.out), chars: r.out.length })
    }
    const mode = MODES.includes(u.searchParams.get('mode')) ? u.searchParams.get('mode') : cfg.defaultMode
    // 2026-10-29：三包取消 → 直接读生成好的 出差包 / 预设
    const MAP = { '出差包': '出差包.txt', '日常': '出差包.txt',
                  '写稿': '预设-写稿.txt', '工作': '预设-写稿.txt',
                  '直播': '预设-直播.txt' }
    const fname = MAP[mode] || '出差包.txt'
    const fp = path.join(root(), '启动包', fname)
    let r2 = { code: 0, out: '' }
    try { r2 = { code: 0, out: fs.readFileSync(fp, 'utf8') } } catch (e) { r2 = { code: 2, err: String(e.message) } }
    const r = r2
    if (r.code !== 0) {
      json(res, 500, { ok: false, error: (r.err || r.out).slice(0, 400) })
      return
    }
    json(res, 200, { ok: true, card, mode, desk, text: r.out, han: han(r.out), chars: r.out.length })
  })

  // ── 新建角色 ──
  route('exact', `${API}/newcard`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    const b = JSON.parse(await readBody(req) || '{}')
    const name = String(b.name || '').trim()
    if (!name) return json(res, 400, { ok: false, error: '名字要填' })
    // id 不再让用户填：有 ASCII 就用它，没有就用时间戳
    let id = String(b.id || '').trim()
    if (!id) {
      const ascii = name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
      id = ascii && ascii.length >= 2 ? ascii.slice(0, 24) : 'card-' + Date.now().toString(36)
      const base = id
      let n = 2
      while (fs.existsSync(path.join(root(), 'cards', id))) id = `${base}-${n++}`
    }
    if (!/^[a-zA-Z0-9_-]{2,40}$/.test(id)) return json(res, 400, { ok: false, error: 'id 只能用字母数字 - _（2–40 位）' })
    const args = ['脚本/new_card.py', '--id', id, '--name', name]
    if (b.from) {
      // 现役卡是「指针卡」（cards/ 下没有目录），但 new_card.py 现已支持它；
      // 这里只在目录与注册表都找不到时才退回模板
      let from = String(b.from)
      const dirExists = fs.existsSync(path.join(root(), 'cards', from))
      if (!dirExists) {
        try {
          const reg = JSON.parse(fs.readFileSync(path.join(root(), 'cards', '_registry.json'), 'utf8').replace(/^\uFEFF/, ''))
          const hit = (reg.cards || []).find((c) => c.id === from)
          if (!hit) from = ''
        } catch {
          from = ''
        }
      }
      if (from) args.push('--from', from)
    }
    const r = await run(args, { timeout: 60000 })
    json(res, r.code === 0 ? 200 : 500, {
      ok: r.code === 0,
      id,
      out: r.out.trim(),
      error: r.code === 0 ? undefined : (r.err || r.out).slice(0, 400),
    })
  })

  // ── 出差包 ──
  route('exact', `${API}/outbound`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    const b = JSON.parse(await readBody(req) || '{}')
    let task = String(b.task || '').trim()
    if (!task) {
      // 自动取任务看板「## 现在」的第一行；拿不到就用兜底
      try {
        const desk = fs.readFileSync(path.join(root(), '入口', '任务看板.md'), 'utf8')
        const m = desk.match(/##\s*现在\s*\n+([^\n]+)/)
        task = (m && m[1].trim()) || '（未指定任务，按任务看板现在的事继续）'
      } catch {
        task = '（未指定任务，按任务看板现在的事继续）'
      }
    }
    const cfg = readConfig()
    const mode = MODES.includes(b.mode) ? b.mode : '工作'
    const args = ['脚本/outbound.py', '--to', String(b.to || '外部客户端'), '--task', task, '--mode', mode]
    if (b.card) args.push('--card', String(b.card))
    if (cfg.outboundDir) args.push('--out', cfg.outboundDir)
    const r = await run(args, { timeout: 90000 })
    json(res, r.code === 0 ? 200 : 500, {
      ok: r.code === 0,
      out: r.out.trim(),
      error: r.code === 0 ? undefined : (r.err || r.out).slice(0, 400),
      defaultCard: cfg.defaultCard,
    })
  })

  // ── 在资源管理器里打开（目录或选中文件） ──
  route('exact', `${API}/reveal`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    const b = JSON.parse(await readBody(req) || '{}')
    let target = String(b.path || '').trim()
    if (!target) return json(res, 400, { ok: false, error: 'path 不能为空' })
    if (!path.isAbsolute(target)) target = path.join(root(), target)
    if (!fs.existsSync(target)) return json(res, 404, { ok: false, error: '路径不存在：' + target })
    const isDir = fs.statSync(target).isDirectory()
    const args = isDir ? [target] : ['/select,', target]
    const child = spawn('explorer.exe', args, { detached: true, stdio: 'ignore', windowsHide: false })
    child.unref()
    json(res, 200, { ok: true, path: target, isDir })
  })

  // ── 查询器（只读：L3 / 任务看板已作废 / L2） ──
  route('exact', `${API}/query`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    const b = JSON.parse(await readBody(req) || '{}')
    const q = String(b.q || '').trim()
    const when = String(b.when || '').trim()
    const kind = String(b.kind || '').trim()
    if (!q && !when) return json(res, 400, { ok: false, error: '给个关键词或时间' })

    let args
    if (/^D-\d+$/i.test(q)) {
      args = ['脚本/wb_query.py', q]                     // 任务看板已作废条目
      if (b.chain) args.push('--chain')
    } else if (b.source === 'l2') {
      args = ['脚本/memory_lookup.py', '--topic', q]      // L2 记忆
    } else {
      args = ['脚本/l3.py', 'grep', q || ' ']             // L3 日志
      if (when) args.push('--when', when)
      if (kind) args.push('--kind', kind)
      args.push('--limit', String(b.limit || 20))
    }
    const r = await run(args, { timeout: 60000 })
    json(res, r.code === 0 || (r.out || '').trim() ? 200 : 500, {
      ok: r.code === 0,
      text: (r.out || '').trim(),
      error: r.code === 0 ? undefined : (r.err || r.out || '').trim().slice(0, 300),
      cmd: args.join(' '),
    })
  })

  // ── 任务看板已作废：列清单（只读） ──
  route('exact', `${API}/retired`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    const r = await run(['脚本/wb_query.py', '--list'], { timeout: 30000 })
    const text = (r.out || '').trim()
    json(res, 200, { ok: Boolean(text), text,
      error: text ? undefined : (r.err || r.out || '').trim().slice(0, 300) })
  })

  // ── 保存配置 ──
  route('exact', `${API}/config`, async (req, res) => {
    if (!isLoopback(req)) return json(res, 403, { ok: false, error: '只接受本机回环' })
    if (req.method !== 'POST') return json(res, 405, { ok: false })
    const b = JSON.parse(await readBody(req) || '{}')
    const patch = {}
    for (const k of ['projectRoot', 'pythonPath', 'defaultCard', 'defaultMode', 'outboundDir']) {
      if (typeof b[k] === 'string' && b[k].trim()) patch[k] = b[k].trim()
    }
    json(res, 200, { ok: true, config: writeConfig(patch), configPath: CFG })
  })

  ctx.effect?.(() => () => {
    for (const d of scripts) {
      try { d?.() } catch {}
    }
  }, 'dsh-her: routes')
}
