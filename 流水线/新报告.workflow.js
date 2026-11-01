export const meta = {
  name: '新报告',
  description: '从可研等资料写出一份完整的职业病危害预评价报告：事实库 → 岗位因素表 → 各章按依赖并行写 → 检查清零 → 审稿 → 出稿',
  whenToUse: '用户给了新项目的资料，要写新报告',
  phases: [
    { title: '准备', detail: '建项目、资料转文字、填项目信息、登记' },
    { title: '事实', detail: '分批摘事实，汇总成事实库和口径表' },
    { title: '骨架', detail: '定评价单元、工作场所、工种和岗位因素表' },
    { title: '写作', detail: '各章按依赖关系并行写，每章写完检查为 0' },
    { title: '检查', detail: '全文机器检查清零' },
    { title: '审稿', detail: '调用审稿流水线：逐章审改、跨章一致、出稿' },
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

// args：{ 项目: 新项目文件夹完整路径, 名称: 项目名称, 方法: '风险评估法'|'类比法', 资料: [资料文件或文件夹路径]（不写=项目的 资料/）, 出稿: true|false（默认 true）, 工厂 }
const A = args || {}
const F = A.工厂 || '/Users/a1234/Desktop/评价报告流水线'
const 项目 = A.项目
const 方法 = A.方法
if (!项目 || !['类比法', '风险评估法'].includes(方法)) throw new Error('缺 args.项目，或 args.方法 不是 类比法／风险评估法')
const R = `python3 ${F}/scripts/report.py`
const S = `${F}/标准`
const D = `${项目}/资料`
const 章 = f => f.slice(0, 2)
const 卡 = f => 章(f) === '03' ? `${S}/各章要点/03_${方法}.md` : `${S}/各章要点/${章(f)}.md`

const 通则 = `你是“职业病危害预评价报告”流水线里的子助手，在写一份新报告（${方法}）。
项目文件夹：${项目}（正文在 正文/，资料、事实库在 资料/）。
标准：${S}/用户口径.md（用户定过的写法，最高优先）、${S}/各章要点/（每章一张卡）、${F}/references/正文写法格式.md（表格、占位符、横向页怎么写）。
规矩：大文件不整篇读，先 grep 再按行号读；只动指定给你的文件；不出 PDF，不给别的对话发消息；中文。
机器检查：${R} check ${项目} --json --章 文件名前缀。
`

const 准备结果 = {
  type: 'object',
  properties: {
    块: { type: 'array', items: { type: 'object', properties: { 文件: { type: 'string' }, 字数: { type: 'integer' }, 标题: { type: 'array', items: { type: 'string' } } }, required: ['文件', '字数'] } },
    样板: { type: 'string' },
    待填: { type: 'array', items: { type: 'string' } },
    正文文件: { type: 'array', items: { type: 'string' } },
    没转成: { type: 'array', items: { type: 'string' } },
  },
  required: ['块', '样板', '正文文件'],
}
const 批结果 = { type: 'object', properties: { 条数: { type: 'integer' }, 标题: { type: 'array', items: { type: 'string' } } }, required: ['条数'] }
const 汇结果 = { type: 'object', properties: { 事实: { type: 'integer' }, 不一: { type: 'integer' }, 没写: { type: 'integer' } }, required: ['事实', '不一', '没写'] }
const 骨结果 = {
  type: 'object',
  properties: { 单元: { type: 'integer' }, 场所: { type: 'integer' }, 工种: { type: 'integer' }, 因素: { type: 'integer' }, 不利情况: { type: 'array', items: { type: 'string' } } },
  required: ['单元', '场所', '工种', '因素'],
}
const 写结果 = {
  type: 'object',
  properties: { 字数: { type: 'integer' }, 表数: { type: 'integer' }, 剩余错误: { type: 'integer' }, 可研没有: { type: 'array', items: { type: 'string' } } },
  required: ['字数', '剩余错误'],
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
const p = await 跑(通则 + `任务：建项目并准备资料。
1. ${项目}/项目信息.md 不存在时运行 ${R} new ${项目} --方法 ${方法} --名称 "${A.名称 || ''}"；已存在就不重建（接着上次做）。
2. 运行 ${R} 资料 ${项目} ${(A.资料 || []).map(x => '"' + x + '"').join(' ')}，记下块数和没转成的文件。
3. 填 项目信息.md：项目名称、建设单位等从资料里找（委托书、合同、备案证、可研封面；grep 资料/文本/ 下的块）。评价单位、法定代表人、评价人员照 ${F}/项目登记.md 里方法同为“${方法}”、样板为“是”的项目的 项目信息.md 抄。报告编号、报告日期资料里没有的留【待填】。
4. 在 ${F}/项目登记.md 的表格末尾加一行：| 简称 | ${方法} | ${项目} | |（已有这个文件夹就不加）。
5. 运行 ${R} check ${项目} --json，取“正文文件”的文件名列表。
返回：资料块列表（读 资料/文本/索引.json：文件、字数、标题前 3 个）、样板项目文件夹、项目信息里还空着的项、正文文件名列表、没转成的资料。`, { label: '准备', phase: '准备', schema: 准备结果, effort: 'medium' })
if (!p || !p.块.length) throw new Error('资料没转出文字，停下：' + JSON.stringify(p && p.没转成))
log(`资料 ${p.块.length} 块；样板 ${p.样板}`)

phase('事实')
const 事实标题 = ['项目概况（名称、性质、规模、建设地点、进度）', '选址与周边（周边企业和设施）', '总平面布置', '产品、原辅材料及用量',
  '生产工艺', '主要设备（与危害有关的：数量、密闭、噪声、温度）', '建筑卫生学（采暖、通风、空调、采光照明）', '辅助用室',
  '劳动定员与班制（各岗位人数、班制、作业方式）', '职业病防护设施（防尘、防毒、防噪、防暑等）', '个人使用的职业病防护用品',
  '应急救援（报警、喷淋洗眼、急救用品、救援依托）', '职业卫生管理（机构、制度、经费）', '检修、化验等非正常作业']
const 批 = []
let cur = [], n = 0
for (const b of p.块) {
  if (n + b.字数 > 50000 && cur.length) { 批.push(cur); cur = []; n = 0 }
  cur.push(b); n += b.字数
}
if (cur.length) 批.push(cur)
const 摘 = await parallel(批.map((bs, i) => () => 跑(通则 + `任务：从这些资料块摘事实，写到 ${D}/事实片段/批${i + 1}.md（没有文件夹先建，已有就覆盖）。
资料块（在 ${D}/文本/）：${bs.map(b => b.文件).join('、')}
按这些标题分节写（本批没有的标题不写）：
${事实标题.map(t => '## ' + t).join('\n')}
每条一行：“- 事实（出处：块文件名 节号或节标题）”。数字、名称照原文，不改写、不推算。
只摘会影响危害因素、接触人员、接触时间、防护设施判定的。投资收益、电气、土建结构、环保排放指标、消防、安全生产内容不摘。
同一事实前后说法不一的，两种都摘，各注出处。
返回：摘了多少条、涉及哪些标题。`, { label: `摘事实 批${i + 1}`, phase: '事实', schema: 批结果, effort: 'medium' })))
if (摘.some(x => !x)) log('有批次没摘成：' + 摘.map((x, i) => x ? '' : '批' + (i + 1)).filter(Boolean).join('、'))
const 汇 = await 跑(通则 + `任务：把 ${D}/事实片段/ 下各批的事实汇总成两个文件。
1. ${D}/事实库.md：按同样的标题分节，去重合并，保留出处。这是全报告事实的唯一来源，写作助手只看它，不要漏条。
2. ${D}/口径.md：
   “## 说法不一”：表格｜项目｜说法1（出处）｜说法2（出处）｜采用｜理由｜。更具体、更新的资料优先（初设优于可研，设备表、布置图优于概述文字）；不影响危害识别的矛盾只写一次，按可研原文写。
   “## 可研没有写的”：影响危害识别和评价、资料里确实没有的事项，每项一行，写影响到哪一章。写作时按“可研没有写……”写进评价并提补充措施。
返回：事实库条数、说法不一条数、可研没有写的条数。`, { label: '汇总事实库', phase: '事实', schema: 汇结果 })
if (!汇) throw new Error('事实库没汇总成，停下')
log(`事实库 ${汇.事实} 条，说法不一 ${汇.不一} 条，可研没有写 ${汇.没写} 条`)

phase('骨架')
const 骨 = await 跑(通则 + `任务：定评价单元、工作场所、工种，写 ${D}/岗位因素表.md。这张表是全报告名称和因素的唯一来源，第1、3～10章都照它。
先读 ${D}/事实库.md、${D}/口径.md、${S}/各章要点/01.md（1.8 评价单元）、${S}/各章要点/04.md（因素识别）、${S}/用户口径.md；看样板 ${p.样板}/正文/ 的表1.8-1 和第4章因素识别表怎么写（grep 表题，只读那两张表）。
表头：｜评价单元｜工作场所｜工种｜定员及班制｜工作地点｜接触时间（h/d）｜职业病危害因素｜检测方式｜出处｜
- 评价单元按工艺系统和辅助系统分；工作场所按有人作业、巡检的区域或设施分，操作室、值班室是所在场所的工作地点，不单列。
- 因素名照《职业病危害因素分类目录》写法；只写职业病危害因素。
- 资料没写的班制、定员、接触时间，按可研和不利情况由你定，出处栏写“按不利情况”，表后加一节“## 按不利情况定的”逐项说明。
返回：评价单元数、工作场所数、工种数、因素种数、按不利情况定的事项（每项一句）。`, { label: '岗位因素表', phase: '骨架', schema: 骨结果 })
if (!骨) throw new Error('岗位因素表没做成，停下')
log(`评价单元 ${骨.单元} 个、工作场所 ${骨.场所} 个、工种 ${骨.工种} 个、因素 ${骨.因素} 种`)

phase('写作')
const 依赖 = {
  '02': [], '03': ['02'], '04': ['02', '03'], '08': ['02'],
  '05': ['02', '04'], '06': ['02', '04'], '07': ['02', '04'],
  '09': ['03', '04', '05', '06', '07'], '10': ['03', '04', '05', '06', '07', '08', '09'],
  '11': ['03', '04', '05', '06', '07', '08', '09', '10'], '01': ['03', '04', '05', '06', '07', '08', '09', '10'],
}
const files = p.正文文件
const 按章 = {}
files.forEach(f => (按章[章(f)] = 按章[章(f)] || []).push(f))
const 写任务 = (f, 前文, gaps) => `任务：写 正文/${f}${/^\d\d_\d/.test(f) ? '（第' + Number(章(f)) + '章的一部分，只写本文件骨架里的节）' : ''}。
按顺序读：
1. ${卡(f)}（本章要点卡）、${S}/用户口径.md。
2. 正文/${f} 现有骨架：标题结构照留；注释里的写法提示照做，写完删掉提示注释和【待填】。
3. 事实只取 ${D}/事实库.md、${D}/口径.md；评价单元、工作场所、工种、因素名称一律照 ${D}/岗位因素表.md。按需 grep，不整篇读。
4. 样板 ${p.样板}/正文/ 里同一章：grep 标题、表题和表头，读一两节看写法深浅。只学结构和写法，不抄任何事实。
${前文.length ? '5. 已写好的前面各章（承接表号、名称、判定和数字；grep 后只读需要的表）：' + 前文.join('、') : ''}
${gaps.length ? '前面各章记下的“可研没有写”的事项，10.1 要逐项承接：\n' + gaps.map(x => '- ' + x).join('\n') : ''}
写法：
- 可研没写的，写“可研没有写／没有明确／没有给出……”并提补充措施；不写【待核】【待填】、待确认、资料未提供。
- 不写消防、安全生产、防寒防冻、放射性内容。写给评审专家和建设单位看：不写编者口吻、方法取舍理由，正文不点名条号。
- 计数句用占位符（如 {{计数:表5.3-1}}），写法见 正文写法格式.md。
写完运行 ${R} check ${项目} --json --章 ${f.replace(/\.md$/, '')}，本文件错误和待办都改到 0。
返回：字数、表数、剩余错误、本章记下的“可研没有写”事项（每项一句，没有写空列表）。`
const memo = {}
const 写果 = {}
function 写(f) {
  if (memo[f]) return memo[f]
  const deps = (依赖[章(f)] || []).flatMap(c => 按章[c] || [])
  memo[f] = Promise.all(deps.map(写)).then(() => {
    const gaps = 章(f) === '10' ? Object.keys(写果).filter(k => 章(k) < '10' && 章(k) !== '01').flatMap(k => (写果[k] && 写果[k].可研没有) || []) : []
    return 跑(通则 + 写任务(f, deps, gaps), { label: '写 ' + f, phase: '写作', schema: 写结果 }).catch(() => null)
  }).then(r => { 写果[f] = r; return r })
  return memo[f]
}
await Promise.all(files.map(写))
const 没写成 = files.filter(f => !写果[f])
log(`写成 ${files.length - 没写成.length}/${files.length} 个文件` + (没写成.length ? '；没写成：' + 没写成.join('、') : ''))

phase('检查')
const 查 = label => 跑(`运行 ${R} check ${项目} --json，照输出返回：错误、提示、待办条数；“按文件”里有错误的文件，每个列出它的错误（照抄）。只运行这一条命令，不改任何文件。`,
  { label, phase: '检查', schema: 检结果, effort: 'low' })
let 末检 = null
for (let round = 1; round <= 3; round++) {
  末检 = await 查('检查 第' + round + '轮')
  if (!末检 || 末检.错误 === 0) break
  await parallel(末检.按文件.map(x => () => 跑(通则 + `任务：把下面这些机器检查错误改掉（位置：${x.文件}）。
${x.错误.map(e => '- ' + e).join('\n')}
位置写“正文”“检查规则.md”“项目信息.md”的，先 grep 找到出处。“通用规则禁用”的照 ${S}/用户口径.md 对应条目改写，不换近义词躲检查。引用了不存在的表，查是表号写错还是那张表漏了，补齐或改对。
改完运行 ${R} check ${项目} --json --章 ${x.文件.replace(/\.md$/, '')}，确认为 0。返回改了几处。`,
    { label: '清零 ' + x.文件, phase: '检查', effort: 'medium' })))
}

phase('审稿')
let 审 = null
try {
  审 = await workflow({ scriptPath: F + '/流水线/审稿.workflow.js' },
    { 项目, 工厂: F, 修改: true, 出稿: A.出稿 !== false, 新报告: true, 事实: D + '/事实库.md' })
} catch (e) {
  log('审稿流水线没能在这里调用（' + String(e).slice(0, 80) + '）；主对话接着单独运行 审稿')
}

return {
  项目, 方法,
  资料: { 块: p.块.length, 没转成: p.没转成 || [] },
  事实库: 汇, 岗位因素表: 骨,
  写作: { 文件: files.length, 没写成 },
  检查: 末检 ? { 错误: 末检.错误, 提示: 末检.提示, 待办: 末检.待办 } : null,
  审稿: 审, 待审稿: !审,
  待填: p.待填 || [],
}
