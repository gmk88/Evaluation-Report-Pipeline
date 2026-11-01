---
判例号: "PL-CTL-0003"
报告类型: "控制效果评价"
判例类型: "排版图件"
涉及章节: "控评目录、版面；PDF 交付"
触发场景: "控评按 Word／WPS 排版，云端只有 LibreOffice；第二版新增第2.3节后 LibreOffice 渲染由 76 页增至 82 页，与 Word 分页不同"
错误做法: "用 LibreOffice 转出的 PDF 当交付件；在云端「更新整个目录」带出原稿未列入目录的三级标题；不提示用户更新页码"
正确做法: "①新增节写入目录，其余页码按 LibreOffice 新旧版页差估算写入（记 toc_pages.json），交付说明写明是估算；②请用户在 Word／WPS 右键目录→更新域→只更新页码；③不附 LibreOffice PDF，需要 PDF 时在 Word 更新后另存；④原稿未设字号的正文段落统一到正文字号，免得 Word 按默认样式显示为五号"
依据: "控评第一、二版定稿包 `README_包内说明.md`；`04_修改记录/edit_log.tsv`、`toc_pages.json`"
可复用规则: "按 Word 排版的报告在 LibreOffice 环境里改，目录页码只能估算；交付时请用户在 Word 只更新页码，不出 LibreOffice PDF。"
反例或例外: "由生成器出、以 LibreOffice 渲染为基线的报告（预评价、设计专篇）照常出 PDF 并做页码校验。"
来源项目: "马沟加气站控制效果评价"
发生日期: "2026-10-07"
已蒸馏到: []
tags: ["判例卡", "控制效果评价", "排版图件", "目录", "Word", "PDF"]
---

# Word 排版的报告在云端改：目录页码估算，请用户只更新页码，不出 LibreOffice PDF

> 一条判例只讲一件事。写不成一句话的，拆成两条。

## 触发场景

新增第2.3节后 LibreOffice 分页 76→82 页，与 Word 不同。

## 正确做法

- 目录写入新增节，其余页码估算并说明。
- 请用户在 Word 只更新页码。
- 不附 LibreOffice PDF。

## 可复用规则

按 Word 排版的报告在 LibreOffice 环境里改，目录页码只能估算；交付时请用户在 Word 只更新页码，不出 LibreOffice PDF。

## 关联

- 没有生成器的改法：[[PL-CTL-0001]]
