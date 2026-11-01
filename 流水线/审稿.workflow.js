export const meta = {
  name: '审稿',
  description: '按审稿清单逐章审一份预评价报告，可直接改；最后查跨章一致、机器检查清零',
  whenToUse: '用户要全文检查、审核、审稿某个报告；新报告流水线最后一步也调用它',
  phases: [
    { title: '准备', detail: '列正文文件和当前检查结果；要改时先备份正文' },
    { title: '逐章审', detail: '每个文件一个审稿助手：审稿清单、要点卡、用户口径' },
    { title: '修改', detail: '按审稿意见改，改完该文件机器检查为 0' },
    { title: '跨章', detail: '名称、数字、第9～11章与前文一致' },
    { title: '复查', detail: '全文机器检查为 0' },
    { title: '出稿', detail: '要出稿时：WPS 两轮导出、发布，写交稿说明' },
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

// args：{ 项目: 项目文件夹完整路径, 修改: true|false（默认 true）, 章: '05,07'（不写=全文）, 重点: '这次特别看什么', 事实: 事实来源路径（可不写）,
//         出稿: true|false（默认 false）, 新报告: true|false（新报告流水线调用时为 true，交稿说明新写）, 工厂 }
const A = args || {}
const F = A.工厂 || '/Users/a1234/Desktop/评价报告流水线'
const 项目 = A.项目
if (!项目) throw new Error('缺 args.项目（项目文件夹的完整路径）')
const 改 = A.修改 !== false
const 只看 = (A.章 || '').split(/[,，、\s]+/).filter(Boolean)
const R = `python3 ${F}/scripts/report.py`
const S = `${F}/标准`

const 通则 = `你是“职业病危害预评价报告”流水线里的子助手。
项目文件夹：${项目}（正文在 正文/，资料、事实库在 资料/）。
标准：${S}/用户口径.md（用户定过的写法，最高优先）、${S}/审稿清单.md、${S}/各章要点/（每章一张卡）。
规矩：大文件不整篇读，先 grep 再按行号读；只动指定给你的文件；不出 PDF，不给别的对话发消息；中文。
机器检查：${R} check ${项目} --json --章 文件名前缀（只看这几个文件的结果）。
`
const 卡 = (f, 方法) => f.startsWith('03') ? `${S}/各章要点/03_${方法}.md` : `${S}/各章要点/${f.slice(0, 2)}.md`
const 前缀 = f => f.replace(/\.md$/, '')

const 准备结果 = {
  type: 'object',
  properties: {
    方法: { type: 'string', enum: ['类比法', '风险评估法'] },
    文件: { type: 'array', items: { type: 'object', properties: { 文件: { type: 'string' }, 字数: { type: 'integer' } }, required: ['文件', '字数'] } },
    错误: { type: 'integer' }, 提示: { type: 'integer' }, 待办: { type: 'integer' },
    备份: { type: 'string' },
    事实来源: { type: 'string' },
  },
  required: ['方法', '文件', '错误', '提示', '待办', '事实来源'],
}
const 审结果 = {
  type: 'object',
  properties: {
    必改: { type: 'integer' }, 建议: { type: 'integer' },
    意见: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          行: { type: 'integer' }, 原文: { type: 'string' }, 问题: { type: 'string' },
          依据: { type: 'string' }, 改法: { type: 'string' }, 级别: { type: 'string', enum: ['必改', '建议'] },
        },
        required: ['原文', '问题', '依据', '改法', '级别'],
      },
    },
  },
  required: ['必改', '建议', '意见'],
}
const 修结果 = {
  type: 'object',
  properties: { 改: { type: 'integer' }, 跳过: { type: 'array', items: { type: 'string' } }, 剩余错误: { type: 'integer' } },
  required: ['改', '剩余错误'],
}
const 跨结果 = {
  type: 'object',
  properties: { 改: { type: 'array', items: { type: 'string' } }, 未改: { type: 'array', items: { type: 'string' } } },
  required: ['改'],
}
const 检结果 = {
  type: 'object',
  properties: {
    错误: { type: 'integer' }, 提示: { type: 'integer' }, 待办: { type: 'integer' },
    按文件: { type: 'array', items: { type: 'object', properties: { 文件: { type: 'string' }, 错误: { type: 'array', items: { type: 'string' } } }, required: ['文件', '错误'] } },
  },
  required: ['错误', '提示', '待办', '按文件'],
}

