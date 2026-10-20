const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const base=path.join(root,'packages/i18n');
const registry=JSON.parse(fs.readFileSync(path.join(base,'languages.json'),'utf8'));
const lines=['// Generated from packages/i18n; edit the JSON files, not this module.','import registry from "../../../../packages/i18n/languages.json";','import aliases from "../../../../packages/i18n/legacy-keys.json";','import slots from "../../../../packages/i18n/legacy-patterns.json";'];
const definitions=[];
for(const [index,lang] of registry.languages.filter(item=>item.enabled!==false).entries()) {
 const names=fs.readdirSync(path.join(base,'locales',lang.code)).filter(name=>name.endsWith('.json')).sort();
 const variables=[];
 for(const [part,name] of names.entries()) {const variable=`l${index}n${part}`;variables.push(variable);lines.push(`import ${variable} from ${JSON.stringify('../../../../packages/i18n/locales/'+lang.code+'/'+name)};`);}
 definitions.push(`${JSON.stringify(lang.code)}: { ${variables.map(name=>'...'+name).join(', ')} }`);
}
lines.push(`export type Language = ${registry.languages.filter(item=>item.enabled!==false).map(item=>JSON.stringify(item.code)).join(' | ')};`);
lines.push('export const LANGUAGES = registry.languages.filter(item => item.enabled).map(item => ({ ...item, code: item.code as Language }));');
lines.push('export const DEFAULT_LANGUAGE = registry.default_language as Language;');
lines.push(`export const messages: Record<string, Record<string, string>> = {${definitions.join(',')}};`);
lines.push('export const legacyKeys: Record<string, string> = aliases;');
lines.push('export const templateUiSlots: Record<string, number[]> = slots;');
lines.push('export const legacyEnglishCatalog: Record<string, string> = Object.fromEntries(Object.entries(aliases).map(([source,key]) => [source,messages.en[key] || source]));');
fs.writeFileSync(path.join(root,'apps/web/app/i18n/resources.ts'),lines.join('\n')+'\n','utf8');
console.log(JSON.stringify({languages:registry.languages.map(item=>item.code),generated:'apps/web/app/i18n/resources.ts'}));
