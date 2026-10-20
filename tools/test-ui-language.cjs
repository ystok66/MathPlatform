// Offline display-language regression checks. No provider requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'apps/web/node_modules/typescript'));
const modules = new Map();
function load(file) {
  file = path.resolve(file);
  if (modules.has(file)) return modules.get(file);
  const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports = {};
  modules.set(file, exports);
  vm.runInNewContext(output, { exports, require: name => load(path.resolve(path.dirname(file), name + '.ts')) }, { filename: file });
  return exports;
}
const base = path.join(root, 'apps/web/app/i18n');
const language = load(path.join(base, 'language.ts'));
const domain = load(path.join(base, 'domain-catalog.ts'));
const admin = load(path.join(base, 'admin-catalog.ts'));
const cjk = /[\u3400-\u9fff]/;
let checks = 0;
for (const text of ['AI生成题', '应用题', '数论', '数学正确性', '星级标定', '活跃态', '终态', '阻止练习', '默认学习者', '学习者=default-learner', '开始 5 道均衡练习']) {
  assert.ok(!cjk.test(language.translateUi(text, 'en')), 'Untranslated UI label: ' + text);
  assert.equal(language.translateUi(text, 'zh-CN'), text);
  checks++;
}
for (const [name, catalog] of [['domain', domain.domainEnglishCatalog], ['admin', admin.adminEnglishCatalog]]) {
  assert.ok(Object.keys(catalog).length > 30, name + ' catalog unexpectedly empty');
  for (const [source, translated] of Object.entries(catalog)) {
    assert.ok(translated && !cjk.test(translated), name + ' value contains Chinese: ' + source);
    assert.equal(language.translateUi(source, 'en'), translated);
    checks++;
  }
}
const question = '小明有 12 个苹果，送出 5 个，还剩多少个？';
const custom = '周末和妈妈复习：希望杯错题';
assert.equal(language.translateUi(question, 'en'), question);
assert.equal(domain.displayAutoTitle(custom, 'en'), custom);
assert.equal(domain.displayAutoTitle(custom, 'zh-CN'), custom);
checks += 3;
for (const text of ['平均数问题', '分数、小数与百分数', '不定方程与整数解', 'M:面积比']) {
  assert.ok(!cjk.test(language.translateUi(text, 'en')), 'Untranslated taxonomy label: ' + text);
  checks++;
}
assert.equal(language.translateUi('文件变化 89 / 额外文件 14', 'en'), 'Changed files: 89 / Extra files: 14');
checks++;
assert.ok(!cjk.test(language.translateUi('发布清单校验通过；升级或迁移前仍建议先执行 backup。', 'en')));
checks++;
console.log(`${checks} display-language checks passed; provider_calls=0`);