phase('准备')
const p = await 跑(通则 + `任务：审稿前准备。
1. 运行 ${R} check ${项目} --json，记下“正文文件”列表（文件名、字数）和错误、提示、待办条数。
2. 看 正文/03_*.md 的一级标题：“职业健康风险评估分析与评价”是风险评估法，“类比企业（项目）调查与分析”是类比法。
3. ${改 ? '在项目文件夹里执行 cp -R 正文 "备份/正文_$(date +%Y%m%d_%H%M)_审稿前"（没有 备份/ 先建），返回备份路径。' : '只审不改，不用备份，备份写空。'}
4. 事实来源：${A.事实 ? '用 ' + A.事实 + '。' : '资料/事实库.md 存在就写它；否则在项目文件夹和上两级找可研的文字版（资料/文本/、提取文本/ 下的 txt），写出路径；都没有写“无”。'}
返回：方法、全部正文文件、三项条数、备份路径、事实来源。`, { label: '准备', phase: '准备', schema: 准备结果, effort: 'low' })
if (!p) throw new Error('准备没做成，停下')
const files = p.文件.filter(f => !只看.length || 只看.some(x => f.文件.startsWith(x)))
log(`${p.方法}，审 ${files.length} 个文件；审前检查：错误 ${p.错误}、提示 ${p.提示}、待办 ${p.待办}`)

const 审任务 = f => `任务：审 正文/${f.文件}（${f.字数} 字）。不改正文。
先读 ${S}/审稿清单.md、${卡(f.文件, p.方法)}、${S}/用户口径.md。事实核对用 ${p.事实来源}（grep 关键词，不整篇读）；名称以 资料/岗位因素表.md（有的话）或第1章表1.8-1、第4章因素识别表为准（grep 表题后只读那张表）。
然后按行号分段读本文件（每次约 300 行），逐段对照审稿清单各维度和要点卡的红线、常见错误。${A.重点 ? '这次特别看：' + A.重点 : ''}
只报有依据的问题：行号、原文（照抄，不超过 40 字）、问题、依据（审稿清单 R 号、要点卡红线、K 号或事实出处）、改法（具体到改成什么）、级别（必改／建议）。标点、编号、计数句这类机器检查能查的不用报。没问题就返回空列表，不凑数。
把意见写成表格存到 ${项目}/审查/审稿记录/${f.文件}（覆盖旧的；没有文件夹先建）。`

const 修任务 = (f, r) => `任务：按审稿意见改 正文/${f.文件}。
意见（行号可能已偏移，按原文找）：
${JSON.stringify(r.意见, null, 1)}
先读 ${S}/用户口径.md 和 ${卡(f.文件, p.方法)}。每条先找到原文、确认问题成立再改；不成立的跳过，记一句理由。“建议”级的确实更好才改。事实只取 ${p.事实来源}，不编。只改意见涉及的地方。
改完运行 ${R} check ${项目} --json --章 ${前缀(f.文件)}，把这个文件的错误改到 0。
返回：改了几条、跳过的（各一句理由）、剩余错误数。`

const results = await pipeline(files,
  f => 跑(通则 + 审任务(f), { label: '审 ' + f.文件, phase: '逐章审', schema: 审结果 }),
  (r, f) => (!改 || !r || !r.意见.length) ? { 文件: f.文件, 审: r, 修: null }
    : 跑(通则 + 修任务(f, r), { label: '改 ' + f.文件, phase: '修改', schema: 修结果, effort: 'medium' })
      .then(m => ({ 文件: f.文件, 审: r, 修: m })),
)

let 跨 = null
if (!只看.length) {
  phase('跨章')
  跨 = await 跑(通则 + `任务：跨章一致性${改 ? '检查，发现就直接改' : '检查，只记录，不改正文'}。
用 grep 抽各章的名称和数字来比，不整章读：
1. 评价单元、工作场所、工种、职业病危害因素名称：以 资料/岗位因素表.md（有的话）、第1章表1.8-1、第4章因素识别表为准，查第2～11章写法。
2. 定员、班制、接触时间、设备台数、限值、风险等级等在多章出现的数字。
3. 第9章关键控制点、第10章问题和建议、第11章结论，与第3～8章的判定是否一致（不多、不少、不矛盾）。
4. “见10.x”“见表x.x-x”这类引用是否指向存在的内容；第1章1.2 评价依据与正文引用的标准是否对得上。
以事实和前面章节为准改后面的。${改 ? '改完运行 ' + R + ' check ' + 项目 + ' --json，错误改到 0。' : ''}
记录写到 ${项目}/审查/审稿记录/跨章一致.md。
返回：改了的（每处一句）、没改的（原因）。`, { label: '跨章一致', phase: '跨章', schema: 跨结果 })
}

