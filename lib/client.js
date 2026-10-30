/**
 * dsh-her —— 客户端（设置页 + 输入框 dock）
 *
 * 设置页「人格」：
 *   · 填「人格与记忆」目录 + python 路径
 *   · 卡列表（id / 名字 / 场景）
 *   · 选卡 + 选模式 → 算汉字数 → 复制注入包
 *   · 新建角色（id / 名字，从模板复制）
 *   · 出差包（目的地 / 任务 → 生成，复制路径）
 *
 * 输入框 dock：一个「载入人格 ▾」按钮，点一下把默认卡+默认模式的包复制到剪贴板。
 *
 * 说明：P1 只做「复制到剪贴板」。真正写进输入框（P2）要等 DSH 的 composer 写入接口确认。
 */

window.__ModuleLoader__.load({
  id: 'dsh-her',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const API = '/dsh-her/api'

    const S = {
      box: { padding: '4px 0' },
      row: { display: 'flex', gap: 8, alignItems: 'center', margin: '8px 0', flexWrap: 'wrap' },
      label: { minWidth: 96, opacity: 0.75, fontSize: 13 },
      input: {
        flex: 1, minWidth: 260, padding: '6px 8px', borderRadius: 6,
        border: '1px solid var(--dsh-border, #3a3f45)', background: 'transparent',
        color: 'inherit', fontSize: 13,
      },
      btn: {
        padding: '6px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 13,
        border: '1px solid var(--dsh-border, #3a3f45)', background: 'transparent',
        color: 'inherit',
      },
      btnMain: { padding: '6px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 13 },
      table: { width: '100%', borderCollapse: 'collapse', fontSize: 13 },
      th: { textAlign: 'left', padding: '6px 8px', opacity: 0.7, fontWeight: 500 },
      td: { padding: '6px 8px', borderTop: '1px solid var(--dsh-border, #2c3137)' },
      hint: { fontSize: 12, opacity: 0.6, marginTop: 4 },
      ok: { fontSize: 12, color: '#4caf50' },
      bad: { fontSize: 12, color: '#e57373' },
      mono: { fontFamily: 'ui-monospace, monospace', fontSize: 12, opacity: 0.85 },
      h3: { fontSize: 14, margin: '18px 0 6px' },
    }

    async function api(path, opts) {
      const r = await fetch(API + path, {
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        ...opts,
      })
      const j = await r.json().catch(() => ({ ok: false, error: 'bad json' }))
      if (!r.ok && !j.error) j.error = 'HTTP ' + r.status
      return j
    }

    async function copy(text) {
      // ① 标准 API（Electron 的 dsh-app:// 里常被拒：NotAllowedError）
      try {
        await navigator.clipboard.writeText(text)
        return true
      } catch {}
      // ② 老办法：临时 textarea + execCommand
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.setAttribute('readonly', '')
        ta.style.position = 'fixed'
        ta.style.top = '-1000px'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        const ok = document.execCommand('copy')
        document.body.removeChild(ta)
        if (ok) return true
      } catch {}
      return false
    }

    function HerSection() {
      const [st, setSt] = React.useState(null)
      const [card, setCard] = React.useState('')
      const [skill, setSkill] = React.useState('无卡')
      const [skills, setSkills] = React.useState([])
      const [size, setSize] = React.useState(null)
      const [msg, setMsg] = React.useState('')
      const [busy, setBusy] = React.useState(false)
      const [root, setRoot] = React.useState('')
      const [py, setPy] = React.useState('')
      const [newId, setNewId] = React.useState('')
      const [newName, setNewName] = React.useState('')
      const [tripPath, setTripPath] = React.useState('')
      const [editOut, setEditOut] = React.useState(false)
      const [outDir, setOutDir] = React.useState('')
      const [manual, setManual] = React.useState('')
      const [loaded, setLoaded] = React.useState(null)   // 载入过的 {card, mode}
      const [policy, setPolicy] = React.useState('strict')
      const [skillMsg, setSkillMsg] = React.useState('')
      const [qText, setQText] = React.useState('')
      const [qWhen, setQWhen] = React.useState('')
      const [qSource, setQSource] = React.useState('l3')
      const [qOut, setQOut] = React.useState('')

      const load = React.useCallback(async () => {
        const j = await api('/state')
        setSt(j)
        setRoot(j.config?.projectRoot || '')
        setPy(j.config?.pythonPath || '')
        setOutDir(j.config?.outboundDir || '')
        setCard((c) => c || j.config?.defaultCard || j.cards?.[0]?.id || '')
        setSkills(j.skills || [])
      }, [])

      React.useEffect(() => { load() }, [load])
      React.useEffect(() => {
        api('/policy').then((j) => { if (j.ok) setPolicy(j.level || 'strict') }).catch(() => {})
      }, [])

      const doPolicy = async () => {
        setBusy(true)
        const j = await api('/policy', { method: 'POST', body: JSON.stringify({ level: policy, why: '设置页切换' }) })
        setMsg(j.ok ? `审核等级已切到 ${policy}` : '切换失败：' + (j.out || j.error || ''))
        setBusy(false)
      }

      const doSave = async () => {
        setBusy(true)
        const j = await api('/config', { method: 'POST', body: JSON.stringify({ projectRoot: root, pythonPath: py }) })
        setMsg(j.ok ? '配置已保存' : '保存失败：' + j.error)
        setBusy(false)
        load()
      }

      const doExportChar = async () => {
        if (!card) { setMsg('先选一张卡'); return }
        setBusy(true)
        setMsg('正在打包…')
        try {
          const r = await fetch(`${API}/charexport?id=${encodeURIComponent(card)}`)
          if (!r.ok) {
            const j = await r.json().catch(() => ({}))
            setMsg('导出失败：' + (j.error || ('HTTP ' + r.status)))
          setSkillMsg('导出失败：' + (j.error || ('HTTP ' + r.status)))
            setBusy(false)
            return
          }
          const blob = await r.blob()
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = `${card}.zip`
          document.body.appendChild(a)
          a.click()
          a.remove()
          setTimeout(() => URL.revokeObjectURL(url), 3000)
          setMsg(`已导出 ${card}.zip（${Math.round(blob.size / 1024)} KB）`)
        } catch (e) {
          setMsg('导出失败：' + String(e))
        }
        setBusy(false)
      }

      const doUseCard = async () => {
        if (!card) return
        setBusy(true)
        const j = await api('/charuse', { method: 'POST', body: JSON.stringify({ id: card }) })
        setBusy(false)
        setMsg(j.ok ? `默认卡已设为 ${card}（开场 / 预检 / 生成包都用它）` : ('设默认失败：' + (j.error || '')))
        load()
      }

      const doExportSkill = async () => {
        if (!skill || skill === '无卡') { setMsg('先在下面选一张技能卡'); return }
        setBusy(true)
        setMsg('正在打包技能卡…')
        setSkillMsg('打包中…')
        try {
          const r = await fetch(`${API}/skillexport?id=${encodeURIComponent(skill)}`)
          if (!r.ok) {
            const j = await r.json().catch(() => ({}))
            setMsg('导出失败：' + (j.error || ('HTTP ' + r.status)))
          setSkillMsg('导出失败：' + (j.error || ('HTTP ' + r.status)))
            setBusy(false)
            return
          }
          const blob = await r.blob()
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          const ascii = (skill || '').replace(/[^\x20-\x7e]/g, '').replace(/[^\w.-]/g, '')
          a.download = `skill-${ascii || 'card'}.zip`
          document.body.appendChild(a)
          a.click()
          a.remove()
          setTimeout(() => URL.revokeObjectURL(url), 3000)
          setMsg(`已导出 skill-${skill}.zip（${Math.round(blob.size / 1024)} KB，含子卡）`)
          setSkillMsg(`已下载（${Math.round(blob.size / 1024)} KB）`)
        } catch (e) { setMsg('导出失败：' + String(e)); setSkillMsg('导出失败：' + String(e)) }
        setBusy(false)
      }

      const skillFileRef = React.useRef(null)
      const doImportSkill = async (file) => {
        if (!file) return
        setBusy(true)
        setMsg('正在检查技能卡…')
        try {
          const buf = await file.arrayBuffer()
          let bin = ''
          const bytes = new Uint8Array(buf)
          for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
          const j = await api('/skillimport', { method: 'POST', body: JSON.stringify({ b64: btoa(bin) }) })
          if (j.ok) {
            setMsg('技能卡导入成功：' + (j.out || '').split('\n')[0])
            load()
          } else if (j.clash) {
            setMsg('已存在同 id 的卡；要覆盖请命令行加 --force，或先改名')
          } else {
            setMsg('技能卡导入失败：' + (j.error || '未知'))
          }
        } catch (e) { setMsg('导入失败：' + String(e)) }
        setBusy(false)
      }

      const fileRef = React.useRef(null)
      const doImportChar = async (file) => {
        if (!file) return
        setBusy(true)
        setMsg('正在检查这张卡…')
        try {
          const buf = await file.arrayBuffer()
          let bin = ''
          const bytes = new Uint8Array(buf)
          for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
          const b64 = btoa(bin)
          const j = await api('/charimport', { method: 'POST', body: JSON.stringify({ b64 }) })
          if (j.ok) {
            setMsg('导入成功：' + (j.out || '').split('\n')[0])
            load()
          } else if (j.questions && j.questions.length) {
            setMsg('这张卡不合规范，需要你回答：' + j.questions.join(' ｜ '))
          } else {
            setMsg('导入失败：' + (j.error || '未知'))
          }
        } catch (e) {
          setMsg('导入失败：' + String(e))
        }
        setBusy(false)
      }

      const doSize = async () => {
        setBusy(true)
        const j = await api(`/pack?card=${encodeURIComponent(card)}&skill=${encodeURIComponent(skill)}`)
        setSize(j.ok ? { han: j.han, chars: j.chars } : null)
        setMsg(j.ok ? '' : '取包失败：' + j.error)
        setBusy(false)
      }

      const doCopy = async () => {
        setBusy(true)
        const j = await api(`/pack?card=${encodeURIComponent(card)}&skill=${encodeURIComponent(skill)}`)
        setBusy(false)
        if (!j.ok) { setMsg('取包失败：' + j.error); return }
        setSize({ han: j.han, chars: j.chars })
        const okCopy = await copy(j.text)
        if (okCopy) {
          setManual('')
          setLoaded({ card, skill })
          setMsg(`已复制 ${j.han} 汉字（${card} / ${skill}），粘贴到新对话即可`)
        } else {
          setManual(j.text)
          setMsg('复制被环境拒绝：下面已摊开全文，Ctrl+A 再 Ctrl+C')
        }
      }

      const closeText = (info) => [
        '收尾：',
        '1. 按模板写 handoff（主题/谁做的/做了什么/起点/卡点/走过的岔路/试过没成的/取舍/哪里还没想明白/原话/open/closes/留下）',
        '2. 更新任务看板（现在 / 卡在哪 / 下一步；已定只追不删，过时用 retire）',
        '3. 跑 python 脚本/workbench.py render（归档 + 刷三包 + L3 校验）',
        '',
        `这一轮：${info.card}｜${info.skill || '无卡'}`,
        '写不了就先用 mark_close_pending 留一行「待补」，别硬写。',
      ].join('\n')

      const doClose = async () => {
        if (!loaded) return
        const okCopy = await copy(closeText(loaded))
        if (okCopy) {
          setMsg(`已复制收尾指令（${loaded.card} / ${loaded.skill || '无卡'}）——粘进对话，她自己会收尾`)
          setLoaded(null)
        } else {
          setManual(closeText(loaded))
          setMsg('复制被拒：下面已摊开收尾指令，Ctrl+A 再 Ctrl+C')
        }
      }

      const doNew = async () => {
        setBusy(true)
        const j = await api('/newcard', { method: 'POST', body: JSON.stringify({ name: newName, from: card || undefined }) })
        setBusy(false)
        setMsg(j.ok ? `已建卡「${newName}」，id=${j.id}` : '建卡失败：' + j.error)
        if (j.ok) { setNewName(''); load() }
      }

      const reveal = async (p) => {
        const j = await api('/reveal', { method: 'POST', body: JSON.stringify({ path: p }) })
        if (!j.ok) setMsg('打开失败：' + j.error)
      }

      const saveOutDir = async () => {
        setBusy(true)
        const j = await api('/config', { method: 'POST', body: JSON.stringify({ outboundDir: outDir }) })
        setBusy(false)
        setEditOut(false)
        setMsg(j.ok ? '输出地址已保存：' + outDir : '保存失败：' + j.error)
      }

      const doQuery = async () => {
        setBusy(true)
        setQOut('')
        const j = await api('/query', {
          method: 'POST',
          body: JSON.stringify({ q: qText, when: qWhen, source: qSource }),
        })
        setBusy(false)
        setQOut(j.ok ? j.text : ('查询失败：' + j.error))
      }

      const doOut = async () => {
        setBusy(true)
        const j = await api('/outbound', { method: 'POST', body: JSON.stringify({ mode: skill === '无卡' ? '出差包' : '写稿', card }) })
        setBusy(false)
        if (j.ok) {
          const m = (j.out || '').match(/出差包[:：]\s*(.+)/)
          setTripPath(m ? m[1].trim() : (j.out || '').split('\n')[0])
          setMsg('')
        } else {
          setTripPath('')
          setMsg('生成失败：' + j.error)
        }
      }

      const cards = st?.cards || []
      const cur = cards.find((c) => c.id === card)

      return h('div', { style: S.box },
        h('h3', { style: S.h3 }, '人格与记忆'),
        h('div', { style: S.hint }, '引擎是纯 Python：插件只负责选卡、取包、复制、建卡、出差。'),

        h('div', { style: S.row },
          h('span', { style: S.label }, '人格与记忆目录'),
          h('input', { style: S.input, value: root, placeholder: 'D:\\\\...\\人格与记忆', onChange: (e) => setRoot(e.target.value) }),
        ),
        h('div', { style: S.row },
          h('span', { style: S.label }, 'python 路径'),
          h('input', { style: S.input, value: py, placeholder: 'python 或完整路径', onChange: (e) => setPy(e.target.value) }),
          h('button', { style: S.btn, disabled: busy, onClick: doSave }, '保存'),
        ),
        h('div', { style: S.row },
          h('button', { style: S.btn, disabled: busy || !card, onClick: doExportChar }, '导出角色（.zip）'),
          h('button', { style: S.btn, disabled: busy || !card, onClick: doUseCard }, '设为默认'),
          h('button', { style: S.btn, disabled: busy, onClick: () => fileRef.current && fileRef.current.click() }, '导入角色'),
          h('input', {
            ref: fileRef, type: 'file', accept: '.zip', style: { display: 'none' },
            onChange: (e) => { const f = e.target.files && e.target.files[0]; e.target.value = ''; doImportChar(f) },
          }),
        ),
        h('div', { style: S.hint }, '导出：卡 + 记忆打包（不含 L3 历史）。导入：不合规范时会列出要你回答的问题。'),

        h('h3', { style: S.h3 }, '载入'),
        h('div', { style: S.row },
          h('select', { style: { ...S.input, flex: 2, minWidth: 200 }, value: card, onChange: (e) => { setCard(e.target.value); setSize(null) } },
            cards.map((c) => h('option', { key: c.id, value: c.id }, `${c.name}（${c.id}）`)),
          ),
          h('select', { style: { ...S.input, flex: 0, minWidth: 140 }, value: skill, onChange: (e) => { setSkill(e.target.value); setSize(null) } },
            [h('option', { key: 'none', value: '无卡' }, '无卡（只带本体）')].concat(
              (skills || []).map((x) => h('option', { key: x, value: x }, x)),
            ),
          ),
        ),
        h('div', { style: S.row },
          h('button', { style: S.btn, disabled: busy, onClick: doSize }, '算体积'),
          h('button', { style: S.btnMain, disabled: busy, onClick: doCopy }, '复制注入包'),
          loaded
            ? h('button', { style: { ...S.btn, borderColor: '#c9a227' }, disabled: busy, onClick: doClose }, '收尾')
            : h('button', { style: S.btn, disabled: true, title: '先点「复制注入包」载入，再收尾' }, '收尾'),
          size ? h('span', { style: S.ok }, `${size.han} 汉字 / ${size.chars} 字符`) : null,
          cur ? h('span', { style: S.hint }, '场景：' + (cur.scenes || []).join('、')) : null,
        ),
        h('div', { style: S.hint }, '技能卡一张就够；素材不进这里（需要时让她自己查记忆）。'),
        h('div', { style: S.row },
          h('button', { style: S.btn, disabled: busy || !skill || skill === '无卡', onClick: doExportSkill, title: (!skill || skill === '无卡') ? '先在上面「技能卡」下拉里选一张（不能是「无卡」）' : '把这张技能卡和它的子卡打包下载' }, '导出技能卡（.zip）'),
          h('button', { style: S.btn, disabled: busy, onClick: () => skillFileRef.current && skillFileRef.current.click() }, '导入技能卡'),
          h('input', {
            ref: skillFileRef, type: 'file', accept: '.zip', style: { display: 'none' },
            onChange: (e) => { const f = e.target.files && e.target.files[0]; e.target.value = ''; doImportSkill(f) },
          }),
          skillMsg ? h('span', { style: S.hint }, skillMsg) : null,
        ),
        h('div', { style: S.hint }, '技能卡包 = 技能卡 + 它的子卡（任务卡）；上面选的哪张，就导出哪张（连子卡一起）。'),
        h('h3', { style: S.h3 }, '审核等级'),
        h('div', { style: S.row },
          h('select', {
            style: { ...S.input, flex: 0, minWidth: 160 },
            value: policy,
            onChange: (e) => setPolicy(e.target.value),
          },
            h('option', { value: 'strict' }, '严格（全部要签发）'),
            h('option', { value: 'simple' }, '简易（写记忆/交接免签）'),
          ),
          h('button', { style: S.btn, disabled: busy, onClick: doPolicy }, '保存等级'),
        ),

        h('h3', { style: S.h3 }, '新建角色'),
        h('div', { style: S.row },
          h('input', { style: S.input, value: newName, placeholder: '角色名字（id 自动生成）', onChange: (e) => setNewName(e.target.value) }),
          h('button', { style: S.btn, disabled: busy || !newName, onClick: doNew }, '建卡'),
        ),
        h('div', { style: S.hint }, '会从当前选中的卡复制一份，并建一个空的记忆套件；id 由名字自动生成。'),

        h('h3', { style: S.h3 }, '出差包'),
        h('div', { style: S.row },
          h('button', { style: S.btnMain, disabled: busy, onClick: doOut }, '生成出差包'),
          h('button', { style: S.btn, disabled: busy || !tripPath, onClick: () => reveal(tripPath) }, '打开地址'),
          h('button', { style: S.btn, disabled: busy, onClick: () => setEditOut(!editOut) }, '更改地址'),
        ),
        editOut ? h('div', { style: S.row },
          h('input', { style: S.input, value: outDir, placeholder: root + '\\出差包', onChange: (e) => setOutDir(e.target.value) }),
          h('button', { style: S.btn, disabled: busy, onClick: saveOutDir }, '保存'),
        ) : null,
        h('div', { style: S.mono }, '生成地址：' + (tripPath || (outDir || root + '\\出差包'))),

        h('h3', { style: S.h3 }, '查询器'),
        h('div', { style: S.row },
          h('input', {
            style: S.input, value: qText, placeholder: '关键词（空格/逗号分隔=全部命中）或 D-13',
            onChange: (e) => setQText(e.target.value),
            onKeyDown: (e) => { if (e.key === 'Enter') doQuery() },
          }),
          h('input', {
            style: { ...S.input, flex: 0, minWidth: 120 },
            value: qWhen, placeholder: '上周 / 10 月中',
            onChange: (e) => setQWhen(e.target.value),
          }),
          h('select', {
            style: { ...S.input, flex: 0, minWidth: 96 }, value: qSource,
            onChange: (e) => setQSource(e.target.value),
          },
            h('option', { value: 'l3' }, 'L3 日志'),
            h('option', { value: 'l2' }, 'L2 记忆'),
          ),
          h('button', { style: S.btnMain, disabled: busy || (!qText && !qWhen), onClick: doQuery }, '查询'),
        ),
        h('div', { style: S.hint }, '时间可以不填；认不出的说法会明确报「没听懂时间」，不会瞎猜。'),
        h('div', { style: { ...S.row, marginTop: 6 } },
          h('button', {
            style: S.btn, disabled: busy,
            onClick: async () => {
              setBusy(true); setQOut('')
              const j = await api('/retired', { method: 'GET' })
              setBusy(false)
              setQOut(j.ok ? (j.text || '（没有已作废条目）') : ('取清单失败：' + j.error))
            },
          }, '列出已作废'),
          h('span', { style: S.hint }, '按编号查（填 D-13）或按关键词查；要追溯替代链用 wb_query --chain'),
        ),
        qOut ? h('textarea', {
          readOnly: true, value: qOut, rows: 10,
          style: { ...S.input, width: '100%', fontFamily: 'ui-monospace, monospace', fontSize: 12, marginTop: 8 },
        }) : null,

        manual ? h('textarea', {
          readOnly: true, value: manual, rows: 14,
          onFocus: (e) => e.target.select(),
          style: { ...S.input, width: '100%', fontFamily: 'ui-monospace, monospace', fontSize: 12, marginTop: 8 },
        }) : null,
        msg ? h('div', { style: (msg.includes('失败') || msg.includes('拒绝')) ? S.bad : S.ok }, msg) : null,
        st?.error ? h('div', { style: S.bad }, '读卡失败：' + st.error) : null,
        h('div', { style: S.mono }, '配置：' + (st?.configPath || '')),
      )
    }

    function DockButton() {
      const [busy, setBusy] = React.useState(false)
      const [tip, setTip] = React.useState('')
      const [pend, setPend] = React.useState([])
      const [showPend, setShowPend] = React.useState(false)

      const refreshPend = React.useCallback(async () => {
        try {
          const j = await api('/pending')
          setPend(j.items || [])
        } catch (e) { setPend([]) }
      }, [])
      React.useEffect(() => { refreshPend() }, [refreshPend])

      const doClose = async () => {
        setBusy(true)
        try {
          const st = await api('/state')
          const cardId = st.config?.defaultCard || st.cards?.[0]?.id || ''
          const line = [
            '收尾：',
            '1. 写 handoff（主题/谁做的/做了什么/起点/卡点/岔路/取舍/哪里还没想明白/原话/open/留下）',
            '2. 更新任务看板（现在 / 卡在哪 / 下一步；退役条目要写理由）',
            '3. 跑 python 脚本/workbench.py render（归档 + 出差包/预设 + 索引 + L3 校验）',
            '',
            `这一轮：${cardId}`,
          ].join('\n')
          const okCopy = await copy(line)
          setTip(okCopy ? '已复制收尾指令' : '复制被拒')
        } catch (e) { setTip('复制失败') }
        setBusy(false)
        setTimeout(() => setTip(''), 4000)
      }

      const doApprove = async (id) => {
        setBusy(true)
        const j = await api('/approve', { method: 'POST', body: JSON.stringify({ id }) })
        setBusy(false)
        setTip(j.ok ? `已同意 ${id}` : '同意失败')
        setTimeout(() => setTip(''), 3500)
        refreshPend()
      }

      const btn = (label, onClick, opts) => h('button', {
        style: { ...S.btn, fontSize: 12, padding: '4px 10px', ...(opts && opts.style) },
        title: (opts && opts.title) || label,
        disabled: busy,
        onClick,
      }, label)

      const n = pend.length
      return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6 } },
        btn(tip || '收尾', doClose, { title: '复制收尾指令，粘进对话她就收尾' }),
        btn(n ? `同意 ${n}` : '同意', () => { setShowPend((v) => !v); refreshPend() }, {
          title: '角色提审批时这里会亮；点一下＝同意',
          style: n ? { ...S.btn, background: '#b45309', color: '#fff', fontSize: 12, padding: '4px 10px' } : undefined,
        }),
        showPend ? h('span', { style: { display: 'inline-flex', gap: 4 } },
          (n ? pend : [{ id: '' }]).slice(0, 4).map((x) => h('span', {
            key: x.id || 'none', style: { ...S.hint, fontSize: 11 },
          }, x.id
            ? h('button', {
                style: { ...S.btn, fontSize: 11, padding: '2px 8px' },
                title: x.why, disabled: busy, onClick: () => doApprove(x.id),
              }, `${x.id} 同意`)
            : '没有待批准'))) : null,
      )
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        ctx.slots.inject('settings.section', () => ctx.slots.register(
          { name: 'settings.section', id: 'dsh-her', order: 7, label: () => '人格' },
          HerSection,
        ))
        ctx.slots.inject('conversation.composer.dock', () => ctx.slots.register(
          { name: 'conversation.composer.dock', id: 'dsh-her-load', order: 20 },
          DockButton,
        ))
      },
    }
  },
})
