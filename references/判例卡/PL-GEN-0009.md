---
判例号: "PL-GEN-0009"
报告类型: "通用"
判例类型: "工作流"
涉及章节: "渲染交付"
触发场景: "用 LibreOffice headless 渲染 PDF 时"
错误做法: "渲染日志出现 `FrankRuhlHofshi` 或 `LinuxLibertine` 替代字体仍然交付——说明仿宋/宋体没有被正确映射。"
正确做法: "检查最终 PDF 的实际字体，必须含 `FangS-SC`、`SimSun`、`TimesNewRomanPSMT`。马沟渲染器 `render_review.py` 显式加载 `_work/fontconfig_qa.conf`，在独立 LibreOffice profile 中映射仿宋到 FangS-SC、宋体到 SimSun，检查字体后再导出。"
依据: "交接文件 §十一字体要求；最终 PDF 字体实测记录"
可复用规则: "渲染后必须核验最终 PDF 内嵌字体；出现非预期替代字体即视为渲染失败，重新渲染后才能交付。"
反例或例外: "无。"
来源项目: "马沟加气站预评价"
发生日期: "2026-10-02"
已蒸馏到: []
tags: ["判例卡", "通用", "工作流", "字体渲染"]
---

# 字体替换失败不得交付

## 触发场景

中文公文字体在 Linux/LibreOffice 下经常缺字，渲染器会静默替换成别的字体。页面看起来还挺正常，只是字形不对——而这类差异在屏幕上不盯着看根本发现不了。

## 做法

**错误做法**：渲染日志出现 `FrankRuhlHofshi` 或 `LinuxLibertine` 替代字体仍然交付——说明仿宋/宋体没有被正确映射。

**正确做法**：检查最终 PDF 的实际字体，必须含 `FangS-SC`、`SimSun`、`TimesNewRomanPSMT`。马沟渲染器 `render_review.py` 显式加载 `_work/fontconfig_qa.conf`，在独立 LibreOffice profile 中映射仿宋到 FangS-SC、宋体到 SimSun，检查字体后再导出。

## 依据

- 项目：交接文件 §十一「出现 FrankRuhlHofshi 或 LinuxLibertine 错误替代字体不能交付」（F3）
- 项目：交接文件 §九「最终 PDF 实际字体含 FangS-SC、SimSun 和 TimesNewRomanPSMT」（F3）
- 脚本：`_work/pre_eval_red_review_20260930/render_review.py`、`_work/fontconfig_qa.conf`

## 可复用规则

> 渲染后必须核验最终 PDF 内嵌字体；出现非预期替代字体即视为渲染失败，重新渲染后才能交付。

## 反例或例外

无。

## 关联

- 同类判例：[[PL-GEN-0004]]（字号 name 与 pt 核对）
- 同类判例：[[PL-GEN-0005]]（域缓存不能交付）