const 查 = label => 跑(`运行 ${R} check ${项目} --json，照输出返回：错误、提示、待办条数；“按文件”里有错误的文件，每个列出它的错误（照抄）。只运行这一条命令，不改任何文件。`,
  { label, phase: '复查', schema: 检结果, effort: 'low' })

async function 清零() {
  for (let round = 1; round <= 3; round++) {
    const c = await 查('检查 第' + round + '轮')
    if (!c || c.错误 === 0) return c
    log(`第${round}轮检查：错误 ${c.错误} 条，分 ${c.按文件.length} 个文件改`)
    await parallel(c.按文件.map(x => () => 跑(通则 + `任务：把下面这些机器检查错误改掉（位置：${x.文件}）。
${x.错误.map(e => '- ' + e).join('\n')}
位置写“正文”“检查规则.md”“项目信息.md”的，先 grep 找到出处再改。“项目规则要求出现……正文里没有”的：先查正文是不是已按新口径换了说法，是就从项目 检查规则.md 的“必备”删掉这项并注明原因，不是就在正文补上。“通用规则禁用”的：照 ${S}/用户口径.md 对应条目改写，不要换个近义词躲检查。
改完运行 ${R} check ${项目} --json --章 ${前缀(x.文件)}，确认为 0。返回改了几处。`,
      { label: '清零 ' + x.文件, phase: '复查', effort: 'medium' })))
  }
  return 查('检查 最终')
}

phase('复查')
const last = 改 ? await 清零() : { 错误: p.错误, 提示: p.提示, 待办: p.待办 }

const ok = results.filter(x => x && x.审)
const sum = k => ok.reduce((n, x) => n + (x.审[k] || 0), 0)
const 已改 = ok.reduce((n, x) => n + ((x.修 && x.修.改) || 0), 0)

let 出 = null
if (A.出稿 && 改 && last && last.错误 === 0) {
  phase('出稿')
  出 = await 跑(`任务：给 ${项目} 出稿并写交稿说明。
1. 运行 ${R} 出稿 ${项目}（Bash 超时设 600000 毫秒；它会排队等 WPS），记下页数、目录条数、是否“核对通过”。
2. ${A.新报告 ? `新写 ${项目}/输出/交稿说明.md（已有就先改名加 _旧）。写给用户看，中文短句：报告编号和页数；资料依据（资料/文本/索引.json 里的来源文件）；评价方法；检查结果（错误 0、提示 ${last.提示} 条）；审稿：意见 ${sum('必改') + sum('建议')} 条、改了 ${已改} 条；资料/岗位因素表.md 里“按不利情况定的”各项；资料/口径.md 里“可研没有写的”中需要建设单位补的少数几项；项目信息.md 里还是【待填】的项（评价人员是照样板抄的，请用户确认）。报告里不写的这些，都只写在交稿说明里。`
    : `在 ${项目}/输出/交稿说明.md 末尾追加一节“## 本轮审稿”（文件没有就新建）：审稿意见 ${sum('必改') + sum('建议')} 条、改了 ${已改} 条，检查结果，PDF 页数。中文短句。`}
返回：是否通过、页数、问题（没有写空）。`, {
    label: '出稿', phase: '出稿', effort: 'low',
    schema: { type: 'object', properties: { 通过: { type: 'boolean' }, 页数: { type: 'integer' }, 问题: { type: 'string' } }, required: ['通过'] },
  })
}

return {
  项目, 方法: p.方法, 审了: ok.length, 必改: sum('必改'), 建议: sum('建议'),
  已改, 出稿: 出,
  跳过: ok.flatMap(x => (x.修 && x.修.跳过) || []).slice(0, 8),
  跨章: 跨 ? { 改: 跨.改.length, 未改: (跨.未改 || []).slice(0, 8) } : null,
  检查: last ? { 错误: last.错误, 提示: last.提示, 待办: last.待办 } : null,
  没审成: files.filter((f, i) => !results[i] || !results[i].审).map(f => f.文件),
  记录: 项目 + '/审查/审稿记录/', 备份: p.备份 || '',
}
