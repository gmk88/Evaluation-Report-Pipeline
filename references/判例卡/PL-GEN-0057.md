---
判例号: "PL-GEN-0057"
报告类型: "通用"
判例类型: "工作流"
涉及章节: "出稿：Word 转 PDF、目录页码回填和复核（build、finalize）"
触发场景: "用户电脑上的项目规则禁止用 LibreOffice 出 PDF，必须用 Microsoft Word。Word 未激活，AppleScript `save as … file format format PDF` 报 -1708。改走打印对话框“存储为PDF”后又遇到三处坑：①回填目录页码后用 AppleScript `close document … saving no` 关文档没生效，Word 打印的仍是内存里的旧稿，目录页码全是0；②197页的稿子打开后 Word 还在分页，打印对话框的“PDF”按钮迟迟不出现，脚本取不到按钮报错；③存储面板默认存到上次用过的文件夹，不一定是 docx 所在文件夹。"
错误做法: "①没核实 Word 能否“另存为 PDF”就写死该命令；②回填 docx 后不关旧窗口就打印；③用固定等待时间找打印对话框的按钮；④按脚本返回值判断 PDF 是否生成；⑤关窗时替用户选“不存储”。"
正确做法: "1. `build 项目 --不出PDF` 只出 Word。\n2. 同名 docx 的旧窗口先关：`osascript scripts/Word关闭窗口.applescript 窗口名关键词`，脚本核对最前面的窗口是目标才按 Cmd+W，出现存储提示不替用户选。别的文档窗口不动。\n3. `open -a \"Microsoft Word\" 文件.docx`，等加载完（约25秒）。\n4. `osascript scripts/Word打印PDF.applescript 窗口名关键词`：打印对话框已开就接着用，“PDF”按钮最多等60秒。\n5. 看 PDF 是否生成、大小是否稳定，不看脚本返回值；找到后移到 `输出/`，与 docx 同名。\n6. `finalize 项目` 回填目录页码和“共N页”；回填后 docx 变了，重复第2～5步再导出一次。\n7. `finalize 项目 --核对`：目录和页脚逐页核对，错误为0才交付；PDF 比 docx 旧时程序会报错。"
依据: "晋钢200MW预评价出稿（2026-10-25～26）：Word 未激活 -1708；关窗失败打印旧缓存（目录页码为0）；197页稿打印对话框按钮延迟；PDF 存到上次文件夹"
可复用规则: "用 Word 出 PDF：先关同名 docx 的旧窗口再重开，走打印对话框“存储为PDF”；按 PDF 文件是否生成判断成败；回填目录后再关、再开、再导出一次，最后 finalize 核对。"
反例或例外: "电脑上允许用 LibreOffice 时，直接 `build`（自动转 PDF、回填、复核），不必走本流程。"
来源项目: "晋钢200MW超临界煤气发电预评价"
发生日期: "2026-10-26"
已蒸馏到: ["references/判定规则与口径.md 第52条", "SKILL.md 第0节、第3节“用 Word 出 PDF”、第9节", "scripts/Word打印PDF.applescript", "scripts/Word关闭窗口.applescript", "scripts/report.py finalize"]
tags: ["判例卡", "通用", "工作流", "PDF", "Microsoft Word", "目录回填", "AppleScript"]
---

# 用 Word 出 PDF：先关旧窗口再重开，按文件判断成败

> 一条判例只讲一件事。写不成一句话的，拆成两条。

## 触发场景

项目规则禁止用 LibreOffice。Word 未激活不能“另存为 PDF”，改走打印对话框。回填目录后没关掉旧窗口，打印出的目录页码全是0；长稿打印对话框按钮出现慢，脚本报错。

## 错误做法

- 写死 Word“另存为 PDF”命令。
- 回填 docx 后不关旧窗口就打印。
- 固定等待时间找按钮；按脚本返回值判断成败；关窗时替用户选“不存储”。

## 正确做法

```bash
python3 scripts/report.py build 某项目 --不出PDF
osascript scripts/Word关闭窗口.applescript "报告编号"     # 关同名旧窗口（没开就跳过）
open -a "Microsoft Word" 某项目/输出/某报告.docx           # 等约25秒
osascript scripts/Word打印PDF.applescript "报告编号"     # 存储为PDF
# 找到生成的 PDF（存在上次用过的文件夹），移到 某项目/输出/，与 docx 同名
python3 scripts/report.py finalize 某项目                # 回填目录页码和“共N页”
# 再关窗口、重开、导出一次，然后：
python3 scripts/report.py finalize 某项目 --核对         # 错误为0才交付
```

## 可复用规则

用 Word 出 PDF：先关同名 docx 的旧窗口再重开，走打印对话框“存储为PDF”；按 PDF 文件是否生成判断成败；回填目录后再关、再开、再导出一次，最后 finalize 核对。

## 补充（2026-10-30）

晋钢项目从 2026-10-30 起改用 WPS 命令行出 PDF（用户定，见 [[PL-GEN-0059]]、`scripts/report.py wps`）：不占用 Word，几个窗口不再互相覆盖。本卡的 Word 做法留给没有 WPS 的电脑用。

## 关联

- [[PL-GEN-0045]]（中间成果随做随存）
