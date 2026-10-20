"use client";
import { createContext, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { DEFAULT_LANGUAGE, LANGUAGES, formatUi, getLanguage, getOutputLanguagePreference, setLanguage, setOutputLanguage, subscribeLanguage, translateUi, type Language, type OutputLanguagePreference } from "./language";
import "./styles.css";
type I18n = { language: Language; outputLanguage: Language; outputPreference: OutputLanguagePreference; t: <T>(value: T) => T; tf: (key: string, values: readonly unknown[]) => string };
const I18nContext = createContext<I18n | null>(null);
const serverLanguage = (): Language => DEFAULT_LANGUAGE;
const serverOutput = (): OutputLanguagePreference => "follow-ui";
export function I18nProvider({ children }: { children: ReactNode }) {
  const language = useSyncExternalStore(subscribeLanguage, getLanguage, serverLanguage);
  const outputPreference = useSyncExternalStore(subscribeLanguage, getOutputLanguagePreference, serverOutput);
  const outputLanguage = outputPreference === "follow-ui" ? language : outputPreference;
  const [storageWarning, setStorageWarning] = useState(false);
  const value = useMemo<I18n>(() => ({ language, outputLanguage, outputPreference, t: <T,>(text: T): T => translateUi(text, language), tf: (key, values) => formatUi(key, values, language) }), [language, outputLanguage, outputPreference]);
  useEffect(() => {
    // Metadata only. Visible strings are translated through React, not DOM scanning.
    document.documentElement.lang = language;
    document.documentElement.dir = LANGUAGES.find(item => item.code === language)?.direction || "ltr";
    document.title = value.t("数学出题与学情诊断平台");
  }, [language, value]);
  const change = (next: Language) => setStorageWarning(!setLanguage(next));
  return (
    <I18nContext.Provider value={value}>
      <div className="i18nBar">
        <div className="i18nControl" role="group" aria-label={value.t("common.language.interface")}>
          <span className="i18nLabel">{value.t("common.language.interface")}</span>
          {LANGUAGES.map(item => <button key={item.code} type="button" lang={item.code} aria-pressed={language === item.code} onClick={() => change(item.code)}>{item.native_name}</button>)}
        </div>
        <label className="i18nControl" title={value.t("common.language.scope")}>
          <span className="i18nLabel">{value.t("common.language.output")}</span>
          <select value={outputPreference} onChange={event => setStorageWarning(!setOutputLanguage(event.target.value as OutputLanguagePreference))}>
            <option value="follow-ui">{value.t("common.language.follow_ui")}</option>
            {LANGUAGES.map(item => <option key={item.code} value={item.code}>{item.native_name}</option>)}
          </select>
        </label>
        {storageWarning && <span role="status" className="i18nStorageWarning">{value.t("common.language.storage_warning")}</span>}
      </div>
      {children}
    </I18nContext.Provider>
  );
}
export function useI18n(): I18n {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useI18n requires I18nProvider");
  return context;
}
