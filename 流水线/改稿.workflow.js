export const meta = {
  name: '改稿',
  description: '把用户一条修改意见变成标准（用户口径、检查规则），再改一个或全部项目的正文，检查清零后出稿',
  whenToUse: '用户对报告提意见、要求改某处或某类写法；意见要在所有项目生效时，项目写“全部”',
  phases: [
    { title: '定口径', detail: '意见写进用户口径和检查规则；定位各项目要改的文件' },
    { title: '修改', detail: '每个文件一个助手，按意见改，改完该文件检查为 0' },
    { title: '复查', detail: '各项目全文检查为 0' },
    { title: '出稿', detail: 'WPS 两轮导出、发布，交稿说明追加一条' },
  ],
}

// 子助手因中转服务或网络出错（返回 null 或抛错）时，原样再起一次，最多 3 次
async function 跑(prompt, opts) {
  for (let i = 1; i <= 3; i++) {
    try {
      const r = await agent(prompt, opts)
      if (r !== null && r !== undefined) return r
    } catch (e) {
      if (i === 3) log(`${(opts && opts.label) || '子助手'} 出错：${String(e).slice(0, 80)}`)
    }
    if (i < 3) log(`${(opts && opts.label) || '子助手'} 第 ${i} 次没成，重来`)
  }
  return null
}

// args：{ 意见: 用户原话, 项目: 项目文件夹完整路径 | 路径数组 | '全部', 日期: '2026-11-02', 出稿: true|false（默认 true）, 工厂 }
const A = args || {}
const F = A.工厂 || '/Users/a1234/Desktop/评价报告流水线'
if (!A.意见) throw new Error('缺 args.意见（用户原话）')
const 目标 = Array.isArray(A.项目) ? A.项目.join('\n') : (A.项目 || '全部')
const 出 = A.出稿 !== false
const R = `python3 ${F}/scripts/report.py`
const S = `${F}/标准`
const 通则 = `你是“职业病危害预评价报告”流水线里的子助手。
标准：${S}/用户口径.md（用户定过的写法，最高优先）、${S}/通用检查规则.md、${S}/各章要点/。项目登记：${F}/项目登记.md。
规矩：大文件不整篇读，先 grep 再按行号读；只动指定给你的文件；不给别的对话发消息；中文。
`

const 定结果 = {
  type: 'object',
  properties: {
    口径号: { type: 'string' },
    标准改动: { type: 'array', items: { type: 'string' } },
    改法: { type: 'string' },
    项目: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          文件夹: { type: 'string' }, 名称: { type: 'string' },
          备份: { type: 'string' },
          任务: { type: 'array', items: { type: 'object', properties: { 文件: { type: 'string' }, 位置: { type: 'array', items: { type: 'string' } } }, required: ['文件', '位置'] } },
        },
        required: ['文件夹', '名称', '任务'],
      },
    },
  },
  required: ['口径号', '标准改动', '改法', '项目'],
}
const 修结果 = {
  type: 'object',
  properties: { 改: { type: 'array', items: { type: 'string' } }, 跳过: { type: 'array', items: { type: 'string' } }, 剩余错误: { type: 'integer' } },
  required: ['改', '剩余错误'],
}
const 检结果 = {
  type: 'object',
  properties: {
    错误: { type: 'integer' }, 提示: { type: 'integer' }, 待办: { type: 'integer' },
    按文件: { type: 'array', items: { type: 'object', properties: { 文件: { type: 'string' }, 错误: { type: 'array', items: { type: 'string' } } }, required: ['文件', '错误'] } },
  },
  required: ['错误', '提示', '待办', '按文件'],
}
const 出结果 = {
  type: 'object',
  properties: { 通过: { type: 'boolean' }, 页数: { type: 'integer' }, 问题: { type: 'string' } },
  required: ['通过'],
}

phase('定口径')
const k = await 跑(通则 + `用户的修改意见（原话，${A.日期 || ''}）：
“${A.意见}”
涉及项目：${目标}（“全部”指 项目登记.md 里的全部项目）。
任务：
1. 读 ${S}/用户口径.md、${S}/通用检查规则.md。判断这条意见：
   - 是以后都要这样写的通用写法：在用户口径.md 末尾加一条（编号接着往下，格式照前面各条，用户原话照抄）；能用词语查的，在通用检查规则.md 加禁用／正则禁用／提示项，上一行注明 K 号。推翻旧条的，旧条标题后加“（已被 K0xx 取代）”，并删掉旧条在通用检查规则.md 里对应的项。
   - 只针对某个项目的具体内容（某张表、某个数字）：不动通用标准，在该项目自己的 检查规则.md 加禁用或必备项（注明日期和用户原话）。
   - 与某章写法有关的：在 ${S}/各章要点/ 对应卡的“红线”或“常见错误”加一句。
   加禁用词前，先 grep 项目登记里样板项目的 正文/，有正常用法的词说明太宽，换更具体的词或放“提示”。
   改了标准的，在 ${F} 运行 git add -A 标准 && git commit -qm "改稿 K号：意见前 20 字"，留版本记录。
2. 对每个涉及的项目：在项目文件夹里执行 cp -R 正文 "备份/正文_$(date +%Y%m%d_%H%M)_改稿前"（没有 备份/ 先建）；运行 ${R} check 项目文件夹 --json 看新规则查出的位置；再 grep 意见涉及的说法，查规则查不到的地方。按文件列出要改的位置（“行号 原文片段”，每处一行）。意见不能用词查的（如“第3章写得太深”），列出涉及的文件，位置写“全文按意见改”。
返回：口径号（只改项目规则的写“项目”）、标准改动（每项一句）、改法（给修改助手看的具体改法，一段话）、各项目的文件夹、简称、备份路径和任务。没有要改的项目，任务写空列表。`, { label: '定口径', phase: '定口径', schema: 定结果 })
if (!k) throw new Error('定口径没做成，停下')
const tasks = k.项目.flatMap(p => p.任务.map(t => ({ ...t, 项目: p.文件夹, 名称: p.名称 })))
log(`${k.口径号}：${k.项目.length} 个项目，${tasks.length} 个文件要改`)

