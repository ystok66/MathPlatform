import { DEFAULT_LANGUAGE, LANGUAGES, legacyKeys, messages, templateUiSlots, type Language } from "./resources";
export { DEFAULT_LANGUAGE, LANGUAGES } from "./resources";
export type { Language } from "./resources";
export type OutputLanguagePreference = Language | "follow-ui";
export const LANGUAGE_STORAGE_KEY = "math-platform-language-v1";
export const OUTPUT_LANGUAGE_STORAGE_KEY = "math-platform-output-language-v1";
export const LANGUAGE_HEADER = "X-Math-Platform-Language";
export const OUTPUT_LANGUAGE_HEADER = "X-Math-Platform-Output-Language";
let currentLanguage: Language | undefined;
let currentOutput: OutputLanguagePreference | undefined;
const listeners = new Set<() => void>();
const missingKeys = new Set<string>();

export function normalizeLanguage(value: unknown): Language {
  return typeof value === "string" ? LANGUAGES.find(item => item.code.toLowerCase() === value.trim().toLowerCase())?.code ?? DEFAULT_LANGUAGE : DEFAULT_LANGUAGE;
}
function normalizeOutput(value: unknown): OutputLanguagePreference {
  return typeof value === "string" && LANGUAGES.some(item => item.code === value) ? value as Language : "follow-ui";
}
export function getLanguage(): Language {
  if (typeof window === "undefined") return DEFAULT_LANGUAGE;
  if (!currentLanguage) {
    try { currentLanguage = normalizeLanguage(window.localStorage.getItem(LANGUAGE_STORAGE_KEY)); }
    catch { currentLanguage = DEFAULT_LANGUAGE; }
  }
  return currentLanguage;
}
export function getOutputLanguagePreference(): OutputLanguagePreference {
  if (typeof window === "undefined") return "follow-ui";
  if (!currentOutput) {
    try { currentOutput = normalizeOutput(window.localStorage.getItem(OUTPUT_LANGUAGE_STORAGE_KEY)); }
    catch { currentOutput = "follow-ui"; }
  }
  return currentOutput;
}
export function getOutputLanguage(): Language {
  const preference = getOutputLanguagePreference();
  return preference === "follow-ui" ? getLanguage() : preference;
}
function savePreference(key: string, value: string): boolean {
  let saved = true;
  try { window.localStorage.setItem(key, value); } catch { saved = false; }
  listeners.forEach(listener => listener());
  return saved;
}
export function setLanguage(value: Language): boolean {
  currentLanguage = normalizeLanguage(value);
  return savePreference(LANGUAGE_STORAGE_KEY, currentLanguage);
}
export function setOutputLanguage(value: OutputLanguagePreference): boolean {
  currentOutput = normalizeOutput(value);
  return savePreference(OUTPUT_LANGUAGE_STORAGE_KEY, currentOutput);
}
export function subscribeLanguage(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (![LANGUAGE_STORAGE_KEY, OUTPUT_LANGUAGE_STORAGE_KEY, null].includes(event.key)) return;
    currentLanguage = undefined; currentOutput = undefined; listener();
  };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(listener); window.removeEventListener("storage", onStorage); };
}
export function languageHeaders(headers?: HeadersInit): Headers {
  const result = new Headers(headers);
  result.set(LANGUAGE_HEADER, getLanguage());
  if (!result.has(OUTPUT_LANGUAGE_HEADER)) result.set(OUTPUT_LANGUAGE_HEADER, getOutputLanguage());
  return result;
}
export function languageFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const inherited = input instanceof Request ? input.headers : undefined;
  return fetch(input, { ...init, headers: languageHeaders(init?.headers ?? inherited) });
}
function lookup(value: string, language: Language): string | undefined {
  const key = legacyKeys[value] || value;
  const translated = messages[language]?.[key];
  if (translated !== undefined) return translated;
  const fallback = messages[DEFAULT_LANGUAGE]?.[key];
  if (fallback !== undefined && !missingKeys.has(language + ":" + key)) {
    missingKeys.add(language + ":" + key);
    console.warn("Missing UI translation", { language, key });
  }
  return fallback;
}
export function formatUi(key: string, values: readonly unknown[], language: Language): string {
  const template = lookup(key, language) ?? key;
  return template.replace(/\{(\d+)\}/g, (_, index: string) => String(values[Number(index)] ?? ""));
}
const escapePattern = (value: string) => value.replace(/[{}.*+?^$()|[\]\\]/g, "\\$&");
const patterns = Object.entries(legacyKeys).filter(([source]) => /\{\d+\}/.test(source)).flatMap(([source, key]) => {
  const templates = new Set([source, messages.en[key]]);
  return [...templates].filter((template): template is string => typeof template === "string").map(template => {
    const slots: number[] = [];
    const expression = template.split(/(\{\d+\})/).map(part => {
      if (/^\{\d+\}$/.test(part)) { slots.push(Number(part.slice(1,-1))); return "([\\s\\S]*?)"; }
      return escapePattern(part);
    }).join("");
    return {source,key,slots,pattern:new RegExp("^" + expression + "$"),weight:template.replace(/\{\d+\}/g, "").length};
  });
}).sort((a,b) => b.weight-a.weight);
const cache = new Map<string,string>();
const countOnly = new Set(["清单缺失 {0}","清单不可读 {0}","版本不符 {0}","缺文件 {0}","文件变化 {0}","额外文件 {0}","组件漂移 {0}","依赖漂移 {0}"]);
function translateString(value: string, language: Language, depth = 0): string {
  const direct = lookup(value,language);
  if (direct !== undefined) return direct;
  if (depth > 5 || value.length > 20000) return value;
  const normalized=value.normalize("NFKC");
  const normalizedMatch=normalized!==value ? lookup(normalized,language) : undefined;
  if (normalizedMatch!==undefined) return normalizedMatch;
  const tag=/^([PSMTC]:)\s*(.+)$/.exec(value);
  if (tag && lookup(tag[2],language)!==undefined) return `${tag[1]} ${lookup(tag[2],language)}`;
  for (const entry of patterns) {
    const match=entry.pattern.exec(value);
    if (!match || (countOnly.has(entry.source) && !/^\d+$/.test(match[1]))) continue;
    const values: string[]=[];
    entry.slots.forEach((slot,index) => { values[slot]=templateUiSlots[entry.source]?.includes(slot) ? translateString(match[index+1],language,depth+1) : match[index+1]; });
    return formatUi(entry.key,values,language);
  }
  const trimmed=value.trim();
  const trimmedMatch=trimmed!==value ? lookup(trimmed,language) : undefined;
  if (trimmedMatch!==undefined) return value.replace(trimmed,trimmedMatch);
  const parts=value.split(/( · |；|、|\n| \/ |，)/);
  if (parts.length>1) {
    const translated=parts.map((part,index)=> index%2 ? part : translateString(part,language,depth+1));
    if (translated.some((part,index)=>index%2===0 && part!==parts[index])) {
      return translated.map((part,index)=> index%2 && ["、","，"].includes(part) ? lookup("common.separator.list",language) : index%2 && part==="；" ? lookup("common.separator.sections",language) : part).join("");
    }
  }
  return value;
}
export function translateUi<T>(value: T, language: Language): T {
  if (typeof value !== "string") return value;
  const cacheKey=language+"\0"+value;
  let translated=cache.get(cacheKey);
  if (translated===undefined) {
    translated=translateString(value,language);
    if (cache.size>4096) cache.clear();
    cache.set(cacheKey,translated);
  }
  return translated as T;
}
export function localizeUi<T>(value: T): T { return translateUi(value,getLanguage()); }
export function formatNumber(value: number, language: Language = getLanguage()): string { return new Intl.NumberFormat(language).format(value); }
