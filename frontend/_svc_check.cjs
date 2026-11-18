const fs = require('fs')
const path = require('path')
const sfc = require('@vue/compiler-sfc')

const root = 'C:/Users/43120/Desktop/foodai/frontend/src'
const files = [
  'views/LoginView.vue',
  'views/ProfileView.vue',
  'layouts/MainLayout.vue',
  'views/WorkoutView.vue',
  'views/RunTrackView.vue',
  'views/WorkoutDetailView.vue',
  'components/FaceCaptureDialog.vue',
  'components/TrackMap.vue',
  'views/mobile/MobileSportView.vue',
]

let bad = 0
for (const f of files) {
  const p = path.join(root, f)
  if (!fs.existsSync(p)) { console.log('MISSING FILE', f); bad++; continue }
  const src = fs.readFileSync(p, 'utf8')
  const { descriptor, errors } = sfc.parse(src, { filename: f })
  if (errors.length) { console.log('PARSE ERR', f, errors.map(e => e.message).join('; ')); bad++; continue }
  try {
    if (descriptor.script || descriptor.scriptSetup) {
      sfc.compileScript(descriptor, { id: f, inlineTemplate: false })
    }
    if (descriptor.template) {
      const r = sfc.compileTemplate({
        source: descriptor.template.content,
        filename: f,
        id: f,
        compilerOptions: { isCustomElement: () => false },
      })
      if (r.errors && r.errors.length) { console.log('TPL ERR', f, r.errors.map(e => e.message || e).join('; ')); bad++; continue }
    }
    console.log('OK  ', f)
  } catch (e) {
    console.log('COMPILE ERR', f, e.message)
    bad++
  }
}
console.log('失败数:', bad)
