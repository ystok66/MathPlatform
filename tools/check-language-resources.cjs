const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const base = path.join(root, 'packages', 'i18n');
const registry = JSON.parse(fs.readFileSync(path.join(base, 'languages.json'), 'utf8'));
const problems = [];
function readMessages(language) {
  const result = {};
  for (const file of fs.readdirSync(path.join(base, 'locales', language)).filter(f => f.endsWith('.json'))) {
    const values = JSON.parse(fs.readFileSync(path.join(base, 'locales', language, file), 'utf8'));
    for (const [key, value] of Object.entries(values)) {
      if (key in result) problems.push(`duplicate:${language}:${key}`);
      if (typeof value !== 'string') problems.push(`not_string:${language}:${key}`);
      result[key] = value;
    }
  }
  return result;
}
const slots = text => [...String(text).matchAll(/\{([A-Za-z_][A-Za-z_0-9]*|[0-9]+)\}/g)].map(m => m[1]).sort().join(',');
const primary = readMessages(registry.default_language);
const promptNames = fs.readdirSync(path.join(root, 'prompts', registry.default_language)).filter(f => f.endsWith('.md'));
for (const {code} of registry.languages.filter(item => item.enabled !== false)) {
  const values = readMessages(code);
  for (const key of new Set([...Object.keys(primary), ...Object.keys(values)])) {
    if (!(key in primary) || !(key in values)) problems.push(`missing_key:${code}:${key}`);
    else if (slots(primary[key]) !== slots(values[key])) problems.push(`interpolation:${code}:${key}`);
  }
  for (const file of promptNames) {
    if (!fs.existsSync(path.join(root, 'prompts', code, file))) problems.push(`missing_prompt:${code}:${file}`);
  }
}
const result = {status: problems.length ? 'FAIL' : 'PASS', languages: registry.languages.map(x => x.code), messages: Object.keys(primary).length, prompt_versions: promptNames.length, problems};
process.stdout.write(JSON.stringify(result, null, 2) + '\n');
process.exitCode = problems.length ? 1 : 0;