const 修 = await pipeline(tasks, t => 跑(通则 + `任务：按用户意见改 ${t.项目}/正文/${t.文件}。
用户原话：“${A.意见}”
改法：${k.改法}
要改的位置（行号可能已偏移，按原文找）：
${t.位置.map(x => '- ' + x).join('\n')}
先读 ${S}/用户口径.md 里 ${k.口径号} 那条，和 ${S}/各章要点/ 里本章的卡。涉及事实的，回 资料/事实库.md 或可研文字版核对，不编。只改与这条意见有关的地方，前后文读顺。
改完运行 ${R} check ${t.项目} --json --章 ${t.文件.replace(/\.md$/, '')}，这个文件的错误改到 0。
返回：改了的（每处一句：原文 → 新文，各不超过 30 字）、跳过的（理由）、剩余错误数。`,
  { label: `改 ${t.名称} ${t.文件}`, phase: '修改', schema: 修结果, effort: 'medium' })
  .then(r => ({ ...t, r })))

async function 清零(p) {
  const 查 = label => 跑(`运行 ${R} check ${p.文件夹} --json，照输出返回：错误、提示、待办条数；“按文件”里有错误的文件，每个列出它的错误（照抄）。只运行这一条命令，不改任何文件。`,
    { label, phase: '复查', schema: 检结果, effort: 'low' })
  for (let round = 1; round <= 3; round++) {
    const c = await 查(`检查 ${p.名称} 第${round}轮`)
    if (!c || c.错误 === 0) return c
    await parallel(c.按文件.map(x => () => 跑(通则 + `任务：把 ${p.文件夹} 的这些机器检查错误改掉（位置：${x.文件}）。
${x.错误.map(e => '- ' + e).join('\n')}
位置写“正文”“检查规则.md”“项目信息.md”的，先 grep 找到出处。“通用规则禁用”的照 ${S}/用户口径.md 对应条目改写，不换近义词躲检查。
改完运行 ${R} check ${p.文件夹} --json --章 ${x.文件.replace(/\.md$/, '')}，确认为 0。返回改了几处。`,
      { label: `清零 ${p.名称} ${x.文件}`, phase: '复查', effort: 'medium' })))
  }
  return 查(`检查 ${p.名称} 最终`)
}

phase('复查')
const 查果 = await parallel(k.项目.map(p => () => 清零(p)))

const 出果 = []
if (出) {
  phase('出稿')
  for (let i = 0; i < k.项目.length; i++) {
    const p = k.项目[i]
    const c = 查果[i]
    const n = 修.filter(x => x && x.项目 === p.文件夹 && x.r).reduce((s, x) => s + x.r.改.length, 0)
    if (!c || c.错误 || !n) { 出果.push({ 通过: false, 问题: !n ? '没有改动，不出稿' : '检查没清零，不出稿' }); continue }
    出果.push(await 跑(`任务：给 ${p.文件夹} 出稿。
1. 运行 ${R} 出稿 ${p.文件夹}（Bash 超时设 600000 毫秒；它会排队等 WPS），记下结果里的页数和是否“核对通过”。
2. 在 ${p.文件夹}/输出/交稿说明.md 末尾追加一节（文件没有就新建）：“## ${A.日期 || '本轮'} 按意见修改”，写用户原话、改了 ${n} 处（按文件列）、检查结果、PDF 页数。中文短句。
返回：是否通过、页数、问题（没有写空）。`, { label: '出稿 ' + p.名称, phase: '出稿', schema: 出结果, effort: 'low' }))
  }
}

return {
  口径号: k.口径号, 标准改动: k.标准改动,
  项目: k.项目.map((p, i) => {
    const mine = 修.filter(x => x && x.项目 === p.文件夹)
    return {
      名称: p.名称,
      改: mine.reduce((s, x) => s + ((x.r && x.r.改.length) || 0), 0),
      跳过: mine.flatMap(x => (x.r && x.r.跳过) || []).slice(0, 5),
      没改成: mine.filter(x => !x.r).map(x => x.文件),
      检查: 查果[i] ? { 错误: 查果[i].错误, 提示: 查果[i].提示, 待办: 查果[i].待办 } : null,
      出稿: 出果[i] || null,
      备份: p.备份 || '',
    }
  }),
}
