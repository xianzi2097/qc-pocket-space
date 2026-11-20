// Scratch 积木文本处理 —— 供两处共用：
//   1. y/.vitepress/config.mts：渲染每个 ```scratch 围栏时即时校验
//   2. scripts/lint-scratch.mjs：一条命令扫描全站所有 md
//
// 纯官方设计（2026-11-18 移除 Python 风格转换层）：
//   原文直送 scratchblocks 官方解析器，官方认什么就渲染什么、报什么。
//   校验层 checkScratchCode()：parse 原文，找出未识别的积木。
//
// 关键：parse() 是纯文本解析，不需要 DOM，所以 Node 里直接跑。
import { parse, loadLanguages } from 'scratchblocks/syntax/index.js'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// 从本文件位置向上定位 node_modules（兼容被 Vite 打包到临时文件的情况）
function loadZhCn() {
  let dir = dirname(fileURLToPath(import.meta.url))
  for (let i = 0; i < 8; i++) {
    try {
      return JSON.parse(
        readFileSync(join(dir, 'node_modules', 'scratchblocks', 'locales', 'zh-cn.json'), 'utf8'),
      )
    } catch {
      const parent = dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  }
  throw new Error('无法定位 scratchblocks/locales/zh-cn.json')
}

loadLanguages({ 'zh-cn': loadZhCn() })


// ============================================================
// 第二层：校验（在转换后的文本上找未识别积木）
// ============================================================

/** 收集文档里所有未识别（obsolete/红色）积木的原文 */
function findUnrecognized(doc) {
  const bad = []
  const walk = (x) => {
    if (!x) return
    if (x.isScript) x.blocks.forEach(walk)
    else if (x.isBlock) {
      if (x.info && x.info.category === 'obsolete') {
        const text = (x.children || [])
          .filter((c) => c.isLabel)
          .map((c) => c.value)
          .join('')
        bad.push(text || '(空积木)')
      }
      x.children.forEach(walk)
    } else if (x.isGlow) walk(x.child)
  }
  doc.scripts.forEach(walk)
  return bad
}

/**
 * 校验一段 scratch 文本（纯官方：原文直送解析器，无任何预处理转换）。
 * 2026-11-18 删除转换层 convertPythonIndent —— 官方 parse 认什么就是什么。
 * @returns {{ ok: boolean, errors: string[], warnings: string[], converted: string }}
 */
export function checkScratchCode(raw) {
  const converted = raw
  const warnings = []
  try {
    const doc = parse(converted, { languages: ['zh-cn', 'en'] })
    const errors = findUnrecognized(doc)
    return { ok: errors.length === 0, errors, warnings, converted }
  } catch (e) {
    return { ok: false, errors: ['解析失败: ' + e.message], warnings, converted }
  }
}
